"""Покрытие области съемки галсами (boustrophedon) и декомпозиция на ячейки.

1. Каждая связная компонента эффективной области получает свой курс галсов θ:
   минимизируется оценка времени съемки = Σ_моделей (площадь / шаг_эфф(θ) / v + число_разворотов(θ) · t_разв).
   Шаг_эфф зависит от угла сноса при боковом ветре (самолет), число разворотов — от ширины
   области поперек галсов. Итог: галсы вдоль длинной стороны и по возможности по ветру.
2. Компонента в повернутой СК (галсы горизонтальны) режется сеткой ячеек G×G.
   Ячейки распределяются между типами БВС (см. optimizer), внутри типа — одинаковый шаг
   галсов, линии выровнены по всей компоненте, поэтому соседние ячейки одного типа
   сшиваются без зазоров и дублей.
3. Галсы строятся сканированием многоугольника (правило чет-нечет по всем кольцам — корректно
   для MultiPolygon и отверстий), удлиняются на половину кадра за границу области (полное
   продольное перекрытие на краю), но не заходят в запретные зоны.
4. Галсы режутся на колонки ширины C, обход колонок и рядов — змейкой.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon, box
from shapely import affinity


def rot_fwd(x, y, th):
    c, s = math.cos(th), math.sin(th)
    return x * c + y * s, -x * s + y * c


def rot_inv(u, v, th):
    c, s = math.cos(th), math.sin(th)
    return u * c - v * s, u * s + v * c


def rotate_geom(g, th):
    """В СК галсов: поворот на −θ вокруг начала координат."""
    return affinity.rotate(g, -th, origin=(0, 0), use_radians=True)


def unrotate_geom(g, th):
    return affinity.rotate(g, th, origin=(0, 0), use_radians=True)


def _edges(geom_rot) -> np.ndarray:
    segs = []
    polys = geom_rot.geoms if isinstance(geom_rot, (MultiPolygon, shapely.GeometryCollection)) else [geom_rot]
    for p in polys:
        if not isinstance(p, Polygon) or p.is_empty:
            continue
        for ring in [p.exterior, *p.interiors]:
            c = np.asarray(ring.coords)
            segs.append(np.column_stack([c[:-1], c[1:]]))
    if not segs:
        return np.zeros((0, 4))
    return np.vstack(segs)


def scan_intervals(edges: np.ndarray, v: float) -> List[tuple]:
    """Пересечения горизонтали v с многоугольником: список (u0, u1) внутри."""
    if len(edges) == 0:
        return []
    u1, v1, u2, v2 = edges[:, 0], edges[:, 1], edges[:, 2], edges[:, 3]
    m = ((v1 <= v) & (v2 > v)) | ((v2 <= v) & (v1 > v))
    if not m.any():
        return []
    uu = u1[m] + (v - v1[m]) * (u2[m] - u1[m]) / (v2[m] - v1[m])
    uu.sort()
    return [(float(uu[i]), float(uu[i + 1])) for i in range(0, len(uu) - 1, 2) if uu[i + 1] - uu[i] > 1e-6]


def subtract_intervals(a0: float, a1: float, forb: List[tuple]) -> List[tuple]:
    out = [(a0, a1)]
    for f0, f1 in forb:
        nxt = []
        for s0, s1 in out:
            if f1 <= s0 or f0 >= s1:
                nxt.append((s0, s1))
            else:
                if f0 > s0:
                    nxt.append((s0, f0))
                if f1 < s1:
                    nxt.append((f1, s1))
        out = nxt
    return out


def wind_vector(speed: float, dir_from_deg: float):
    az_to = math.radians(dir_from_deg + 180.0)
    return speed * math.sin(az_to), speed * math.cos(az_to)


@dataclass
class Component:
    idx: int
    geom: object          # метрическая СК, эффективная область (общая для всех типов)
    w_par: float = 0.0    # составляющая ветра вдоль галсов, м/с
    w_perp: float = 0.0   # поперек галсов
    theta: float = 0.0    # курс галсов, рад от оси X (восток) против часовой
    rot: object = None    # геометрия в СК галсов
    vmin: float = 0.0
    umin: float = 0.0

    @property
    def heading_deg(self) -> float:
        """Путевой угол галса от севера по часовой (0..180)."""
        return (90.0 - math.degrees(self.theta)) % 180.0


@dataclass
class Cell:
    comp: int
    ci: int
    cj: int
    box_rot: tuple
    area: float
    dist_site: float
    centroid: tuple


@dataclass
class Piece:
    model: str
    comp: int
    col: int
    row: int
    ax: float
    ay: float
    bx: float
    by: float
    optional: bool = False  # добор кромок за другой тип БВС: недостижимость = потеря покрытия, а не невыполнимость

    @property
    def length(self):
        return math.hypot(self.bx - self.ax, self.by - self.ay)


def turn_time(v: float, is_plane: bool, turn_r: float, accel: float, d: float, same_row: bool) -> float:
    """Время перехода между концом галса и началом следующего (v — путевая скорость съемки)."""
    if same_row:
        return d / v
    if is_plane:
        # та же геометрия, что строится в траектории: вынос R + дуга радиуса r = max(R, d/2) + заход R
        r = max(turn_r, d / 2)
        arc = r * (2 * math.pi - 2 * math.asin(min(1.0, d / (2 * r))))
        return (2 * turn_r + arc) / v
    return d / v + 2 * v / max(accel, 0.5)


def choose_heading(comp_geom, geoms: Dict[str, object], models: Dict[str, object], wind_ms: float,
                   wind_dir: float, step_deg: float = 2.0) -> float:
    hull = np.asarray(comp_geom.convex_hull.exterior.coords) if comp_geom.convex_hull.geom_type == "Polygon" else None
    if hull is None:
        return 0.0
    wx, wy = wind_vector(wind_ms, wind_dir)
    area = comp_geom.area
    cands = list(np.radians(np.arange(0, 180, step_deg)))
    # добавляем направления ребер выпуклой оболочки — оптимум ширины достигается на них
    for a, b in zip(hull[:-1], hull[1:]):
        cands.append(math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi)
    best, best_cost = 0.0, float("inf")
    for th in cands:
        _, v = rot_fwd(hull[:, 0], hull[:, 1], th)
        width = float(v.max() - v.min())
        cross = abs(-wx * math.sin(th) + wy * math.cos(th))
        par = abs(wx * math.cos(th) + wy * math.sin(th))
        cost = 0.0
        for key, g in geoms.items():
            m = models[key]
            scale = g.wind_ms / wind_ms if wind_ms > 0 else 0.0
            wp, wq = par * scale, cross * scale
            v = g.survey_speed(wp, wq)
            if v <= 0:
                cost += 1e12
                continue
            crab = g.crab_rad(wq, wp, v)
            s = g.spacing(crab)
            n_turns = width / s
            tt = turn_time(v, m.is_plane, m.turn_radius_m, getattr(m, "accel_ms2", 2.0), s, False)
            cost += area / s / v + n_turns * tt
        if cost < best_cost - 1e-9:
            best, best_cost = th, cost
    return best


def _split_nonconvex(p: Polygon, min_area: float, solidity: float = 0.75, depth: int = 0) -> List[Polygon]:
    """Разбиение изогнутых/невыпуклых областей (коридоры с поворотами, «Г»-образные участки).

    Пока выпуклость (площадь / площадь выпуклой оболочки) ниже порога, область режется
    поперек длинной оси минимального описанного прямоугольника; разрез выбирается среди
    нескольких положений так, чтобы максимизировать выпуклость худшей из частей.
    Каждая часть затем получает свой курс галсов.
    """
    hull = p.convex_hull.area
    if depth >= 5 or p.area < 4 * min_area or hull <= 0 or p.area / hull >= solidity:
        return [p]
    rect = np.asarray(p.minimum_rotated_rectangle.exterior.coords)[:4]
    e1, e2 = rect[1] - rect[0], rect[2] - rect[1]
    axis = e1 if np.hypot(*e1) >= np.hypot(*e2) else e2
    L = float(np.hypot(*axis))
    if L < 1e-6:
        return [p]
    ux, uy = axis / L
    c = np.asarray(p.centroid.coords[0])
    best = None
    for f in (-0.3, -0.15, 0.0, 0.15, 0.3):
        m = c + np.array([ux, uy]) * f * L
        # полуплоскость «до» разреза
        big = 4 * L
        half = Polygon([m + np.array([-uy, ux]) * big, m + np.array([-uy, ux]) * big - np.array([ux, uy]) * big,
                        m - np.array([-uy, ux]) * big - np.array([ux, uy]) * big, m - np.array([-uy, ux]) * big])
        a, b = p.intersection(half), p.difference(half)
        if a.is_empty or b.is_empty or a.area < min_area or b.area < min_area:
            continue
        sol = min(x.area / max(x.convex_hull.area, 1e-9) for x in (a, b))
        if best is None or sol > best[0]:
            best = (sol, a, b)
    if best is None or best[0] <= p.area / hull + 0.02:
        return [p]
    out = []
    for part in best[1:]:
        for g in getattr(part, "geoms", [part]):
            if isinstance(g, Polygon) and g.area >= min_area:
                out.extend(_split_nonconvex(g, min_area, solidity, depth + 1))
    return out or [p]


def make_components(eff_geom, geoms, models, wind_ms, wind_dir, min_area=1.0) -> List[Component]:
    parts = [g for g in getattr(eff_geom, "geoms", [eff_geom]) if isinstance(g, Polygon) and g.area >= min_area]
    parts = [q for p in parts for q in _split_nonconvex(p, max(min_area, 0.02 * p.area))]
    comps = []
    for i, p in enumerate(sorted(parts, key=lambda g: -g.area)):
        th = choose_heading(p, geoms, models, wind_ms, wind_dir)
        r = rotate_geom(p, th)
        minx, miny, _, _ = r.bounds
        wx, wy = wind_vector(wind_ms, wind_dir)
        comps.append(Component(idx=i, geom=p, theta=th, rot=r, vmin=miny, umin=minx,
                               w_par=abs(wx * math.cos(th) + wy * math.sin(th)),
                               w_perp=abs(-wx * math.sin(th) + wy * math.cos(th))))
    return comps


def make_cells(comps: List[Component], cell_m: float, sites_xy: np.ndarray) -> List[Cell]:
    cells = []
    for c in comps:
        minx, miny, maxx, maxy = c.rot.bounds
        nx = max(1, int(math.ceil((maxx - minx) / cell_m)))
        ny = max(1, int(math.ceil((maxy - miny) / cell_m)))
        boxes, ids = [], []
        for i in range(nx):
            for j in range(ny):
                b = (minx + i * cell_m, miny + j * cell_m, minx + (i + 1) * cell_m, miny + (j + 1) * cell_m)
                boxes.append(box(*b))
                ids.append((i, j, b))
        inter = shapely.intersection(np.array(boxes, dtype=object), c.rot)
        areas = shapely.area(inter)
        for (i, j, b), g, a in zip(ids, inter, areas):
            if a < 1.0:
                continue
            cu, cv = g.centroid.x, g.centroid.y
            cx, cy = rot_inv(cu, cv, c.theta)
            d = float(np.min(np.hypot(sites_xy[:, 0] - cx, sites_xy[:, 1] - cy)))
            cells.append(Cell(comp=c.idx, ci=i, cj=j, box_rot=b, area=float(a), dist_site=d, centroid=(cx, cy)))
    return cells


def generate_pieces(model_key: str, geom, comp: Component, region_rot, forbidden_rot, crab: float,
                    col_m: float, ext_m: float, sites_xy: np.ndarray, row_counter_start: int = 0,
                    optional: bool = False) -> List[Piece]:
    """Галсы одного типа БВС в пределах region_rot (СК галсов), нарезанные на колонки."""
    s = geom.spacing(crab)
    edges = _edges(region_rot)
    fedges = _edges(forbidden_rot) if forbidden_rot is not None and not forbidden_rot.is_empty else np.zeros((0, 4))
    if len(edges) == 0:
        return []
    minv, maxv = float(edges[:, [1, 3]].min()), float(edges[:, [1, 3]].max())
    k0 = int(math.floor((minv - comp.vmin - s / 2) / s))
    k1 = int(math.ceil((maxv - comp.vmin - s / 2) / s))
    rows = []
    levels = [(k, comp.vmin + s / 2 + k * s) for k in range(k0, k1 + 1)]
    # A polygon thinner than half a spacing can lie entirely between scanlines.
    # Give every such island a real survey line instead of silently dropping it.
    for part in getattr(region_rot, "geoms", [region_rot]):
        if part.geom_type != "Polygon" or part.is_empty:
            continue
        pe = _edges(part)
        if not any(scan_intervals(pe, v) for _, v in levels):
            levels.append((k0 - 1 - len(levels), part.representative_point().y))
    for k, v in levels:
        if v < minv - 1e-6 or v > maxv + 1e-6:
            continue
        ivs = scan_intervals(edges, v)
        if not ivs:
            continue
        forb = scan_intervals(fedges, v) if len(fedges) else []
        segs = []
        for u0, u1 in ivs:
            # удлинение на ext за край области, но не в запретную зону
            lo, hi = u0 - ext_m, u1 + ext_m
            for f0, f1 in forb:
                if f1 <= u0 + 1e-6 and f1 > lo:
                    lo = f1
                if f0 >= u1 - 1e-6 and f0 < hi:
                    hi = f0
            for a, b in subtract_intervals(lo, hi, forb):
                if b - a > 1.0:
                    segs.append((a, b))
        if segs:
            rows.append((k, v, segs))
    if not rows:
        return []
    # нарезка на колонки
    by_col: Dict[int, List[tuple]] = {}
    for k, v, segs in rows:
        for a, b in segs:
            c0 = int(math.floor((a - comp.umin) / col_m))
            c1 = int(math.floor((b - comp.umin - 1e-6) / col_m))
            for c in range(c0, c1 + 1):
                lo = max(a, comp.umin + c * col_m)
                hi = min(b, comp.umin + (c + 1) * col_m)
                if hi - lo > 1.0:
                    by_col.setdefault(c, []).append((k, v, lo, hi))
    cols = sorted(by_col)
    # старт с колонки, ближайшей к площадке
    if len(cols) > 1:
        def col_dist(c):
            items = by_col[c]
            u = np.mean([(x[2] + x[3]) / 2 for x in items])
            vv = np.mean([x[1] for x in items])
            x, y = rot_inv(u, vv, comp.theta)
            return float(np.min(np.hypot(sites_xy[:, 0] - x, sites_xy[:, 1] - y)))
        if col_dist(cols[-1]) < col_dist(cols[0]):
            cols = cols[::-1]
    # «полосы» внутри колонки: если ряд разрезан отверстием/запретной зоной на несколько отрезков,
    # отрезки с одинаковым порядковым номером образуют полосу; полосы облетаются по очереди
    # (упрощенная клеточная декомпозиция boustrophedon) — без перескока через препятствие на каждом ряду.
    pieces: List[Piece] = []
    up = True
    rc = row_counter_start
    for c in cols:
        items = by_col[c]
        rows_k: Dict[int, list] = {}
        for x in items:
            rows_k.setdefault(x[0], []).append(x)
        lanes: Dict[int, list] = {}
        for k, segs in rows_k.items():
            for rank, x in enumerate(sorted(segs, key=lambda x: x[2])):
                lanes.setdefault(rank, []).append(x)
        for lane in sorted(lanes):
            segs_l = sorted(lanes[lane], key=lambda x: x[0], reverse=not up)
            for (k, v, lo, hi) in segs_l:
                fwd = (rc % 2 == 0)
                ua, ub = (lo, hi) if fwd else (hi, lo)
                ax, ay = rot_inv(ua, v, comp.theta)
                bx, by = rot_inv(ub, v, comp.theta)
                pieces.append(Piece(model=model_key, comp=comp.idx, col=c * 100 + lane, row=k, ax=ax, ay=ay, bx=bx, by=by,
                                    optional=optional))
                rc += 1
            up = not up
    return pieces
