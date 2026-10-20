"""Оркестратор планирования.

Этапы:
  1. Подготовка: локальная СК, ЦМР, активные запретные зоны, препятствия, воздушное пространство.
  2. Геометрия съемки для каждого типа БВС (высота по GSD, шаг, скорость; отсев по ветру/облачности).
  3. Эффективная область = область съемки ∩ разрешенное пространство − запретные зоны (с буфером типа)
     − высотные препятствия, выше рабочей высоты.
  4. Компоненты, курс галсов, сетка ячеек.
  5. Поиск: распределение площади между типами БВС (доли), ширина колонок нарезки; для каждого
     варианта — галсы → упаковка в вылеты → расписание → критерий (время или налет).
  6. Лучший вариант: точные 4D-траектории, ремонт по энергии, разведение, покрытие, валидация.
"""
from __future__ import annotations

import itertools
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import numpy as np
import shapely
from shapely.geometry import LineString, Point, box

from . import export as _export
from .catalog import load_catalog
from .coverage import generate_pieces, make_cells, make_components, rotate_geom, wind_vector, rot_fwd
from .dispatch import DroneState, SchedulingError, dispatch, schedule_estimate
from .environment import daylight_windows
from .geo import LocalFrame, Scenario, Site  # noqa: F401
from .models import MissionParams
from .routing import Router, RoutingError
from .sensors import compute_survey_geom
from .sites import infeasible_recommendations, suggest_sites, vlos_sites
from .sortie import ModelCtx, fixed_overhead, order_nearest, pack
from .terrain import Terrain, TerrainXY


def _union(geoms):
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return shapely.Polygon()
    return shapely.union_all(geoms)


def _iso(ts: float, tz) -> str:
    return datetime.fromtimestamp(ts, tz).isoformat(timespec="seconds")


def _share_grid(k: int, step: float):
    n = int(round(1 / step))
    out = []
    for c in itertools.product(range(n + 1), repeat=k):
        if sum(c) == n:
            out.append(tuple(x / n for x in c))
    return out


class Planner:
    def __init__(self, scenario: Scenario, params: MissionParams, progress: Optional[Callable] = None):
        self.sc = scenario
        self.p = params
        self.cat = load_catalog()
        self.glob = self.cat.glob
        self.progress = progress or (lambda f, m: None)
        self.warnings: List[str] = []
        self.recs: List[dict] = []
        self.search_log: List[dict] = []
        self.balance_reference = None

    # ------------------------------------------------------------------ подготовка
    def prepare(self):
        sc, p, glob = self.sc, self.p, self.glob
        for es in p.extra_sites:
            if any(s.name == es.name for s in sc.sites):
                raise PlanningError(f"Имя площадки «{es.name}» повторяется — задайте уникальные имена")
            sc.sites.append(Site(name=es.name, lon=es.lon, lat=es.lat))
        lon0, lat0 = sc.centroid()
        self.frame = fr = LocalFrame(lon0, lat0)
        self.lonlat0 = (lon0, lat0)
        self.progress(0.02, "Загрузка рельефа")
        wb = sc.work_bounds() or sc.bounds()  # рельеф нужен в районе работ; зоны региональных файлов его не расширяют
        self.terrain_raw = Terrain(wb, enabled=p.use_terrain, margin_deg=0.06)
        self.progress(0.025, "Подготовка геометрии задания")
        if self.terrain_raw.warning:
            self.warnings.append(self.terrain_raw.warning)
        self.terrain = TerrainXY(self.terrain_raw, fr)
        self.tz = p.start_time.tzinfo
        self.t0 = p.start_time.timestamp()

        self.requested = _union([fr.geom_to_xy(g) for _, g in sc.areas])
        self.ceiling = glob["corridor_max_agl_m"]
        if sc.airspace_ceiling_m:
            self.ceiling = min(self.ceiling, sc.airspace_ceiling_m)
        self.no_plan = p.airspace_mode == "no_flight_plan"
        if p.cloud_base_m is not None:
            self.ceiling = min(self.ceiling, p.cloud_base_m - 50.0)
        if self.no_plan:
            # ФП ИВП (Пост. №138): без плана полета — ниже 150 м от земли; 5 м запаса на огибание рельефа по ЦМР
            self.ceiling = min(self.ceiling, 145.0)
            self.warnings.append(
                "Режим без плана полета (ФП ИВП, Пост. №138): потолок 150 м от земли, прямая видимость (≤ "
                f"{p.vlos_radius_km:g} км от площадки), светлое время. Требования, которые сервис не проверяет по данным: "
                "вне диспетчерских зон аэродромов, мест массовых мероприятий и полетов над населенными пунктами — "
                "их нужно задать слоем запретных зон. Сверьте актуальную редакцию правил и региональные ограничения")
        zones, temp_unknown = [], []
        self.zone_floor = {}  # id(зоны) → нижняя граница над землей, м (None — от земли)
        for z in sc.nofly:
            if z.active_to is not None and z.active_to <= p.start_time:
                self.warnings.append(f"Зона «{z.name}» неактивна в период работ — не учитывается")
                continue
            if z.time_unknown:
                if p.temp_restrictions == "ignore":
                    self.warnings.append(f"Временное ограничение «{z.name}» без времени действия — не учитывается (выбор пользователя)")
                    continue
                temp_unknown.append(z.name)
            lo = self._zone_floor_agl(z)
            self.zone_floor[id(z)] = lo
            if lo is not None and lo >= self.ceiling:
                self.warnings.append(f"Зона «{z.name}» выше коридора полетов (нижняя граница ≈ {lo:.0f} м над землей) — не влияет")
                continue
            zones.append(z)
        if temp_unknown:
            self.warnings.append(f"Временные ограничения без времени действия ({len(temp_unknown)}: {', '.join(temp_unknown[:6])}"
                                 f"{'…' if len(temp_unknown) > 6 else ''}) считаются действующими весь период работ. Если их действие "
                                 "проверено по NOTAM — выберите «не учитывать» или задайте active_from/active_to")
        self.zones = zones
        if not sc.main_sites:
            self._auto_sites(zones)
        self.obstacles = []
        self.obst_top_amsl = []  # абсолютная отметка верха (для высот перелетов под низким потолком)
        for index, o in enumerate(sc.obstacles):
            if index % 100 == 0:
                self.progress(0.03, "Подготовка высотных препятствий")
            g = fr.geom_to_xy(o.geom)
            if o.radius_m:
                g = g.buffer(o.radius_m)
            h = o.height_m
            if o.alt_ref == "AMSL":
                # абсолютная отметка верха (eTOD) → высота над землей по ЦМР; берем минимум рельефа под объектом
                # (консервативно). Без ЦМР отметка трактуется как высота над землей — это тоже консервативно.
                if self.terrain.enabled:
                    pts = np.vstack([np.asarray(g.exterior.coords) if g.geom_type == "Polygon" else np.asarray(g.envelope.exterior.coords),
                                     [[g.centroid.x, g.centroid.y]]])
                    h = max(0.0, o.height_m - float(np.min(self.terrain.h(pts[:, 0], pts[:, 1]))))
            self.obstacles.append((o.name, g, h))
            if o.alt_ref == "AMSL" and self.terrain.enabled:
                self.obst_top_amsl.append(o.height_m)
            else:
                pts = np.asarray(g.exterior.coords) if g.geom_type == "Polygon" else np.asarray(g.envelope.exterior.coords)
                self.obst_top_amsl.append(h + float(np.max(self.terrain.h(pts[:, 0], pts[:, 1]))))
        if sc.airspace is not None:
            air = fr.geom_to_xy(sc.airspace)
            minx, miny, maxx, maxy = self.requested.union(air).bounds
            self.outside_air = box(minx - 30000, miny - 30000, maxx + 30000, maxy + 30000).difference(air)
        else:
            self.outside_air = shapely.Polygon()
        main = sc.main_sites
        # взлет вертикальный: с площадки внутри постоянной запретной зоны нельзя подняться, не нарушив ее
        blocked = [s for s in main if any(z.active_from is None and z.active_to is None
                                          and fr.geom_to_xy(z.geom).contains(Point(*fr.to_xy(s.lon, s.lat))) for z in zones)]
        if blocked and len(blocked) < len(main):
            main = [s for s in main if s not in blocked]
            for s in blocked:
                self.warnings.append(f"Площадка «{s.name}» внутри запретной зоны — не используется")
        elif blocked:
            self.warnings.append("Все площадки внутри запретных зон — взлет нарушит зону; добавьте площадку вне зон")
        self.site_names = [s.name for s in main]
        self.sites_xy = np.array([fr.to_xy(s.lon, s.lat) for s in main], dtype=float)
        self.reserve_xy = np.array([fr.to_xy(s.lon, s.lat) for s in sc.reserve_sites], dtype=float).reshape(-1, 2)

    def _zone_floor_agl(self, z):
        """Нижняя граница зоны над землей. Для AMSL (эшелоны, «от 800 м AMSL») — по максимуму рельефа под зоной
        в районе работ (консервативно: зона считается ниже, чем в среднем)."""
        if z.alt_min_m is None:
            return None
        if z.alt_ref != "AMSL" or not self.terrain.enabled:
            return z.alt_min_m
        from .sites import sample_area
        g = self.frame.geom_to_xy(z.geom)
        minx, miny, maxx, maxy = self.requested.bounds
        g = g.intersection(box(minx - 5000, miny - 5000, maxx + 5000, maxy + 5000))
        if g.is_empty or g.area <= 0:
            return z.alt_min_m
        pts = sample_area(g, step=500.0, max_pts=3000)
        return z.alt_min_m - float(np.max(self.terrain.h(pts[:, 0], pts[:, 1])))

    def _auto_sites(self, zones):
        """В задании нет площадок — расставляем: круги досягаемости самого «короткого» типа в парке покрывают область."""
        from .sites import auto_sites
        cat, fr = self.cat, self.frame
        used = [cat.drones[f.model] for f in self.p.fleet if f.count > 0 and f.model in cat.drones]
        cop = [m for m in used if not m.is_plane]
        if cop:
            R = min(0.22 * m.endurance_min * 60 * 0.8 * m.cruise_ms for m in cop)
        else:
            R = min(25000.0, min(0.22 * m.endurance_min * 60 * 0.8 * m.cruise_ms for m in used)) if used else 5000.0
        if self.p.airspace_mode == "no_flight_plan":
            R = min(R, self.p.vlos_radius_km * 1000 * 0.95)
        forb = _union([fr.geom_to_xy(z.geom) for z in zones if z.active_from is None and z.active_to is None])
        ll = auto_sites(self.requested, forb, R, fr)
        for i, (lon, lat) in enumerate(ll):
            self.sc.sites.append(Site(name=f"Авто-площадка {i + 1}", lon=float(lon), lat=float(lat)))
        self.auto_site_names = {f"Авто-площадка {i + 1}" for i in range(len(ll))}
        disks = _union([Point(*fr.to_xy(lon, lat)).buffer(R) for lon, lat in ll])
        coverage = 100 * self.requested.intersection(disks).area / max(self.requested.area, 1)
        self.warnings.append(f"Площадки в задании не заданы — расставлено автоматически: {len(ll)} (круги досягаемости "
                             f"≈ {R / 1000:.1f} км покрывают {coverage:.1f}% запрошенной площади; "
                             "положение уточнено k-медианами вне запретных зон). "
                             "Проверьте подъезд и пригодность площадок на местности")

    def survey_geoms(self):
        p, cat = self.p, self.cat
        self.models, self.geoms, self.excluded = {}, {}, {}
        for f in p.fleet:
            if f.count <= 0 or f.model in self.models:
                continue
            m = cat.drones[f.model]
            if self.no_plan and m.is_plane:
                self.excluded[f.model] = [f"{m.title}: самолет летает за пределами прямой видимости — для него нужен план "
                                          "полета; в режиме без плана полета не используется"]
                continue
            # без плана полета над съемкой оставляем слой для перелетов (вертикальное разведение)
            survey_cap = self.ceiling - self.glob["vertical_sep_m"] if self.no_plan else self.ceiling
            # Leave room for interpolation between DEM samples and exported
            # waypoint rounding. A nominal height at the ceiling is unsafe.
            survey_cap = min(survey_cap, m.max_alt_agl_m) - 8.0
            g, reasons = compute_survey_geom(cat, m, p.survey_type, p.gsd_cm, p.front_overlap, p.side_overlap,
                                             p.wind_speed_ms, survey_cap, p.cloud_base_m, wind_model=p.wind_model)
            if g is None:
                self.excluded[f.model] = reasons
            else:
                self.models[f.model] = m
                self.geoms[f.model] = g
        for k, rs in self.excluded.items():
            for r in rs:
                self.warnings.append(f"Исключен из плана: {r}")

    def build_contexts(self):
        glob = self.glob
        # ветер растет с высотой: тип, для которого ветер на нижнем эшелоне перелета превышает ТТХ, исключается
        alpha = glob.get("wind_shear_alpha", 0.143)
        while self.p.wind_speed_ms > 0 and len(self.models) > 0:
            hmax = max(g.altitude_agl for g in self.geoms.values())
            base = math.ceil((hmax + glob["vertical_sep_m"]) / 10.0) * 10.0
            worst = None
            for key, m in self.models.items():
                h_w = 10.0 * (m.max_wind_ms / self.p.wind_speed_ms) ** (1 / alpha)
                if h_w - 30 < base and (worst is None or h_w < worst[1]):
                    worst = (key, h_w)
            if worst is None:
                break
            key, h_w = worst
            m = self.models.pop(key)
            self.geoms.pop(key)
            msg = (f"{m.title}: ветер {self.p.wind_speed_ms:.1f} м/с у земли превысит {m.max_wind_ms:.0f} м/с на высоте ~{h_w:.0f} м — "
                   f"ниже эшелона перелета {base:.0f} м с запасом 30 м; тип исключен из плана")
            self.excluded[key] = [msg]
            self.warnings.append(f"Исключен из плана: {msg}")
        if not self.models:
            why = "; ".join(v[0] for v in self.excluded.values())
            raise PlanningError(f"Ни один тип БВС из флота не может безопасно выполнить работы: {why}")
        self.Hmax = max(g.altitude_agl for g in self.geoms.values())
        vsep, step = glob["vertical_sep_m"], glob["layer_step_m"]
        # 3D/4D-классификация запретных зон
        fr = self.frame
        horiz, self.temp_zones = [], []
        for z in self.zones:
            g = fr.geom_to_xy(z.geom)
            lo = self.zone_floor.get(id(z), z.alt_min_m if z.alt_ref == "AGL" else None)
            if lo is not None and lo - vsep >= self.Hmax + vsep:
                # зона выше рабочих высот: не блокирует съемку, ограничивает эшелоны перелета сверху
                self.ceiling = min(self.ceiling, lo - vsep)
                self.warnings.append(f"Зона «{z.name}» (нижняя граница ≈ {lo:.0f} м над землей) выше рабочих высот: "
                                     f"съемка под ней разрешена, эшелоны перелета ограничены {self.ceiling:.0f} м")
                continue
            if (z.active_from or z.active_to) and not (z.active_to is None and z.active_from <= self.p.start_time):
                a = z.active_from.timestamp() if z.active_from else -1e18
                b = z.active_to.timestamp() if z.active_to else 1e18
                self.temp_zones.append((g, a, b, z.name))
                self.warnings.append(f"Временная зона «{z.name}»: вылеты, чья траектория ее задевает, планируются вне окна действия")
                continue
            horiz.append(g)
        self.nofly = _union(horiz)
        # ветер растет с высотой: эшелоны перелета не выше высоты, где ветер достигает предела ТТХ
        alpha = glob.get("wind_shear_alpha", 0.143)
        if self.p.wind_speed_ms > 0:
            for key, m in list(self.models.items()):
                h_w = 10.0 * (m.max_wind_ms / self.p.wind_speed_ms) ** (1 / alpha) - 30  # запас на неровности рельефа
                if h_w < self.ceiling:
                    self.ceiling = max(h_w, self.Hmax + vsep)
                    self.warnings.append(f"Ветер: выше {h_w:.0f} м он превысит допустимые для {m.title} {m.max_wind_ms:.0f} м/с — "
                                         f"эшелоны перелета ограничены {self.ceiling:.0f} м")
        self.temp_union = _union([t[0] for t in self.temp_zones])
        self.common = self.requested.difference(self.nofly).difference(self.outside_air)
        self.base_layer = math.ceil((self.Hmax + vsep) / 10.0) * 10.0
        if self.base_layer > self.ceiling:
            self.base_layer = self.ceiling
            if self.base_layer - self.Hmax < vsep - 1e-6:
                self.warnings.append("Нет места для эшелонов перелета выше съемки — перелеты на высоте съемки, разведение по времени")
        self.n_layers = max(1, int((self.ceiling - self.base_layer) // step) + 1)
        # борта
        self.drone_specs = []
        k = 0
        fleet_sorted = sorted(self.p.fleet, key=lambda f: self.cat.drones[f.model].is_plane)  # коптерам — нижние эшелоны
        for f in fleet_sorted:
            if f.model not in self.models:
                continue
            m = self.models[f.model]
            for i in range(f.count):
                layer = min(self.base_layer + (k % self.n_layers) * step, m.max_alt_agl_m - 5)
                short = {"geoscan_201": "G201", "geoscan_gemini": "GEM", "geoscan_801": "G801"}.get(f.model, f.model)
                self.drone_specs.append(dict(id=f"{short}-{i + 1}", model=f.model, title=m.title,
                                             layer=layer, batteries=f.batteries or m.batteries,
                                             charge_s=m.charge_min * 60, turnaround_s=m.turnaround_min * 60))
                k += 1
        if k > self.n_layers:
            self.warnings.append(f"Бортов ({k}) больше, чем эшелонов в коридоре ({self.n_layers}) — часть эшелонов общая, разведение по времени")
        n_by_model = {}
        for d in self.drone_specs:
            n_by_model[d["model"]] = n_by_model.get(d["model"], 0) + 1
        self.n_by_model = n_by_model
        self.layer_est = {m: max(d["layer"] for d in self.drone_specs if d["model"] == m) for m in self.models}

        self.ctxs: Dict[str, ModelCtx] = {}
        self.eff: Dict[str, object] = {}
        self.forb_survey: Dict[str, object] = {}
        vcl, hbuf = glob["obstacle_v_clearance_m"], glob["obstacle_h_buffer_m"]
        reserve = self.p.reserve_pct / 100.0
        # на высоте съемки важны препятствия у самой области (развороты самолета — в пределах ~3 км)
        near_req = self.requested.buffer(3000)
        shapely.prepare(near_req)
        for key, m in self.models.items():
            g = self.geoms[key]
            vcl = m.obstacle_v_clearance_m or glob["obstacle_v_clearance_m"]
            buf = m.nofly_buffer_m
            obs_s = _union([og.buffer(max(hbuf, buf if m.is_plane else hbuf)) for _, og, h in self.obstacles
                            if h + vcl > g.altitude_agl and near_req.intersects(og)])
            obs_t = _union([og.buffer(max(hbuf, buf if m.is_plane else hbuf))
                            for _, og, h in self.obstacles if h + vcl > self.base_layer])
            fs = _union([self.nofly.buffer(buf) if not self.nofly.is_empty else None, obs_s,
                         self.outside_air.buffer(buf) if not self.outside_air.is_empty else None])
            # временные зоны в маршрутизатор не входят: вылет, чья траектория их задевает, ставится вне окна действия
            ft = _union([self.nofly.buffer(buf * 0.5) if not self.nofly.is_empty else None, obs_t,
                         self.outside_air.buffer(buf * 0.5) if not self.outside_air.is_empty else None])
            self.forb_survey[key] = fs
            self.eff[key] = self.common.difference(fs)
            budget_s = m.endurance_min * 60 * (1 - reserve)
            budget_m = (m.max_route_km * 1000 * (1 - reserve)) if m.max_route_km else math.inf
            from .environment import wind_at
            wt = wind_at(self.layer_est[key], self.p.wind_speed_ms, glob.get("wind_shear_alpha", 0.143))
            self.ctxs[key] = ModelCtx(key=key, model=m, geom=g, sites_xy=self.sites_xy, budget_s=budget_s,
                                      budget_m=budget_m, router_transit=Router(ft), router_survey=Router(fs),
                                      terrain=self.terrain, terrain_step=glob["terrain_step_m"],
                                      # методика QA: ветер не входит ни в путевую скорость, ни в расход АКБ
                                      wind_t=(0.0, 0.0) if self.p.wind_model == "qa" else wind_vector(wt, self.p.wind_dir_deg),
                                      est_margin=0.99 if m.is_plane else 0.97, agl_cap=min(self.ceiling, m.max_alt_agl_m),
                                      temp_zones=[(g, a, b) for g, a, b, _ in self.temp_zones],
                                      energy_mode=self.p.energy_model)
        if self.obstacles:
            # Check actual 3D segments against every obstacle, including the
            # spiral exit and segments lowered to remain under an AGL ceiling.
            for c in self.ctxs.values():
                vcl = c.model.obstacle_v_clearance_m or glob["obstacle_v_clearance_m"]
                zs = [obstacle[1].buffer(hbuf) for obstacle in self.obstacles]
                c.obst_tops = (shapely.STRtree(zs), np.asarray(self.obst_top_amsl) + vcl)
        self.coverable = _union(list(self.eff.values()))
        wind = self.p.wind_speed_ms
        self.comps = make_components(self.coverable, self.geoms, self.models, wind, self.p.wind_dir_deg)
        if not self.comps:
            raise PlanningError("После исключения запретных зон и препятствий не осталось области для съемки")
        for key, ctx in self.ctxs.items():
            g = self.geoms[key]
            k = g.wind_ms / max(self.p.wind_speed_ms, 1e-6) if self.p.wind_speed_ms > 0 else 0.0
            for c in self.comps:
                v = g.survey_speed(c.w_par * k, c.w_perp * k)
                ctx.vs_comp[c.idx] = v
            if self.p.energy_model == "calibrated" and not ctx.model.is_plane:
                # упаковка считает бюджет по времени; в калиброванной модели горизонтальный полет дешевле/дороже висения —
                # масштабируем бюджет упаковки на f горизонтального полета (итог все равно проверяется по точной энергии)
                from .energy import load_table
                tbl = load_table()
                if tbl is not None:
                    f_lvl = float(tbl(g.speed_ms, 0.0, 0.0)[0]) / tbl.f_hover
                    ctx.est_margin = ctx.est_margin / max(f_lvl, 0.8)
        n = max(1, len(self.drone_specs))
        G = min(1500.0, max(300.0, math.sqrt(self.coverable.area / (n * 12))))
        self.cell_m = G
        self.cells = make_cells(self.comps, G, self.sites_xy)
        self._cells_cache = {1.0: (G, self.cells)}
        # радиус действия без съемки (туда-обратно) — для распределения ячеек
        self.radius = {}
        for key, ctx in self.ctxs.items():
            ft_, _ = fixed_overhead(ctx, self.layer_est[key])
            vmin_t = min(ctx.geom.transit_speed(-np.hypot(*ctx.wind_t), 0.0), ctx.vt)
            vmax_t = ctx.geom.transit_speed(np.hypot(*ctx.wind_t), 0.0)
            r = max(0.0, (ctx.budget_s - ft_) / (1 / vmin_t + 1 / vmax_t))
            if ctx.budget_m < math.inf:
                r = min(r, ctx.budget_m / 2)
            if self.no_plan:
                ctx.strict_ceiling = True
                ctx.max_site_dist = self.p.vlos_radius_km * 1000
                r = min(r, ctx.max_site_dist)
            self.radius[key] = r
        # кэш повернутых геометрий
        self.eff_rot = {(k, c.idx): rotate_geom(self.eff[k].intersection(c.geom.buffer(1.0)), c.theta)
                        for k in self.models for c in self.comps}
        self.forb_rot = {(k, c.idx): rotate_geom(self.forb_survey[k], c.theta) if not self.forb_survey[k].is_empty else None
                         for k in self.models for c in self.comps}
        fallback = sorted(self.models, key=lambda k: self.models[k].nofly_buffer_m)
        self.fallback = {k: next((o for o in fallback if o != k and self.models[o].nofly_buffer_m < self.models[k].nofly_buffer_m), None)
                         for k in self.models}
        self.windows = None
        if self.p.respect_daylight or self.no_plan:
            lon0, lat0 = self.lonlat0
            self.windows = daylight_windows(lat0, lon0, self.p.start_time, 60, self.p.min_sun_elevation_deg)
            if not self.windows:
                raise PlanningError("Высота Солнца не достигает порога в горизонте 60 дней — выберите другую дату или порог съемки")
            else:
                h = (self.windows[0][1] - self.windows[0][0]) / 3600
                self.warnings.append(f"Окно съемки в первый день: {_iso(self.windows[0][0], self.tz)[11:16]}–{_iso(self.windows[0][1], self.tz)[11:16]} ({h:.1f} ч, Солнце ≥ {self.p.min_sun_elevation_deg:.0f}°)")

    # ------------------------------------------------------------------ поиск
    def assign_cells(self, shares: Dict[str, float]) -> Dict[str, List[int]]:
        order = sorted(self.models, key=lambda k: -self.radius[k])
        total = sum(c.area for c in self.cells)
        quota = {k: shares.get(k, 0) * total for k in order}
        out = {k: [] for k in order}
        idx = sorted(range(len(self.cells)), key=lambda i: -self.cells[i].dist_site)
        for i in idx:
            c = self.cells[i]
            eligible = [k for k in order if self.ctxs[k].vs_of(c.comp) > 0]
            if not eligible:
                raise PlanningError("Курс галсов несовместим с ветром или частотой камеры; уменьшите перекрытие, ветер или измените GSD")
            reach = [k for k in eligible if self.radius[k] >= c.dist_site]
            cand = [k for k in reach if quota[k] > 0] or [k for k in eligible if quota[k] > 0 and not reach] or reach or eligible[:1]
            if not cand:
                cand = order[:1]
            k = cand[0]
            out[k].append(i)
            quota[k] -= c.area
        return out

    def pieces_for(self, key: str, assign: Dict[str, List[int]], col_m: float):
        """Галсы типа key: (основные — в своих ячейках, добор — кромки у запретных зон в чужих ячейках)."""
        g, m = self.geoms[key], self.models[key]
        ctx = self.ctxs[key]
        margin = max(0.0, g.spacing() - g.swath_eff() / 2)
        ext = (g.along_m / 2 if g.kind == "camera" else 20.0)
        pieces, extra = [], []
        rc = 0
        for comp in self.comps:
            if ctx.vs_of(comp.idx) <= 0:
                continue
            own = [self.cells[i] for i in assign.get(key, []) if self.cells[i].comp == comp.idx]
            reg_parts = []
            main_reg = None
            if own:
                bx = _union([box(b.box_rot[0], b.box_rot[1] - margin, b.box_rot[2], b.box_rot[3] + margin) for b in own])
                main_reg = bx.intersection(self.eff_rot[(key, comp.idx)])
            # остатки ячеек других типов, которые те не могут снять (буфер маневра у запретных зон)
            for o, cells_o in assign.items():
                if o == key or self.fallback.get(o) != key:
                    continue
                oc = [self.cells[i] for i in cells_o if self.cells[i].comp == comp.idx]
                if not oc:
                    continue
                bo = _union([box(*b.box_rot) for b in oc])
                resid = bo.intersection(self.eff_rot[(key, comp.idx)]).difference(self.eff_rot[(o, comp.idx)])
                resid = resid.buffer(-8).buffer(8)  # отбрасываем «щепки» вдоль общих границ — они и так покрыты полосой кадра
                if resid.area > 5000:
                    reg_parts.append(resid)
            crab = self._crab(comp, key)
            if main_reg is not None and main_reg.area >= 1:
                pieces.extend(generate_pieces(key, g, comp, main_reg, self.forb_rot[(key, comp.idx)], crab, col_m, ext, self.sites_xy, rc))
                rc += 100000
            if reg_parts:
                region = _union(reg_parts)
                if main_reg is not None:
                    region = region.difference(main_reg)
                if region.area >= 100:
                    extra.extend(generate_pieces(key, g, comp, region, self.forb_rot[(key, comp.idx)], crab, col_m, ext, self.sites_xy, rc,
                                                 optional=True))
                    rc += 100000
        return pieces, extra

    def _tag_blocked(self, key, sorties):
        if not self.temp_zones:
            return
        m = self.models[key]
        reach = m.nofly_buffer_m + (2 * m.turn_radius_m if m.is_plane else 0)
        zb = [(g.buffer(reach), a, b) for g, a, b, _ in self.temp_zones]
        for s in sorties:
            ml = shapely.MultiLineString([[(p.ax, p.ay), (p.bx, p.by)] for p in s.pieces])
            s.blocked = [(a, b) for g, a, b in zb if g.intersects(ml)]

    def _crosswind(self, comp) -> float:
        wx, wy = wind_vector(self.p.wind_speed_ms, self.p.wind_dir_deg)
        th = comp.theta
        return abs(-wx * math.sin(th) + wy * math.cos(th))

    def _crab(self, comp, key):
        g = self.geoms[key]
        scale = g.wind_ms / self.p.wind_speed_ms if self.p.wind_speed_ms > 0 else 0.0
        return g.crab_rad(comp.w_perp * scale, comp.w_par * scale, self.ctxs[key].vs_of(comp.idx))

    def fresh_drones(self) -> List[DroneState]:
        t_ready = self.t0 + self.glob["setup_min"] * 60
        return [DroneState(id=d["id"], model=d["model"], title=d["title"], layer=d["layer"],
                           batteries=[t_ready] * d["batteries"], charge_s=d["charge_s"], turnaround_s=d["turnaround_s"],
                           free_at=t_ready) for d in self.drone_specs]

    def _use_cells(self, scale: float):
        """Сетка ячеек распределения площади с масштабом scale от базового размера (кэш по шагу 0.1)."""
        scale = round(float(scale), 1)
        if scale not in self._cells_cache:
            G = self._cells_cache[1.0][0] * scale
            self._cells_cache[scale] = (G, make_cells(self.comps, G, self.sites_xy))
        self.cell_m, self.cells = self._cells_cache[scale]
        return scale

    def evaluate(self, shares: Dict[str, float], cols: Dict[str, float], keep=False, cell_scale: float = 1.0):
        cell_scale = self._use_cells(cell_scale)
        assign = self.assign_cells(shares)
        sorties, bad = {}, []
        lost = 0.0
        for key in self.models:
            ps, extra = self.pieces_for(key, assign, cols[key])
            s, b = pack(self.ctxs[key], ps, self.layer_est[key])
            if extra:
                s2, b2 = pack(self.ctxs[key], order_nearest(extra, self.sites_xy), self.layer_est[key])
                for x in s2:
                    x.origin = "extra"
                s = s + s2
                lost += sum(x[2] for x in b2)  # недостижимый добор кромок — потеря покрытия, не невыполнимость
            self._tag_blocked(key, s)
            sorties[key] = s
            bad.extend([(x, y, key, L) for x, y, L, o in b])
        drones = self.fresh_drones()
        scheduling_error = None
        try:
            end, flight = schedule_estimate(sorties, drones, self.sites_xy, self.glob, self.windows)
        except SchedulingError as e:
            # One allocation may exhaust the planning horizon while another fleet mix fits.
            scheduling_error = str(e)
            end, flight = self.t0, sum(s.est_s for v in sorties.values() for s in v)
        makespan = (end + 600 - self.t0) if end > 0 else 0.0
        n_sorties = sum(len(v) for v in sorties.values())
        bad_len = sum(b[3] for b in bad)
        score = self._mission_score(makespan, flight, n_sorties)
        score += bad_len * 100.0 + lost * 2.0
        if scheduling_error:
            score += 1e15
        res = dict(score=score, makespan=makespan, flight=flight, n_sorties=n_sorties, bad_m=bad_len, lost_m=round(lost),
                   shares={k: round(v, 3) for k, v in shares.items()}, cols={k: round(v) for k, v in cols.items()},
                   cell_scale=cell_scale, method=getattr(self, "_method", "grid"), scheduling_error=scheduling_error)
        self.search_log.append({k: v for k, v in res.items()})
        if keep:
            res.update(sorties=sorties, bad=bad, assign=assign)
        return res

    def search(self):
        keys = list(self.models)
        G = self.cell_m
        col_cands = [2 * G, 4 * G, 8 * G, 1e7]
        effort = self.p.search_effort
        if effort == "fast":
            col_cands = [4 * G, 1e7]
        cols = {}
        # этап 1: ширина колонок для каждого типа (тип снимает всю площадь один)
        n_stage1 = len(keys) * len(col_cands)
        it = 0
        for k in keys:
            best = None
            for c in col_cands:
                it += 1
                self.progress(0.05 + 0.25 * it / n_stage1, f"Нарезка галсов: {self.models[k].title}")
                sh = {kk: (1.0 if kk == k else 0.0) for kk in keys}
                cc = {kk: c for kk in keys}
                r = self.evaluate(sh, cc)
                if best is None or r["score"] < best[0]:
                    best = (r["score"], c)
            cols[k] = best[1]
        # этап 2: доли площади между типами
        if len(keys) == 1:
            grid = [(1.0,)]
        else:
            step = {"fast": 0.25, "normal": 0.1 if len(keys) == 2 else 0.2, "thorough": 0.05 if len(keys) == 2 else 0.1}[effort]
            grid = _share_grid(len(keys), step)
        best = None
        for i, sh in enumerate(grid):
            self.progress(0.3 + 0.4 * (i + 1) / len(grid), "Распределение площади между типами БВС")
            r = self.evaluate(dict(zip(keys, sh)), cols)
            if best is None or r["score"] < best["score"]:
                best = r
        if self.p.objective == "balanced":
            self._calibrate_balance()
            best = min(self.search_log, key=lambda r: r["score"])
        # этап 3: локальное уточнение долей (в обоих режимах — пул байесовского режима включает все варианты сетки)
        if len(keys) > 1 and effort != "fast":
            step = 0.05 if len(keys) == 2 else 0.1
            improved = True
            rounds = 0
            while improved and rounds < 4:
                improved = False
                rounds += 1
                base = best["shares"]
                for a, b in itertools.permutations(keys, 2):
                    sh = dict(base)
                    if sh[a] < step - 1e-9:
                        continue
                    sh[a] -= step
                    sh[b] += step
                    r = self.evaluate(sh, cols)
                    if r["score"] < best["score"] - 1e-6:
                        best, improved = r, True
        # этап 4: байесовская оптимизация (TPE) с теплым стартом от лучшего варианта сетки
        if self.p.optimizer == "bayes":
            best = self._bayes(keys, cols, best)
        self.best_cfg = best
        # кандидаты для финального отбора: лучшие различные конфигурации по оценке
        # в пул финального отбора — лучшие варианты КАЖДОГО метода: быстрая оценка не видит переносов
        # вылетов на следующий световой день, поэтому решает только полное построение
        seen, cands = set(), []
        per_method = 3 if self.p.objective == "balanced" else (1 if effort == "fast" else 3)
        for method in ("grid", "bayes"):
            n = 0
            for r in sorted((r for r in self.search_log if r.get("method", "grid") == method and not r.get("scheduling_error")), key=lambda r: r["score"]):
                key = (tuple(sorted(r["shares"].items())), tuple(sorted(r["cols"].items())), r.get("cell_scale", 1.0))
                if key in seen:
                    continue
                seen.add(key)
                cands.append(r)
                n += 1
                if n >= per_method:
                    break
        self.candidates = cands
        if not cands:
            raise PlanningError(best.get("scheduling_error") or "Нет допустимого расписания в горизонте планирования")
        return self.evaluate(best["shares"], best["cols"], keep=True, cell_scale=best.get("cell_scale", 1.0))

    def _bayes(self, keys, cols0, best):
        """Байесовская оптимизация параметров плана (Optuna TPE).

        Пространство шире сетки: непрерывные доли площади между типами БВС, непрерывная ширина колонок
        нарезки для каждого типа (или «на всю ширину»), масштаб сетки ячеек распределения площади.
        Теплый старт: первая проба — лучший вариант сетки, поэтому результат по оценке не хуже сетки.
        TPE строит вероятностные модели «хороших» и «плохих» проб и выбирает следующую точку по
        максимуму ожидаемого улучшения — оценок нужно меньше, чем при переборе.
        """
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        self._method = "bayes"
        G0 = self._cells_cache[1.0][0]
        n_trials = {"fast": 20, "normal": 60, "thorough": 150}[self.p.search_effort]
        multi = len(keys) > 1

        def decode(t):
            if multi:
                w = [t.suggest_float(f"w_{k}", 0.0, 1.0) for k in keys]
                s = sum(w) or 1.0
                shares = {k: wi / s for k, wi in zip(keys, w)}
            else:
                shares = {keys[0]: 1.0}
            cols = {}
            for k in keys:
                if t.suggest_categorical(f"full_{k}", [False, True]):
                    cols[k] = 1e7
                else:
                    cols[k] = G0 * t.suggest_float(f"col_{k}", 1.5, 12.0, log=True)
            scale = t.suggest_float("cell_scale", 0.6, 1.6, step=0.1)
            return shares, cols, scale

        def objective(t):
            shares, cols, scale = decode(t)
            r = self.evaluate(shares, cols, cell_scale=scale)
            self.progress(0.7 + 0.02 * min(1.0, (t.number + 1) / n_trials), f"Байесовская оптимизация: проба {t.number + 1}/{n_trials}")
            return r["score"]

        study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=0, n_startup_trials=8, multivariate=True))
        warm = {"cell_scale": 1.0}
        for k in keys:
            if multi:
                warm[f"w_{k}"] = float(best["shares"].get(k, 0.0))
            full = cols0[k] >= 1e6
            warm[f"full_{k}"] = full
            if not full:
                warm[f"col_{k}"] = float(min(12.0, max(1.5, cols0[k] / G0)))
        study.enqueue_trial(warm)
        study.optimize(objective, n_trials=n_trials)
        bayes_log = [r for r in self.search_log if r.get("method") == "bayes"]
        cand = min(bayes_log, key=lambda r: r["score"]) if bayes_log else best
        self._method = "grid"
        self._use_cells(1.0)
        self.bayes_stats = {"trials": n_trials, "grid_best_score": round(best["score"], 1), "bayes_best_score": round(cand["score"], 1)}
        return cand if cand["score"] <= best["score"] else best

    def _mission_score(self, makespan, flight, n_sorties):
        if self.p.objective == "balanced" and self.balance_reference:
            ref = self.balance_reference
            # Freeze the grid ranges for local search, TPE and final dispatch.
            # A constant grid objective has zero regret; use its positive
            # baseline as a fallback scale for later candidates that differ.
            time_range = ref["time_max_s"] - ref["time_s"]
            flight_range = ref["flight_max_s"] - ref["flight_s"]
            t = (makespan - ref["time_s"]) / (time_range or ref["time_s"])
            f = (flight - ref["flight_s"]) / (flight_range or ref["flight_s"])
            # Do not clip: later improvements or overruns must still affect
            # ranking. Keep the existing seconds scale for additive penalties.
            score = (max(0.5 * t, 0.5 * f) + 0.01 * (t + f)) * ref["time_s"]
        elif self.p.objective in ("time", "balanced"):
            score = makespan + 1e-3 * flight
        else:
            score = flight + 1e-3 * makespan
        if self.p.objective != "time":
            score += n_sorties * self.p.takeoff_weight_h * 3600
        if self.p.objective != "time" and self.p.deadline_h and makespan > self.p.deadline_h * 3600:
            score += (makespan - self.p.deadline_h * 3600) * 50
        return score

    def _calibrate_balance(self):
        valid = [r for r in self.search_log if not r.get("scheduling_error") and r["flight"] > 0 and r["makespan"] > 0]
        feasible = [r for r in valid if r["bad_m"] < 1]
        pool = feasible or valid
        if not pool:
            return
        penalties = [r["score"] - self._mission_score(r["makespan"], r["flight"], r["n_sorties"]) for r in self.search_log]
        self.balance_reference = {"time_s": min(r["makespan"] for r in pool),
                                  "time_max_s": max(r["makespan"] for r in pool),
                                  "flight_s": min(r["flight"] for r in pool),
                                  "flight_max_s": max(r["flight"] for r in pool),
                                  "source": "feasible_grid" if feasible else "partial_grid",
                                  "method": "augmented_chebyshev", "time_weight": 0.5,
                                  "tie_break_weight": 0.01}
        for r, penalty in zip(self.search_log, penalties):
            r["score"] = self._mission_score(r["makespan"], r["flight"], r["n_sorties"]) + penalty

    def _actual_score(self, assigned):
        if not assigned:
            return float("inf")
        end = max(a.end for a in assigned) + 600
        flight = sum(a.end - a.start for a in assigned)
        return self._mission_score(end - self.t0, flight, len(assigned))

    def _dispatch_cfg(self, cfg, p0, p1):
        ev = self.evaluate(cfg["shares"], {k: float(v) for k, v in cfg["cols"].items()}, keep=True, cell_scale=cfg.get("cell_scale", 1.0))
        sid = 0
        for key in ev["sorties"]:
            for s in ev["sorties"][key]:
                sid += 1
                s.id = sid
        drones = self.fresh_drones()
        assigned, tracks, dstats = dispatch(self.ctxs, ev["sorties"], drones, self.sites_xy, self.glob, self.windows, self.terrain,
                                            progress=lambda f: self.progress(p0 + (p1 - p0) * f, "Построение траекторий и разведение бортов"))
        return ev, assigned, drones, dstats

    # ------------------------------------------------------------------ итог
    def run(self) -> dict:
        t_start = time.time()
        try:
            return self._run()
        except (PlanningError, SchedulingError, RoutingError) as e:
            return self._fail(str(e), time.time() - t_start)

    def _run(self) -> dict:
        t_start = time.time()
        self.prepare()
        self.survey_geoms()
        if not self.models:
            return self._fail("Ни один тип БВС не может выполнить съемку при заданных условиях", time.time() - t_start)
        self.build_contexts()
        self.search()
        # финальный отбор: полное построение (траектории, ремонт, разведение) для лучших кандидатов
        finals = []
        n = len(self.candidates)
        for i, cfg in enumerate(self.candidates):
            p0 = 0.72 + 0.18 * i / n
            try:
                ev, assigned, drones, dstats = self._dispatch_cfg(cfg, p0, p0 + 0.18 / n)
            except (SchedulingError, RoutingError) as e:
                self.warnings.append(f"Вариант распределения {cfg['shares']} отклонен: {e}")
                continue
            sc = self._actual_score(assigned) + 100.0 * sum(b[3] for b in ev["bad"] + dstats["unreachable"])
            cfg["actual_score"] = round(sc, 1) if np.isfinite(sc) else None
            finals.append((sc, cfg, ev, assigned, drones, dstats))
        if not finals:
            raise PlanningError("Не удалось построить траектории для выбранных вариантов: " + self.warnings[-1])
        self.progress(0.92, "Покрытие и проверка ограничений")
        from .validate import validate_plan
        original_recs = list(self.recs)
        result = None
        selected_daylight_violations = 0
        for _, self.best_cfg, best, assigned, drones, dstats in sorted(finals, key=lambda item: item[0]):
            self.recs = list(original_recs)
            for i, a in enumerate(sorted(assigned, key=lambda a: a.start)):
                a.sortie.id = i + 1
            bad = best["bad"] + dstats["unreachable"]
            if bad:
                titles = {k: m.title for k, m in self.models.items()}
                forb_all = _union(list(self.forb_survey.values()))
                self.recs.extend(infeasible_recommendations([(b[0], b[1], b[2]) for b in bad], self.sites_xy, forb_all,
                                                            self.frame, titles, self.radius))
            candidate = self._result(assigned, drones, dstats, "infeasible" if bad else "ok", time.time() - t_start)
            candidate["validation"] = validate_plan(candidate, self.sc, self.p)
            if result is None:
                result = candidate
                selected_daylight_violations = dstats["daylight_violations"]
            if candidate["validation"]["passed"]:
                result = candidate
                selected_daylight_violations = dstats["daylight_violations"]
                break
            failed = [c["name"] for c in candidate["validation"]["checks"] if not c["passed"]]
            self.warnings.append(f"Вариант распределения {self.best_cfg['shares']} отклонен проверкой: {'; '.join(failed)}")
        if selected_daylight_violations:
            self.warnings.append(f"{selected_daylight_violations} вылет(ов) не помещаются в светлое окно — проверьте длительность дня")
        if not result["validation"]["passed"]:
            result["status"] = "infeasible"
            failed = [c["name"] for c in result["validation"]["checks"] if not c["passed"]]
            result["error"] = "План не прошел проверку: " + "; ".join(failed)
            result["recommendations"].append({"kind": "infeasible", "text": result["error"] + ". Измените площадки, парк или ограничения и пересчитайте план"})
        result["summary"]["compute_s"] = round(time.time() - t_start, 1)
        self.progress(1.0, "Готово")
        return result

    def _fail(self, msg, dt):
        recs = [{"kind": "infeasible", "text": msg}]
        for k, rs in getattr(self, "excluded", {}).items():
            for r in rs:
                if r != msg:
                    recs.append({"kind": "infeasible", "text": r})
        if self.p.survey_type == "ir" and not any(f.model == "geoscan_801" and f.count > 0 for f in self.p.fleet):
            recs.append({"kind": "infeasible", "text": "Тепловизионная съемка доступна только на Геоскан 801 — добавьте его в парк"})
        return {"id": uuid.uuid4().hex[:10], "status": "infeasible", "error": msg, "warnings": self.warnings,
                "recommendations": recs, "summary": {"compute_s": round(dt, 1)}, "params": self.p.model_dump(mode="json"),
                "scenario_name": self.sc.name}

    def _coverage(self, assigned):
        lines_by_model = {}
        for a in assigned:
            s = a.sortie
            for p in s.pieces:
                lines_by_model.setdefault((s.model, p.comp), []).append(LineString([(p.ax, p.ay), (p.bx, p.by)]))
        covered = []
        model_areas = {}
        for (key, comp), lines in lines_by_model.items():
            g = self.geoms[key]
            crab = self._crab(self.comps[comp], key)
            w2 = g.swath_eff(crab) / 2
            footprint = shapely.union_all(shapely.buffer(np.array(lines, dtype=object), w2, cap_style="flat"))
            covered.append(footprint)
            model_areas.setdefault(key, []).append(footprint)
        areas = {key: _union(parts).intersection(self.common).area for key, parts in model_areas.items()}
        total = sum(areas.values())
        self.actual_shares = {key: area / total for key, area in areas.items()} if total else {}
        cov = _union(covered).buffer(0.5)
        req = self.requested
        covered_req = req.intersection(cov)
        uncovered = req.difference(cov).buffer(-2).buffer(2)
        reasons = []
        if not uncovered.is_empty:
            u_nf = uncovered.intersection(self.nofly).area if not self.nofly.is_empty else 0.0
            u_air = uncovered.intersection(self.outside_air).area if not self.outside_air.is_empty else 0
            obs = _union([og for _, og, _ in self.obstacles])
            u_obs = uncovered.intersection(obs.buffer(self.glob["obstacle_h_buffer_m"] + 1)).area if not obs.is_empty else 0
            u_rest = max(0.0, uncovered.area - u_nf - u_air - u_obs)
            for nm, v in (("запретные зоны", u_nf), ("вне разрешенного пространства", u_air),
                          ("высотные препятствия", u_obs), ("буферы безопасности/невыполнимые участки", u_rest)):
                if v > 100:
                    reasons.append({"reason": nm, "area_km2": round(v / 1e6, 4)})
        allowed = self.common.area
        self.coverage_allowed = min(1.0, covered_req.intersection(self.common).area / max(allowed, 1))
        return covered_req.area / max(req.area, 1), uncovered, reasons

    def _ll(self, x, y):
        lon, lat = self.frame.to_ll(np.asarray(x), np.asarray(y))
        return np.round(lon, 7), np.round(lat, 7)

    def _geom_ll(self, g):
        return shapely.geometry.mapping(self.frame.geom_to_ll(g)) if g is not None and not g.is_empty else None

    def _result(self, assigned, drones, dstats, status, dt) -> dict:
        tz = self.tz
        sorties_out = []
        total_dist = total_survey = photos = 0.0
        for a in sorted(assigned, key=lambda a: a.start):
            s, tb = a.sortie, a.traj
            ctx = self.ctxs[s.model]
            g = self.geoms[s.model]
            P = np.asarray(tb.P)
            T = np.asarray(tb.T) + a.start
            lon, lat = self._ll(P[:, 0], P[:, 1])
            agl = P[:, 2] - self.terrain.h(P[:, 0], P[:, 1])
            wps = [[float(lon[i]), float(lat[i]), round(float(P[i, 2]), 1), round(float(agl[i]), 1), float(T[i]), tb.PH[i]]
                   for i in range(len(P))]
            phases = []
            for i in range(1, len(T)):
                ph = tb.PH[i]
                if phases and phases[-1]["phase"] == ph:
                    phases[-1]["t_end"] = float(T[i])
                else:
                    phases.append({"phase": ph, "t_start": float(T[i - 1]), "t_end": float(T[i])})
            surv = sum(p.length for p in s.pieces)
            bases = [g.photo_base(self._crab(self.comps[p.comp], s.model)) for p in s.pieces]
            n_ph = sum(math.ceil(p.length / b) + 1 for p, b in zip(s.pieces, bases) if b)
            photos += n_ph
            total_dist += tb.dist
            total_survey += surv
            dur = a.end - a.start
            m = self.models[s.model]
            sorties_out.append({
                "id": s.id, "drone_id": a.drone, "model": s.model, "model_title": m.title,
                "site": self.site_names[s.site], "landing_site": self.site_names[s.site],
                "t_takeoff": a.start, "t_landing": a.end,
                "takeoff_iso": _iso(a.start, tz), "landing_iso": _iso(a.end, tz),
                "duration_min": round(dur / 60, 1), "distance_km": round(tb.dist / 1000, 2),
                "survey_km": round(surv / 1000, 2), "lines": len(s.pieces), "photos": n_ph,
                "photo_base_m": min(bases) if bases and bases[0] is not None else None,
                "battery_used_pct": round(100 * tb.energy_s / (m.endurance_min * 60), 1),
                "transit_alt_agl_m": [d.layer for d in drones if d.id == a.drone][0],
                "survey_alt_agl_m": round(g.altitude_agl, 1), "survey_speed_ms": round(ctx.vs_of(s.pieces[0].comp), 1),
                "transit_speed_ms": round(ctx.vt, 1), "ground_hold_s": round(a.delay_s),
                "launch_heading_deg": round((self.p.wind_dir_deg) % 360) if m.is_plane else None,
                "phases": phases,
                "keypoints": [{"label": lbl, "i": i} for i, lbl in tb.marks],
                "waypoints": wps,
                "lines_ll": [[list(map(float, self._ll(p.ax, p.ay))), list(map(float, self._ll(p.bx, p.by)))] for p in s.pieces],
            })
        start = min((s["t_takeoff"] for s in sorties_out), default=self.t0)
        end = max((s["t_landing"] for s in sorties_out), default=self.t0) + 10 * 60  # выгрузка данных после последней посадки
        flight_s = sum(s["t_landing"] - s["t_takeoff"] for s in sorties_out)
        cov_frac, uncovered, unc_reasons = self._coverage(assigned)
        drones_out = []
        for d in drones:
            ss = [s for s in sorties_out if s["drone_id"] == d.id]
            fl = sum(s["t_landing"] - s["t_takeoff"] for s in ss)
            drones_out.append({"id": d.id, "model": d.model, "title": d.title, "transit_alt_agl_m": d.layer,
                               "color": _export.PALETTE[len(drones_out) % len(_export.PALETTE)],
                               "sorties": [s["id"] for s in ss], "flight_h": round(fl / 3600, 2),
                               "utilization_pct": round(100 * fl / max(end - self.t0, 1), 1)})
        days = len({datetime.fromtimestamp(s["t_takeoff"], self.tz).date() for s in sorties_out})
        # ресурс до ТО (ответы организаторов: 201 — каждые 80 полетов, 801 — каждые 160 ч налета)
        maint = []
        for key, m in self.models.items():
            ss = [s for s in sorties_out if s["model"] == key]
            if not ss:
                continue
            n_dr = sum(1 for d in drones_out if d["model"] == key and d["sorties"])
            fh = sum(s["t_landing"] - s["t_takeoff"] for s in ss) / 3600
            if m.maintenance_flights:
                used, interval = len(ss) / m.maintenance_flights, f"{m.maintenance_flights} полетов"
            elif m.maintenance_hours:
                used, interval = fh / m.maintenance_hours, f"{m.maintenance_hours:g} ч налета"
            else:
                continue
            maint.append({"model": key, "title": m.title, "sorties": len(ss), "flight_h": round(fh, 2), "interval": interval,
                          "used_pct": round(100 * used, 1), "per_drone_pct": round(100 * used / max(n_dr, 1), 1),
                          "note": m.maintenance_note})
        summary = {
            "objective": self.p.objective, "energy_model": self.p.energy_model, "wind_model": self.p.wind_model,
            "balance_reference": self.balance_reference,
            "airspace_mode": self.p.airspace_mode, "ceiling_agl_m": round(self.ceiling, 1),
            "mission_start_iso": _iso(self.t0, tz), "first_takeoff_iso": _iso(start, tz), "mission_end_iso": _iso(end, tz),
            "makespan_h": round((end - self.t0) / 3600, 2), "flight_hours": round(flight_s / 3600, 2),
            "sorties": len(sorties_out), "takeoffs": len(sorties_out), "drones_used": sum(1 for d in drones_out if d["sorties"]),
            "drones_total": len(drones_out), "work_days": days, "total_km": round(total_dist / 1000, 1),
            "survey_km": round(total_survey / 1000, 1), "photos": int(photos),
            "area_requested_km2": round(self.requested.area / 1e6, 3),
            "coverage_pct": round(100 * cov_frac, 2), "coverage_allowed_pct": round(100 * self.coverage_allowed, 2),
            "area_allowed_km2": round(self.common.area / 1e6, 3), "uncovered": unc_reasons,
            "conflicts_resolved_by_delay": dstats["conflict_checks_resolved"],
            "shares": self.actual_shares, "allocation_shares": self.best_cfg["shares"], "column_width_m": self.best_cfg["cols"],
            "cell_m": round(self._cells_cache[round(self.best_cfg.get("cell_scale", 1.0), 1)][0]) if hasattr(self, "_cells_cache") else round(self.cell_m),
            "optimizer": self.p.optimizer, "bayes": getattr(self, "bayes_stats", None), "maintenance": maint,
            "headings_deg": [round(c.heading_deg, 1) for c in self.comps],
            "survey_speed_by_model_ms": {k: [round(self.ctxs[k].vs_of(c.idx), 1) for c in self.comps] for k in self.ctxs},
            "terrain": self.terrain_raw.source, "transit_layers_agl_m": sorted({s["transit_alt_agl_m"] for s in sorties_out}),
        }
        forb_all = _union([self.nofly] + [og for _, og, _ in self.obstacles])
        if self.no_plan:
            v = vlos_sites(self.coverable, self.sites_xy, self.p.vlos_radius_km * 1000, forb_all, self.frame)
            if v:
                self.recs.insert(0, v)
                # точечные рекомендации «добавьте площадку около…» дублируют покрытие кругами — оставляем одну сводную
                self.recs = [x for x in self.recs if x.get("kind") != "infeasible"]
        self.recs.extend(suggest_sites(self.coverable, self.sites_xy, forb_all, self.frame))
        geo = {
            "requested": self._geom_ll(self.requested),
            "uncovered": self._geom_ll(uncovered) if uncovered.area > 100 else None,
            "components": [{"heading_deg": round(c.heading_deg, 1)} for c in self.comps],
        }
        return {
            "id": uuid.uuid4().hex[:10], "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": status, "scenario_name": self.sc.name, "params": self.p.model_dump(mode="json"),
            "summary": summary,
            "models": {k: {**g.to_dict(), "title": self.models[k].title, "type": self.models[k].type,
                           "radius_km": round(self.radius[k] / 1000, 1), "budget_min": round(self.ctxs[k].budget_s / 60, 1)}
                       for k, g in self.geoms.items()},
            "excluded": self.excluded, "drones": drones_out, "sorties": sorties_out,
            "sites": [{"name": s.name, "lon": s.lon, "lat": s.lat, "reserve": s.reserve,
                       "auto": s.name in getattr(self, "auto_site_names", ())} for s in self.sc.sites],
            "geo": geo, "warnings": self.warnings, "recommendations": self.recs,
            "search_log": self.search_log[-200:],
            "final_candidates": self.candidates,
        }


class PlanningError(ValueError):
    pass


def plan(scenario: Scenario, params: MissionParams, progress=None) -> dict:
    return Planner(scenario, params, progress).run()
