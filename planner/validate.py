"""Независимая проверка плана («crash-тест», как у экспертов).

Работает только с выходными данными (WGS-84 точки + метки времени) и исходным сценарием —
не использует внутренние структуры планировщика. Геометрия и сближения проверяются
на отрезках между точками, рельеф дополнительно опрашивается с шагом 1 с.

Проверки:
  1. Опасные сближения бортов: горизонт < R_разв пары при вертикали < 30 м (оба в воздухе).
  2. Заходы в бесполетные зоны с учетом высотного диапазона и временного окна зоны.
  3. Высотные препятствия: пролет над/рядом ниже верха + запас.
  4. Разрешенное воздушное пространство и коридор высот.
  5. Энергия: длительность и протяженность вылета с резервом; достижимость назначенной
     площадки по оставшейся части проверенного маршрута.
  6. Ветер на рабочей высоте ≤ ТТХ; съемка в светлое время.
  7. Расписание: доступный парк, АКБ, обслуживание, переезды, площадки и срок.
"""
from __future__ import annotations

from datetime import datetime
from dataclasses import replace
from typing import Dict, List

import numpy as np
import shapely

from .catalog import load_catalog
from .environment import daylight_windows, wind_at
from .geo import LocalFrame, Scenario, Site


def _sample(sortie, frame, terrain):
    W = np.array([w[:5] for w in sortie["waypoints"]], dtype=float)
    T = W[:, 4]
    x, y = frame.to_xy(W[:, 0], W[:, 1])
    t0, t1 = int(np.ceil(T[0])), int(np.floor(T[-1]))
    ts = np.unique(np.concatenate([T, np.arange(t0, t1 + 1, dtype=float)]))
    lon, lat = np.interp(ts, T, W[:, 0]), np.interp(ts, T, W[:, 1])
    Z = np.interp(ts, T, W[:, 2])
    # высота над землей — собственной выборкой ЦМР в каждой секунде, а не из точек плана
    agl = Z - terrain.elevation(lon, lat)
    return {
        "t0": t0, "T": ts, "W": W, "P": np.column_stack([x, y, W[:, 2]]),
        "X": np.interp(ts, T, x), "Y": np.interp(ts, T, y),
        "Z": Z, "AGL": agl,
        "phase": np.array([sortie["waypoints"][min(i, len(T) - 1)][5] for i in np.searchsorted(T, ts, side="left")]),
    }


def _clip_linear(lo, hi, values, lower=None, upper=None):
    """Clip segment fractions to a scalar linear constraint, including boundaries."""
    a, d = values[:-1], np.diff(values)
    moving = np.abs(d) > 1e-12
    safe_d = np.where(moving, d, 1)
    if lower is not None:
        u = (lower - a) / safe_d
        lo = np.where(moving & (d > 0), np.maximum(lo, u), lo)
        hi = np.where(moving & (d < 0), np.minimum(hi, u), hi)
        hi = np.where(~moving & (a < lower), -1, hi)
    if upper is not None:
        u = (upper - a) / safe_d
        lo = np.where(moving & (d < 0), np.maximum(lo, u), lo)
        hi = np.where(moving & (d > 0), np.minimum(hi, u), hi)
        hi = np.where(~moving & (a > upper), -1, hi)
    return lo, hi


def _segments(A, constraints=()):
    """Subsegments during which all time/altitude constraints apply."""
    lo, hi = np.zeros(len(A["T"]) - 1), np.ones(len(A["T"]) - 1)
    for values, lower, upper in constraints:
        lo, hi = _clip_linear(lo, hi, values, lower, upper)
    idx = np.flatnonzero(lo <= hi)
    xy = np.column_stack([A["X"], A["Y"]])
    d = np.diff(xy, axis=0)[idx]
    ends = np.stack([xy[idx] + lo[idx, None] * d, xy[idx] + hi[idx, None] * d], axis=1)
    geoms = shapely.linestrings(ends)
    # отрезок нулевой длины (вертикальный взлет/посадка, зависание) — точка: GEOS < 3.12 считает такую линию
    # невалидной, и intersects/covers дают False — зависание над вышкой не ловилось, взлет считался «вне пространства»
    flat = np.hypot(*(ends[:, 1] - ends[:, 0]).T) < 1e-9
    if flat.any():
        geoms[flat] = shapely.points(ends[flat, 0])
    return idx, geoms


def _structure_errors(sorties, cat):
    errors, ids = [], set()
    if not isinstance(sorties, list) or any(not isinstance(s, dict) for s in sorties):
        return ["sorties должен быть массивом объектов вылетов"]
    for s in sorties:
        try:
            if s["id"] in ids:
                errors.append(f"Повторяется ID вылета {s['id']}")
            ids.add(s["id"])
            if s["model"] not in cat.drones or not isinstance(s["drone_id"], str):
                raise ValueError("неизвестная модель или борт")
            W = np.asarray([w[:5] for w in s["waypoints"]], dtype=float)
            if W.ndim != 2 or W.shape[1] != 5 or len(W) < 2 or not np.isfinite(W).all():
                raise ValueError("нужны минимум две конечные точки [lon, lat, AMSL, AGL, t, phase]")
            if np.any(np.diff(W[:, 4]) <= 0):
                raise ValueError("метки времени должны строго возрастать")
            if np.any(np.abs(W[:, 0]) > 180) or np.any(np.abs(W[:, 1]) > 90):
                raise ValueError("координаты вне WGS-84")
            if any(len(w) != 6 or w[5] not in {"ground", "takeoff", "transit_out", "descent", "survey", "climb", "transit_back", "landing"} for w in s["waypoints"]):
                raise ValueError("неизвестный этап полета")
            if not np.isfinite([s["t_takeoff"], s["t_landing"]]).all():
                raise ValueError("неконечное время вылета")
            if abs(W[0, 4] - s["t_takeoff"]) > 0.11 or abs(W[-1, 4] - s["t_landing"]) > 0.11:
                raise ValueError("время вылета не совпадает с траекторией")
            if W[-1, 4] - W[0, 4] > 86400:
                raise ValueError("длительность вылета превышает сутки")
        except (KeyError, TypeError, ValueError, IndexError) as e:
            errors.append(f"Некорректный вылет: {e}")
    return errors


def _pt(frame, x, y, t, label, sortie=None) -> dict:
    """Точка на карте для показа нарушения/факта проверки в интерфейсе."""
    lon, lat = frame.to_ll(float(x), float(y))
    return {"lon": round(float(lon), 6), "lat": round(float(lat), 6), "t": int(t), "label": label, "sortie": sortie}


def validate_plan(result: dict, scenario: Scenario, params) -> dict:
    sites = list(scenario.sites)
    for extra in getattr(params, "extra_sites", []):
        existing = next((s for s in sites if s.name == extra.name), None)
        if existing is not None:
            if (existing.lon, existing.lat) != (extra.lon, extra.lat):
                return {"passed": False, "checks": [{"name": "Площадки", "passed": False,
                        "details": [f"Имя площадки «{extra.name}» задано с разными координатами"]}]}
        else:
            sites.append(Site(name=extra.name, lon=extra.lon, lat=extra.lat))
    if not any(not s.reserve for s in sites):
        # With no supplied launch site, auto placement is part of the output contract.
        for auto in result.get("sites", []):
            if not isinstance(auto, dict) or not auto.get("auto"):
                continue
            try:
                lon, lat = float(auto["lon"]), float(auto["lat"])
                name = auto["name"]
                if not isinstance(name, str) or not name or any(s.name == name for s in sites):
                    raise ValueError("invalid name")
                if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    raise ValueError("invalid coordinate")
            except (KeyError, ValueError, TypeError):
                return {"passed": False, "checks": [{"name": "Площадки", "passed": False,
                        "details": ["Некорректная автоматическая площадка в плане"]}]}
            sites.append(Site(name=name, lon=lon, lat=lat))
    scenario = replace(scenario, sites=sites)
    cat = load_catalog()
    glob = cat.glob
    sorties = result.get("sorties", [])
    checks: List[dict] = []
    if not sorties:
        return {"passed": False, "checks": [{"name": "План", "passed": False, "details": ["нет вылетов"]}]}
    errors = _structure_errors(sorties, cat)
    if errors:
        return {"passed": False, "checks": [{"name": "Структура плана", "passed": False, "details": errors[:20]}]}
    lon0, lat0 = scenario.centroid()
    frame = LocalFrame(lon0 + 1e-4, lat0 + 1e-4)  # отдельная проекция — проверка независима
    from .terrain import Terrain
    terrain = Terrain(scenario.work_bounds() or scenario.bounds(), enabled=getattr(params, "use_terrain", True), margin_deg=0.06)
    S = {s["id"]: _sample(s, frame, terrain) for s in sorties}
    if params.use_terrain and not terrain.enabled:
        checks.append({"name": "Данные рельефа", "passed": False,
                       "details": [terrain.warning or "ЦМР недоступна — высоты проверить невозможно"]})
    vsep = glob["vertical_sep_m"]
    reserve = params.reserve_pct / 100

    # Original segments retain short, physically impossible jumps that resampling can hide.
    speed_viol = []
    for s in sorties:
        m, A = cat.drones[s["model"]], S[s["id"]]
        dt = np.diff(A["W"][:, 4])
        delta = np.diff(A["P"], axis=0)
        horizontal = np.linalg.norm(delta[:, :2], axis=1)
        wind_allowance = 0.0
        if getattr(params, "wind_model", "qa") == "physical":
            wind_allowance = wind_at(max(0.0, float(A["AGL"].max())), params.wind_speed_ms,
                                     glob.get("wind_shear_alpha", 0.143))
        # 0.1 m accounts for exported coordinate/altitude rounding, not extra flight time.
        if np.any(horizontal > (m.max_speed_ms + wind_allowance) * dt + 0.1):
            speed_viol.append(f"Вылет {s['id']}: горизонтальная скорость превышает ТТХ")
        descent = np.full(len(dt), m.sink_ms)
        if m.is_plane:
            landing = np.array([w[5] == "landing" for w in s["waypoints"][1:]])
            agl_at_points = np.interp(A["W"][:, 4], A["T"], A["AGL"])
            parachute = landing & (horizontal <= 0.1) & (agl_at_points[:-1] <= m.parachute_alt_m + 0.2)
            descent[parachute] = max(m.sink_ms, m.parachute_ms)
        if np.any(delta[:, 2] > m.climb_ms * dt + 0.11) or np.any(-delta[:, 2] > descent * dt + 0.11):
            speed_viol.append(f"Вылет {s['id']}: скорость набора высоты или снижения превышает ТТХ")
    checks.append({"name": "Скорости и вертикальная динамика", "passed": not speed_viol,
                   "value": len(speed_viol), "details": speed_viol[:20] or ["Скорости на каждом участке в пределах ТТХ с учетом ветра и посадки"]})

    # 1. разведение
    min_sep = np.inf
    closest = None
    viol, viol_pts = [], []
    ss = sorted(sorties, key=lambda s: s["t_takeoff"])
    for i, a in enumerate(ss):
        A = S[a["id"]]
        for b in ss[i + 1:]:
            if b["t_takeoff"] > a["t_landing"]:
                break
            if b["drone_id"] == a["drone_id"]:
                continue
            B = S[b["id"]]
            t0, t1 = max(A["T"][0], B["T"][0]), min(A["T"][-1], B["T"][-1])
            if t1 <= t0:
                continue
            ts = np.unique(np.concatenate([[t0, t1], A["T"][(A["T"] > t0) & (A["T"] < t1)],
                                          B["T"][(B["T"] > t0) & (B["T"] < t1)]]))
            pa = np.column_stack([np.interp(ts, A["T"], A[c]) for c in ("X", "Y", "Z", "AGL")])
            pb = np.column_stack([np.interp(ts, B["T"], B[c]) for c in ("X", "Y", "Z", "AGL")])
            rel = pa[:, :3] - pb[:, :3]
            lo, hi = np.zeros(len(ts) - 1), np.ones(len(ts) - 1)
            for values, lower, upper in ((rel[:, 2], -vsep + 1e-7, vsep - 1e-7),
                                         (pa[:, 3], 3.000001, None), (pb[:, 3], 3.000001, None)):
                lo, hi = _clip_linear(lo, hi, values, lower, upper)
            valid = lo <= hi
            dr = np.diff(rel[:, :2], axis=0)
            den = np.sum(dr * dr, axis=1)
            u = -np.sum(rel[:-1, :2] * dr, axis=1) / np.where(den > 0, den, 1)
            u = np.maximum(lo, np.minimum(hi, u))
            dh = np.linalg.norm(rel[:-1, :2] + u[:, None] * dr, axis=1)
            dh[~valid] = np.inf
            k = int(np.argmin(dh))
            tm = ts[k] + u[k] * (ts[k + 1] - ts[k])
            dz = abs(rel[k, 2] + u[k] * (rel[k + 1, 2] - rel[k, 2]))
            pos = pa[k, :2] + u[k] * (pa[k + 1, :2] - pa[k, :2])
            if dh[k] < min_sep:
                min_sep = float(dh[k])
                closest = _pt(frame, *pos, tm, f"Наибольшее сближение: {a['drone_id']} × {b['drone_id']}, "
                              f"{dh[k]:.1f} м по горизонтали, {dz:.1f} м по вертикали", a["id"])
                closest["pair"] = [a["id"], b["id"]]
            sep = max(cat.drones[a["model"]].separation_m, cat.drones[b["model"]].separation_m)
            if dh[k] < sep:
                viol.append(f"{a['drone_id']} × {b['drone_id']}: {dh[k]:.1f} м по горизонтали, {dz:.1f} м по вертикали, t={tm:.2f}")
                viol_pts.append(_pt(frame, *pos, tm, viol[-1], a["id"]))
    checks.append({"name": "Опасные сближения бортов", "passed": not viol, "points": viol_pts[:20] or ([closest] if closest else []),
                   "value": None if not np.isfinite(min_sep) else round(min_sep, 1),
                   "details": viol[:20] or [f"Минимальное горизонтальное расстояние при вертикали < {vsep:.0f} м: "
                                            f"{'—' if not np.isfinite(min_sep) else f'{min_sep:.0f} м'}"]})

    # 2. бесполетные зоны
    nf_viol, nf_pts = [], []
    for z in scenario.nofly:
        if getattr(z, "time_unknown", False) and getattr(params, "temp_restrictions", "active") == "ignore":
            continue  # пользователь подтвердил, что временное ограничение в период работ не действует
        g = frame.geom_to_xy(z.geom)
        shapely.prepare(g)
        for s in sorties:
            A = S[s["id"]]
            alt = A["AGL"] if z.alt_ref == "AGL" else A["Z"]
            idx, segments = _segments(A, [(A["AGL"], 0.1, None), (alt, z.alt_min_m, z.alt_max_m),
                                         (A["T"], z.active_from.timestamp() if z.active_from else None,
                                          z.active_to.timestamp() if z.active_to else None)])
            inside = shapely.intersects(g, segments)
            if inside.any():
                nf_viol.append(f"Вылет {s['id']} ({s['drone_id']}) пересекает зону «{z.name}»")
                k = int(idx[np.argmax(inside)])
                nf_pts.append(_pt(frame, A["X"][k], A["Y"][k], A["T"][k], nf_viol[-1], s["id"]))
    checks.append({"name": "Бесполетные зоны", "passed": not nf_viol, "value": len(nf_viol), "points": nf_pts[:20],
                   "details": nf_viol[:20] or ["Заходов нет (с учетом высот и времени действия зон)"]})

    # 3. препятствия — пространственный индекс: для вылета проверяются только объекты рядом с его траекторией
    ob_viol, ob_warn, ob_pts = [], 0, []
    obs = scenario.obstacles  # уже обрезаны до района работ (Scenario.crop_obstacles)
    cores, nears = [], []
    for o in obs:
        g = frame.geom_to_xy(o.geom)
        core = g.buffer(max(o.radius_m, 1.0)) if o.radius_m else g
        cores.append(core)
        nears.append(core.buffer(glob["obstacle_h_buffer_m"]))
    tree = shapely.STRtree(nears) if nears else None
    for s in sorties:
        if tree is None:
            break
        A = S[s["id"]]
        track = shapely.LineString(np.column_stack([A["X"], A["Y"]])) if len(A["X"]) > 1 else shapely.Point(A["X"][0], A["Y"][0])
        for k in tree.query(track, predicate="intersects"):
            o, core, near = obs[k], cores[k], nears[k]
            # отметка верха над уровнем моря (eTOD) сравнивается с абсолютной высотой борта, иначе — с высотой над землей
            alt = A["Z"] if o.alt_ref == "AMSL" else A["AGL"]
            clearance = cat.drones[s["model"]].obstacle_v_clearance_m or glob["obstacle_v_clearance_m"]
            idx, segments = _segments(A, [(A["AGL"], 0.1, None), (alt, None, o.height_m + clearance - 0.2)])
            hit = shapely.intersects(near.buffer(-0.2), segments)
            if hit.any():
                ob_viol.append(f"Вылет {s['id']} ({s['drone_id']}) нарушает запас {glob['obstacle_h_buffer_m']} м / "
                               f"{clearance} м у «{o.name}» ({o.height_m:.0f} м {o.alt_ref})")
                k = int(idx[np.argmax(hit)])
                ob_pts.append(_pt(frame, A["X"][k], A["Y"][k], A["T"][k], ob_viol[-1], s["id"]))
            close = shapely.contains_xy(near, A["X"], A["Y"]) & (alt < o.height_m + glob["obstacle_v_clearance_m"]) & (A["AGL"] > 3)
            ob_warn += int(close.any())
    checks.append({"name": "Высотные препятствия", "passed": not ob_viol, "value": len(ob_viol), "points": ob_pts[:20],
                   "details": ob_viol[:20] or [f"Столкновений нет; пролетов в зоне безопасности (буфер {glob['obstacle_h_buffer_m']} м / {glob['obstacle_v_clearance_m']} м): {ob_warn}"]})

    # 4. пространство и высоты
    corridor = glob["corridor_max_agl_m"]
    if scenario.airspace_ceiling_m:
        corridor = min(corridor, scenario.airspace_ceiling_m)
    no_plan = getattr(params, "airspace_mode", "flight_plan") == "no_flight_plan"
    if no_plan:
        corridor = min(corridor, 150.0)  # ФП ИВП: без плана полета — ниже 150 м от земли
    air_viol, air_pts = [], []
    air_g = frame.geom_to_xy(scenario.airspace) if scenario.airspace is not None else None
    for s in sorties:
        A = S[s["id"]]
        m = cat.drones[s["model"]]
        ceiling = min(corridor, m.max_alt_agl_m)
        if params.cloud_base_m is not None:
            ceiling = min(ceiling, params.cloud_base_m - 50)
        mx = float(A["AGL"].max())
        if mx > ceiling + 0.2:
            air_viol.append(f"Вылет {s['id']}: высота {mx:.0f} м над землей > {ceiling:.0f} м")
            k = int(np.argmax(A["AGL"]))
            air_pts.append(_pt(frame, A["X"][k], A["Y"][k], A["T"][k], air_viol[-1], s["id"]))
        if air_g is not None:
            _, segments = _segments(A, [(A["AGL"], 0.1, None)])
            out = ~shapely.covers(air_g, segments)
            if out.any():
                air_viol.append(f"Вылет {s['id']}: {int(out.sum())} с вне разрешенного пространства")
        if float(A["AGL"].min()) < -0.2:
            air_viol.append(f"Вылет {s['id']}: траектория ниже поверхности земли")
        if float(A["Z"].max()) > m.max_alt_amsl_m + 0.2:
            air_viol.append(f"Вылет {s['id']}: превышен потолок {m.max_alt_amsl_m:g} м AMSL")
        sv = A["phase"] == "survey"
        if sv.any() and float(A["AGL"][sv].min()) < m.min_alt_agl_m - 5:
            air_viol.append(f"Вылет {s['id']}: съемка ниже минимальной безопасной высоты {m.min_alt_agl_m:.0f} м")
    checks.append({"name": "Воздушное пространство и высоты", "passed": not air_viol, "value": len(air_viol), "points": air_pts[:20],
                   "details": air_viol[:20] or [f"Все точки в коридоре ≤ {corridor:.0f} м AGL и в разрешенной области"]})

    # 4б. прямая видимость и тип БВС в режиме без плана полета
    if no_plan:
        R = params.vlos_radius_km * 1000
        sites_xy = {x.name: frame.to_xy(x.lon, x.lat) for x in scenario.sites}
        vl = []
        for s in sorties:
            m = cat.drones[s["model"]]
            if m.is_plane:
                vl.append(f"Вылет {s['id']}: самолет {m.title} — полет за пределами прямой видимости требует плана полета")
                continue
            sx, sy = sites_xy.get(s.get("site"), (None, None))
            if sx is None:
                vl.append(f"Вылет {s['id']}: неизвестная площадка взлета")
                continue
            A = S[s["id"]]
            dmax = float(np.max(np.hypot(A["X"] - sx, A["Y"] - sy)))
            if dmax > R + 0.2:
                vl.append(f"Вылет {s['id']}: удаление от площадки {dmax / 1000:.2f} км > {params.vlos_radius_km:g} км")
        checks.append({"name": "Прямая видимость (ФП ИВП, без плана полета)", "passed": not vl, "value": len(vl),
                       "details": vl[:20] or [f"Все вылеты — коптеры в пределах {params.vlos_radius_km:g} км от площадки взлета"]})

    # 5. энергия и достижимость площадок
    from .coverage import wind_vector
    from .energy import energy_equiv_s
    wind_t = wind_vector(wind_at(300.0, params.wind_speed_ms, glob.get("wind_shear_alpha", 0.143)), params.wind_dir_deg)
    if getattr(params, "wind_model", "qa") == "qa":
        wind_t = (0.0, 0.0)  # методика QA: ветер не влияет на расход АКБ
    landing = [frame.to_xy(x.lon, x.lat) for x in scenario.sites]
    L = np.array(landing, dtype=float).reshape(-1, 2)
    en_viol, min_margin = [], 100.0
    for s in sorties:
        m = cat.drones[s["model"]]
        A = S[s["id"]]
        Tmax = m.endurance_min * 60
        dur = s["t_landing"] - s["t_takeoff"]
        mode = getattr(params, "energy_model", "linear")
        # Integrate every original segment; resampling changes turn/acceleration energy.
        W, P = A["W"], A["P"]
        ph = [w[5] for w in s["waypoints"]]
        e_s = energy_equiv_s(P, W[:, 4], ph, wind_t, m.is_plane, mode, m.accel_ms2)
        if e_s > Tmax * (1 - reserve) + 2:
            en_viol.append(f"Вылет {s['id']}: расход {100 * e_s / Tmax:.0f}% АКБ > {100 * (1 - reserve):.0f}% "
                           f"(модель {'калиброванная' if mode == 'calibrated' and not m.is_plane else 'линейная'})")
        distance_km = float(np.linalg.norm(np.diff(P, axis=0), axis=1).sum()) / 1000
        if m.max_route_km and distance_km > m.max_route_km * (1 - reserve) + 0.1:
            en_viol.append(f"Вылет {s['id']}: {distance_km:.1f} км > {m.max_route_km * (1 - reserve):.0f} км")
        if not len(L):
            en_viol.append(f"Вылет {s['id']}: нет площадок посадки")
            continue
        # A straight line to the closest site may cross a prohibited area and is
        # only a lower bound, not a reachability proof. The validated remainder
        # of this sortie is a constructive route to its actual landing site.
        margin = 100 * (1 - e_s / Tmax)
        min_margin = min(min_margin, margin)
        if e_s > Tmax:
            en_viol.append(f"Вылет {s['id']}: из точки маршрута не достигается ни одна площадка")
    checks.append({"name": "Энергия и достижимость площадок", "passed": not en_viol, "value": round(min_margin, 1),
                   "details": en_viol[:20] or [f"Все вылеты с резервом ≥ {params.reserve_pct:.0f}%; из любой точки маршрута "
                                               f"посадка достижима по оставшейся проверенной траектории, минимальный остаток {min_margin:.0f}%. "
                                               "Отдельный кратчайший аварийный маршрут не рассчитывается"]})

    # 6. ветер и светлое время
    w_viol = []
    for s in sorties:
        m = cat.drones[s["model"]]
        mx = float(S[s["id"]]["AGL"].max())
        w = wind_at(mx, params.wind_speed_ms, glob.get("wind_shear_alpha", 0.143))
        if w > m.max_wind_ms:
            w_viol.append(f"Вылет {s['id']}: ветер {w:.1f} м/с на {mx:.0f} м > {m.max_wind_ms} м/с")
    checks.append({"name": "Ветер (ТТХ)", "passed": not w_viol, "value": len(w_viol),
                   "details": w_viol[:10] or ["Ветер на рабочих высотах в пределах ТТХ всех бортов"]})
    if params.respect_daylight or no_plan:
        win = daylight_windows(lat0, lon0, params.start_time, 60, params.min_sun_elevation_deg)
        dl = 0
        for s in sorties:
            if not any(a <= s["t_takeoff"] and s["t_landing"] <= b + 0.1 for a, b in win):
                dl += 1
        checks.append({"name": "Светлое время", "passed": dl == 0, "value": dl,
                       "details": [f"Вылетов вне окна (Солнце ≥ {params.min_sun_elevation_deg:.0f}°): {dl}"]})
    schedule_viol = []
    site_map = {x.name: x for x in scenario.sites}
    by_drone = {}
    for s in ss:
        by_drone.setdefault(s["drone_id"], []).append(s)
        A = S[s["id"]]
        if s["t_takeoff"] < params.start_time.timestamp():
            schedule_viol.append(f"Вылет {s['id']}: старт раньше начала миссии")
        for field, i in (("site", 0), ("landing_site", -1)):
            site = site_map.get(s.get(field))
            if site is None:
                schedule_viol.append(f"Вылет {s['id']}: неизвестная площадка {s.get(field)!r}")
                continue
            x, y = frame.to_xy(site.lon, site.lat)
            if np.hypot(A["X"][i] - x, A["Y"][i] - y) > 5 or abs(A["AGL"][i]) > 1:
                schedule_viol.append(f"Вылет {s['id']}: точка {'взлета' if i == 0 else 'посадки'} не совпадает с площадкой «{site.name}»")
    for drone, flights in by_drone.items():
        model = flights[0]["model"]
        m = cat.drones[model]
        if any(s["model"] != model for s in flights):
            schedule_viol.append(f"Борт {drone}: модель меняется между вылетами")
        item = next((f for f in params.fleet if f.model == model), None)
        if item is None:
            schedule_viol.append(f"Борт {drone}: модели нет в заданном парке")
            continue
        ready = [params.start_time.timestamp() + glob["setup_min"] * 60] * (item.batteries or m.batteries)
        prev = None
        for s in flights:
            if prev:
                earliest = prev["t_landing"] + m.turnaround_min * 60
                a, b = site_map.get(prev.get("landing_site")), site_map.get(s.get("site"))
                if a and b and (a.lon, a.lat) != (b.lon, b.lat):
                    ax, ay = frame.to_xy(a.lon, a.lat)
                    bx, by = frame.to_xy(b.lon, b.lat)
                    earliest += np.hypot(bx - ax, by - ay) * 1.3 / (glob["ground_speed_kmh"] / 3.6) + glob["relocation_pack_min"] * 60
                if s["t_takeoff"] < earliest - 0.2:
                    schedule_viol.append(f"Борт {drone}: вылет {s['id']} начинается до освобождения/обслуживания/переезда борта")
            idx = int(np.argmin(ready))
            if s["t_takeoff"] < ready[idx] - 0.2:
                schedule_viol.append(f"Борт {drone}: нет готовой АКБ/не завершено развертывание для вылета {s['id']}")
            ready[idx] = s["t_landing"] + m.charge_min * 60
            prev = s
    for f in params.fleet:
        count = sum(flights[0]["model"] == f.model for flights in by_drone.values())
        if count > f.count:
            schedule_viol.append(f"Тип {f.model}: задействовано {count} бортов, доступно {f.count}")
    if params.deadline_h is not None:
        end = max(s["t_landing"] for s in sorties) + 600
        if end > params.start_time.timestamp() + params.deadline_h * 3600 + 0.1:
            schedule_viol.append(f"Срок {params.deadline_h:g} ч не выдерживается (включая выгрузку после посадки)")
    checks.append({"name": "Расписание, площадки и срок", "passed": not schedule_viol,
                   "details": schedule_viol[:20] or ["Парк, площадки, АКБ, обслуживание, переезды и срок согласованы"]})
    return {"passed": all(c["passed"] for c in checks), "checks": checks}
