"""Вылеты: упаковка галсов в вылеты с учетом энергии и построение 4D-траекторий.

Энергия — линейная модель по времени полета (QA): вылет допустим, если
    t_взлет + t_перелет_туда + t_съемка + t_развороты + t_перелет_обратно + t_посадка ≤ T_max · (1 − резерв),
для Геоскан 201 дополнительно ограничена протяженность маршрута (ТТХ: 210 км).

Упаковка — жадная последовательная по порядку обхода «змейкой»: галсы добавляются, пока
остается энергия на возврат к ближайшей площадке; галс, который целиком не помещается,
делится (бинарный поиск доли), остаток открывает следующий вылет. Если даже минимальный
отрезок галса недостижим с резервом — точка невыполнима, выдается рекомендация.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import shapely

from .coverage import Piece, turn_time
from .routing import RoutingError, path_length


@dataclass
class ModelCtx:
    key: str
    model: object          # DroneModel
    geom: object           # SurveyGeom
    sites_xy: np.ndarray   # основные площадки
    budget_s: float
    budget_m: float
    router_transit: object = None
    router_survey: object = None
    terrain: object = None
    terrain_step: float = 25.0
    vs_comp: dict = field(default_factory=dict)   # путевая скорость съемки по компонентам (курс галсов vs ветер)
    est_margin: float = 0.97                      # запас оценки упаковки относительно точной траектории
    agl_cap: float = 700.0                        # потолок AGL (коридор или нижняя граница высотной зоны)
    temp_zones: list = field(default_factory=list)  # [(геометрия, t_from, t_to)] временные запретные зоны
    energy_mode: str = "linear"                   # модель расхода АКБ: linear | calibrated
    max_site_dist: float = math.inf               # прямая видимость (ФП ИВП без плана полета): удаление от площадки, м
    strict_ceiling: bool = False                  # потолок жесткий на наборе/возврате (без плана полета, 150 м)
    obst_tops: Optional[tuple] = None             # (STRtree, зоны препятствий с буфером, верх + запас AMSL) — для перелетов ниже эшелона
    wind_t: tuple = (0.0, 0.0)                    # вектор ветра на эшелоне перелета, м/с (куда дует)
    _detour: dict = field(default_factory=dict, repr=False)  # кэш коэффициентов обхода зон: (площадка, клетка 250 м) → k
    _tline: dict = field(default_factory=dict, repr=False)   # кэш профилей огибания рельефа по отрезкам (ремонт перестраивает вылет)

    @property
    def vs(self):
        return self.geom.speed_ms

    @property
    def vt(self):
        """Номинальная скорость (без ветра) — для спиралей набора/снижения."""
        return self.geom.transit_speed(0.0, 0.0)

    def vs_of(self, comp: int) -> float:
        return self.vs_comp.get(comp, self.geom.speed_ms)

    def vt_dir(self, ax, ay, bx, by) -> float:
        dx, dy = bx - ax, by - ay
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return self.vt
        ux, uy = dx / d, dy / d
        wx, wy = self.wind_t
        return self.geom.transit_speed(wx * ux + wy * uy, -wx * uy + wy * ux)

    def leg_t(self, ax, ay, bx, by) -> float:
        return math.hypot(bx - ax, by - ay) / self.vt_dir(ax, ay, bx, by)

    def detour(self, si: int, x: float, y: float) -> float:
        """Во сколько раз перелет площадка si ↔ точка длиннее прямой из-за обхода запретных зон (кэш по клеткам 250 м).

        Без этого упаковка недооценивала вылеты у зон: точная траектория не укладывалась в АКБ и ремонт
        отрезал хвосты в отдельные короткие вылеты."""
        r = self.router_transit
        if r is None or r.empty:
            return 1.0
        key = (si, int(x // 250.0), int(y // 250.0))
        k = self._detour.get(key)
        if k is None:
            sx, sy = float(self.sites_xy[si][0]), float(self.sites_xy[si][1])
            cx, cy = (key[1] + 0.5) * 250.0, (key[2] + 0.5) * 250.0
            d = math.hypot(cx - sx, cy - sy)
            k = 1.0 if d < 300 or r._clear((sx, sy), (cx, cy)) else max(1.0, r.routed_dist((sx, sy), (cx, cy)) / d)
            self._detour[key] = k
        return k

    def transit_len(self, a, b) -> float:
        """Длина перелета a → b на эшелоне в обход зон (кэш по клеткам 250 м — оценка для упаковки)."""
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        rt = self.router_transit
        if rt is None or rt.empty:
            return d
        key = ("t", int(a[0] // 250), int(a[1] // 250), int(b[0] // 250), int(b[1] // 250))
        k = self._detour.get(key)
        if k is None:
            k = 1.0 if d < 50 or rt._clear(a, b) else max(1.0, rt.routed_dist(a, b) / d)
            self._detour[key] = k
        return d * k

    def site_leg(self, si: int, x: float, y: float, outbound: bool) -> Tuple[float, float]:
        """Время и путь перелета площадка → точка (outbound) или точка → площадка с учетом обхода зон."""
        sx, sy = float(self.sites_xy[si][0]), float(self.sites_xy[si][1])
        try:
            L = math.hypot(x - sx, y - sy) * self.detour(si, x, y)
        except RoutingError:
            return math.inf, math.inf
        v = self.vt_dir(sx, sy, x, y) if outbound else self.vt_dir(x, y, sx, sy)
        return L / v, L


def fixed_overhead(ctx: ModelCtx, layer_agl: float) -> Tuple[float, float]:
    """Время и путь на взлет/набор/снижение/посадку при эшелоне перелета layer_agl."""
    m, H = ctx.model, ctx.geom.altitude_agl
    if m.is_plane:
        tc = layer_agl / m.climb_ms
        tds = max(0.0, layer_agl - H) / m.sink_ms
        tc2 = max(0.0, layer_agl - H) / m.climb_ms
        tl = max(0.0, layer_agl - m.parachute_alt_m) / m.sink_ms
        lead = 4 * m.turn_radius_m / ctx.vt  # заход на галс и выход с него
        return m.launch_s + tc + tds + tc2 + tl + m.parachute_alt_m / m.parachute_ms + lead, (tc + tds + tc2 + tl) * ctx.vt + 4 * m.turn_radius_m
    t = layer_agl / m.climb_ms + (layer_agl - H) / m.sink_ms + (layer_agl - H) / m.climb_ms + layer_agl / m.sink_ms + 20.0
    return t, 0.0


@dataclass
class Sortie:
    model: str
    pieces: List[Piece]
    site: int
    est_s: float = 0.0
    est_m: float = 0.0
    id: int = -1
    blocked: list = field(default_factory=list)  # окна (epoch) временных запретных зон, которые задевает вылет
    origin: str = "main"                          # main | extra (добор кромок) | tail (перенос при ремонте) — диагностика


def _d(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


def _turn(ctx: ModelCtx, prev: Piece, q: Piece) -> Tuple[float, float]:
    d = _d(prev.bx, prev.by, q.ax, q.ay)
    same_row = prev.comp == q.comp and prev.row == q.row
    v = ctx.vs_of(q.comp)
    t = turn_time(v, ctx.model.is_plane, ctx.model.turn_radius_m, ctx.model.accel_ms2, d, same_row)
    return t, t * v


def _conn(ctx: ModelCtx, prev: Piece, q: Piece, layer_agl: float) -> Tuple[float, float]:
    """Переход между галсами. Если на рабочей высоте путь закрыт (препятствие/зона), траектория поднимется
    на эшелон и обойдет зону (build_trajectory) — оценка должна это учитывать."""
    rs = ctx.router_survey
    if rs is not None and not rs.empty:
        a, b = (prev.bx, prev.by), (q.ax, q.ay)
        if math.hypot(b[0] - a[0], b[1] - a[1]) > 1.0 and not rs._clear(a, b):
            L = ctx.transit_len(a, b)
            m = ctx.model
            dz = max(0.0, layer_agl - ctx.geom.altitude_agl)
            return dz / m.climb_ms + dz / m.sink_ms + L / ctx.vt, L
    return _turn(ctx, prev, q)


def _sub(p: Piece, f0: float, f1: float) -> Piece:
    return Piece(model=p.model, comp=p.comp, col=p.col, row=p.row,
                 ax=p.ax + (p.bx - p.ax) * f0, ay=p.ay + (p.by - p.ay) * f0,
                 bx=p.ax + (p.bx - p.ax) * f1, by=p.ay + (p.by - p.ay) * f1, optional=p.optional)


def order_nearest(pieces: List[Piece], sites_xy: np.ndarray) -> List[Piece]:
    """Порядок разрозненных галсов (добор кромок у зон) цепочкой «ближайший сосед» с возможным разворотом галса:
    вылет собирает соседние полосы, а не прыгает между зонами в порядке змейки по всей области."""
    if len(pieces) < 3:
        return list(pieces)
    left = list(pieces)
    A = np.array([[p.ax, p.ay, p.bx, p.by] for p in left])
    used = np.zeros(len(left), dtype=bool)
    d_site = np.minimum(np.hypot(A[:, 0, None] - sites_xy[None, :, 0], A[:, 1, None] - sites_xy[None, :, 1]).min(1),
                        np.hypot(A[:, 2, None] - sites_xy[None, :, 0], A[:, 3, None] - sites_xy[None, :, 1]).min(1))
    out: List[Piece] = []
    cur = None
    while not used.all():
        if cur is None:
            i = int(np.argmin(np.where(used, np.inf, d_site)))
            da = np.hypot(A[i, 0] - sites_xy[:, 0], A[i, 1] - sites_xy[:, 1]).min()
            db = np.hypot(A[i, 2] - sites_xy[:, 0], A[i, 3] - sites_xy[:, 1]).min()
            rev = db < da
        else:
            da = np.hypot(A[:, 0] - cur[0], A[:, 1] - cur[1])
            db = np.hypot(A[:, 2] - cur[0], A[:, 3] - cur[1])
            d = np.where(used, np.inf, np.minimum(da, db))
            i = int(np.argmin(d))
            rev = db[i] < da[i]
        used[i] = True
        p = left[i]
        if rev:
            p = Piece(model=p.model, comp=p.comp, col=p.col, row=p.row, ax=p.bx, ay=p.by, bx=p.ax, by=p.ay, optional=p.optional)
        out.append(p)
        cur = (p.bx, p.by)
    return out


def clip_to_disks(pieces: List[Piece], S: np.ndarray, R: float) -> Tuple[List[Piece], List[tuple]]:
    """Обрезка галсов кругами радиуса R вокруг площадок (зона прямой видимости). Вне кругов — невыполнимо."""
    keep, bad = [], []
    for p in pieces:
        L = p.length
        if L < 1e-6:
            continue
        dx, dy = (p.bx - p.ax) / L, (p.by - p.ay) / L
        ivs = []
        for sx, sy in S:
            # |a + t·d − s|² = R², t ∈ [0, L]
            fx, fy = p.ax - sx, p.ay - sy
            b = fx * dx + fy * dy
            c = fx * fx + fy * fy - R * R
            disc = b * b - c
            if disc <= 0:
                continue
            r = math.sqrt(disc)
            t0, t1 = max(0.0, -b - r), min(L, -b + r)
            if t1 > t0:
                ivs.append((t0, t1))
        ivs.sort()
        merged = []
        for a, b2 in ivs:
            if merged and a <= merged[-1][1] + 1e-6:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b2))
            else:
                merged.append((a, b2))
        cur = 0.0
        for a, b2 in merged:
            if a - cur > 1.0:
                bad.append((p.ax + dx * cur, p.ay + dy * cur, a - cur, p.optional))
            if b2 - a > 1.0:
                keep.append(_sub(p, a / L, b2 / L))
            cur = b2
        if L - cur > 1.0:
            bad.append((p.ax + dx * cur, p.ay + dy * cur, L - cur, p.optional))
    return keep, bad


def pack(ctx: ModelCtx, pieces: List[Piece], layer_agl: float) -> Tuple[List[Sortie], List[tuple]]:
    ft, fd = fixed_overhead(ctx, layer_agl)
    S = ctx.sites_xy
    R = ctx.max_site_dist
    pre_bad: List[tuple] = []
    if R < math.inf:
        pieces, pre_bad = clip_to_disks(pieces, S, R * 0.98)
        # прямая видимость: куски галсов группируются по кругу своей площадки (порядок змейки внутри группы) —
        # иначе соседние в очереди куски принадлежат разным кругам и вылет закрывается после первого же
        def home(p):
            mx, my = (p.ax + p.bx) / 2, (p.ay + p.by) / 2
            return int(np.argmin(np.hypot(S[:, 0] - mx, S[:, 1] - my)))
        pieces = [p for _, _, p in sorted((home(p), i, p) for i, p in enumerate(pieces))]
    queue = deque(pieces)
    out: List[Sortie] = []
    bad: List[tuple] = []
    Bt, Bm = ctx.budget_s * ctx.est_margin, ctx.budget_m * ctx.est_margin

    def fits(t_cur, m_cur, p: Piece, f: float, si: int, extra_t=0.0, extra_m=0.0):
        ex, ey = p.ax + (p.bx - p.ax) * f, p.ay + (p.by - p.ay) * f
        L = p.length * f
        if R < math.inf and (_d(ex, ey, S[si, 0], S[si, 1]) > R or _d(p.ax, p.ay, S[si, 0], S[si, 1]) > R):
            return False, 0.0, 0.0
        bt, back = ctx.site_leg(si, ex, ey, outbound=False)
        t = t_cur + extra_t + L / ctx.vs_of(p.comp) + bt
        m = m_cur + extra_m + L + back
        return t <= Bt and m <= Bm, t, m

    def max_fraction(t_cur, m_cur, p, si, extra_t, extra_m):
        ok, _, _ = fits(t_cur, m_cur, p, 1.0, si, extra_t, extra_m)
        if ok:
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(22):
            mid = (lo + hi) / 2
            if fits(t_cur, m_cur, p, mid, si, extra_t, extra_m)[0]:
                lo = mid
            else:
                hi = mid
        return lo

    while queue:
        p = queue.popleft()
        if p.length < 1.0:
            continue
        min_len = min(p.length, 60.0)
        f = 0.0
        for si in np.argsort(np.hypot(S[:, 0] - p.ax, S[:, 1] - p.ay)):
            si = int(si)
            t0_out, d0 = ctx.site_leg(si, p.ax, p.ay, outbound=True)
            if not math.isfinite(t0_out):
                continue
            t_cur, m_cur = ft + t0_out, fd + d0
            f = max_fraction(t_cur, m_cur, p, si, 0.0, 0.0)
            if f * p.length >= min_len - 1e-6:
                break
        if f * p.length < min_len - 1e-6:
            bad.append((p.ax, p.ay, p.length, p.optional))
            continue
        cur = [p if f >= 1.0 else _sub(p, 0.0, f)]
        if f < 1.0:
            queue.appendleft(_sub(p, f, 1.0))
        t_cur += cur[0].length / ctx.vs_of(p.comp)
        m_cur += cur[0].length
        while queue and f >= 1.0:
            q = queue[0]
            try:
                tt, tm = _conn(ctx, cur[-1], q, layer_agl)
            except RoutingError:
                break  # finish this sortie; the next piece can use another site
            fq = max_fraction(t_cur, m_cur, q, si, tt, tm)
            if fq >= 1.0:
                queue.popleft()
                cur.append(q)
                t_cur += tt + q.length / ctx.vs_of(q.comp)
                m_cur += tm + q.length
                continue
            if fq * q.length >= max(200.0, 0.2 * q.length):
                queue.popleft()
                cur.append(_sub(q, 0.0, fq))
                queue.appendleft(_sub(q, fq, 1.0))
                t_cur += tt + fq * q.length / ctx.vs_of(q.comp)
                m_cur += tm + fq * q.length
            break
        # лучшая площадка для вылета (взлет и посадка на одной площадке)
        a, b = cur[0], cur[-1]
        legs = [(ctx.site_leg(i, a.ax, a.ay, True), ctx.site_leg(i, b.bx, b.by, False)) for i in range(len(S))]
        tsum = [o[0] + r[0] for o, r in legs]
        dsum = np.array([o[1] + r[1] for o, r in legs])
        body_t = t_cur - t0_out - ft
        body_m = m_cur - d0 - fd
        best = si
        for i in np.argsort(tsum):
            if R < math.inf and max(max(_d(q.ax, q.ay, S[i, 0], S[i, 1]), _d(q.bx, q.by, S[i, 0], S[i, 1])) for q in cur) > R:
                continue  # прямая видимость: все точки вылета в радиусе от площадки
            if ft + body_t + tsum[i] <= Bt and fd + body_m + dsum[i] <= Bm:
                best = int(i)
                break
        est_t = ft + body_t + tsum[best]
        est_m = fd + body_m + float(dsum[best])
        out.append(Sortie(model=ctx.key, pieces=cur, site=best, est_s=est_t, est_m=est_m))
    return out, bad + pre_bad


# ---------------------------------------------------------------- траектории

PHASES = ("ground", "takeoff", "transit_out", "descent", "survey", "climb", "transit_back", "landing")


class TrajBuilder:
    def __init__(self, x, y, z, phase="ground"):
        self.P = [(float(x), float(y), float(z))]
        self.T = [0.0]
        self.PH = [phase]
        self.dist = 0.0
        self.marks = []  # (index, label)

    @property
    def last(self):
        return self.P[-1]

    def move(self, x, y, z, speed, phase, vrate=None):
        x0, y0, z0 = self.P[-1]
        dh = math.hypot(x - x0, y - y0)
        dt = dh / speed if speed > 0 else 0.0
        if vrate:
            dt = max(dt, abs(z - z0) / vrate)
        if dh < 1e-6 and abs(z - z0) < 1e-6:
            return
        self.P.append((float(x), float(y), float(z)))
        self.T.append(self.T[-1] + dt)
        self.PH.append(phase)
        self.dist += math.sqrt(dh * dh + (z - z0) ** 2)

    def hold(self, dt, phase):
        if dt <= 0:
            return
        self.P.append(self.P[-1])
        self.T.append(self.T[-1] + dt)
        self.PH.append(phase)

    def mark(self, label):
        self.marks.append((len(self.P) - 1, label))


def _simplify_profile(d: np.ndarray, z: np.ndarray, tol: float) -> np.ndarray:
    """Дуглас–Пекер по профилю (расстояние, высота): оставляем точки, где рельеф меняется > tol."""
    n = len(d)
    if n <= 2:
        return np.arange(n)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        seg = z[i] + (z[j] - z[i]) * (d[i + 1:j] - d[i]) / max(d[j] - d[i], 1e-9)
        err = np.abs(z[i + 1:j] - seg)
        k = int(np.argmax(err))
        if err[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return np.nonzero(keep)[0]


def _terrain_line(ctx: ModelCtx, ax, ay, bx, by, agl, tol=3.0):
    """Точки линии с огибанием рельефа на высоте agl над землей (с кэшем: те же галсы при ремонте вылета)."""
    key = (round(ax, 1), round(ay, 1), round(bx, 1), round(by, 1), round(agl, 1))
    hit = ctx._tline.get(key)
    if hit is not None:
        return hit
    if len(ctx._tline) > 200000:
        ctx._tline.clear()
    res = _terrain_line_raw(ctx, ax, ay, bx, by, agl, tol)
    ctx._tline[key] = res
    return res


def _terrain_line_raw(ctx: ModelCtx, ax, ay, bx, by, agl, tol=3.0):
    L = _d(ax, ay, bx, by)
    n = max(1, int(math.ceil(L / min(10.0, ctx.terrain_step))))
    s = np.linspace(0, 1, n + 1)
    xs, ys = ax + (bx - ax) * s, ay + (by - ay) * s
    zs = ctx.terrain.h(xs, ys) + agl
    idx = _simplify_profile(s * L, zs, min(tol, 0.5)) if ctx.terrain.enabled else np.array([0, n])
    return [(float(xs[i]), float(ys[i]), float(zs[i])) for i in idx]


def _obst_top(ctx: ModelCtx, pts) -> float:
    """Минимальная безопасная абсолютная высота над препятствиями у ломаной (верх + вертикальный запас)."""
    if ctx.obst_tops is None:
        return -math.inf
    tree, tops = ctx.obst_tops
    pts = np.asarray(pts, dtype=float)
    g = shapely.LineString(pts) if len(pts) > 1 else shapely.Point(pts[0])
    idx = tree.query(g, predicate="intersects")
    return float(tops[idx].max()) if len(idx) else -math.inf


def _chunks(ctx: ModelCtx, a, b, layer_agl, depth=0):
    """Участки постоянной высоты: max рельефа + эшелон, но не выше потолка AGL над минимумом рельефа участка.

    Рельеф опрашивается с шагом 10 м (провалы между точками опроса при потолке 150 м критичны); участок дробится,
    пока высота не уложится под потолок. Невозможный коридор отклоняется:
    ограничение потолка не должно опускать маршрут под поверхность земли.
    """
    if not ctx.terrain.enabled:
        return [(a, b, layer_agl)]
    L = float(np.hypot(*(b - a)))
    if not ctx.strict_ceiling:
        # коридор 700 м: потолок почти не ограничивает — участки по шагу ЦМР, над минимумом рельефа участка
        n = max(2, int(math.ceil(L / ctx.terrain_step)) + 1)
        s = np.linspace(0, 1, n)
        hs = ctx.terrain.h(a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s)
        hi, lo = float(hs.max()), float(hs.min())
        z = max(hi + layer_agl, _obst_top(ctx, [a, b]) + 1.0)
        if z - lo <= ctx.agl_cap - 5 or L < 60 or depth > 8:
            if z - lo > ctx.agl_cap - 5:
                safe = max(hi + max(30.0, ctx.model.min_alt_agl_m), _obst_top(ctx, [a, b]))
                z = lo + ctx.agl_cap - 5
                if z < safe:
                    raise RoutingError("Перепад рельефа/препятствия не оставляют безопасного коридора под потолком; измените маршрут или площадку")
            return [(a, b, z)]
        mid = (a + b) / 2
        return _chunks(ctx, a, mid, layer_agl, depth + 1) + _chunks(ctx, mid, b, layer_agl, depth + 1)
    n = max(2, int(math.ceil(L / min(10.0, ctx.terrain_step))) + 1)
    s = np.linspace(0, 1, n)
    hs = ctx.terrain.h(a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s)
    hi, lo = float(hs.max()), float(hs.min())
    cap = ctx.agl_cap - 8
    # под потолком участок может идти ниже эшелона — тогда он обязан пройти над препятствиями, которые
    # маршрутизатор перелета не обходил (они ниже эшелона)
    safe = max(hi + 30.0, _obst_top(ctx, [a, b]))
    z = max(hi + layer_agl, safe)
    if z - lo <= cap:
        return [(a, b, z)]
    if L < 15 or depth > 12:
        if safe > lo + cap:
            raise RoutingError("Перепад рельефа/препятствия не оставляют безопасного коридора под потолком; измените маршрут или площадку")
        return [(a, b, lo + cap)]
    if lo + cap >= safe and depth > 3:
        return [(a, b, lo + cap)]
    mid = (a + b) / 2
    return _chunks(ctx, a, mid, layer_agl, depth + 1) + _chunks(ctx, mid, b, layer_agl, depth + 1)


def _transit(tb: TrajBuilder, ctx: ModelCtx, path, layer_agl, phase, chunk_m=1500.0):
    """Перелет по маршруту в обход зон; высота кусочно-постоянная (огибание рельефа на эшелоне)."""
    m = ctx.model
    pts = np.asarray(path, dtype=float)
    # A plane exits a spiral away from its centre. Route from its actual
    # position, otherwise the first segment cuts across the obstacle buffer.
    if np.linalg.norm(pts[0] - np.asarray(tb.last[:2])) > 0.1:
        pts = np.asarray(ctx.router_transit.path(tb.last[:2], pts[-1]), dtype=float)
    # делим ломаную на участки ~chunk_m, дробим там, где перепад рельефа выводит за потолок AGL
    segs = []
    for a, b in zip(pts[:-1], pts[1:]):
        L = float(np.hypot(*(b - a)))
        n = max(1, int(math.ceil(L / chunk_m)))
        for k in range(n):
            segs.extend(_chunks(ctx, a + (b - a) * k / n, a + (b - a) * (k + 1) / n, layer_agl))
    for a, b, z in segs:
        x0, y0, z0 = tb.last
        safe = _obst_top(ctx, [(x0, y0), b]) + 1.0
        if m.is_plane and z0 < safe:
            r = m.turn_radius_m
            cx, cy = x0 - r, y0
            circle = shapely.Point(cx, cy).buffer(r).boundary
            if not ctx.router_transit.empty and ctx.router_transit.core.intersects(circle):
                raise RoutingError("Нет безопасного места для набора высоты перед препятствием")
            _spiral(tb, cx, cy, r, max(z, safe), m.climb_ms, ctx.vt, phase, full_turns=True)
            x0, y0, z0 = tb.last
        if abs(z - z0) > 0.5:
            if m.is_plane:
                pass  # самолет меняет высоту по ходу участка (ограничение скороподъемности — в move)
            else:
                tb.move(x0, y0, z, 1.0, phase, m.climb_ms if z > z0 else m.sink_ms)
        x0, y0, _ = tb.last
        tb.move(b[0], b[1], z, ctx.vt_dir(x0, y0, b[0], b[1]), phase, (m.climb_ms if z > tb.last[2] else m.sink_ms) if m.is_plane else None)


def _spiral(tb: TrajBuilder, cx, cy, r, z_to, rate, v, phase, full_turns=False):
    x0, y0, z0 = tb.last
    ang = math.atan2(y0 - cy, x0 - cx)
    if abs(z_to - z0) < 1:
        return
    total_t = abs(z_to - z0) / rate
    omega = v / r
    if full_turns:
        period = 2 * math.pi / omega
        total_t = math.ceil(total_t / period) * period
    total_ang = omega * total_t
    n = max(4, int(total_ang / math.radians(20)))
    for k in range(1, n + 1):
        a = ang + total_ang * k / n
        z = z0 + (z_to - z0) * k / n
        tb.P.append((cx + r * math.cos(a), cy + r * math.sin(a), z))
        tb.T.append(tb.T[-1] + total_t / n)
        tb.PH.append(phase)
        tb.dist += r * total_ang / n


def _plane_turn(tb: TrajBuilder, ctx: ModelCtx, prev: Piece, q: Piece, agl):
    """Разворот самолета между галсами: вынос за край + дуга (защитная зона маневра)."""
    R = ctx.model.turn_radius_m
    L1 = max(prev.length, 1e-6)
    L2 = max(q.length, 1e-6)
    d1 = ((prev.bx - prev.ax) / L1, (prev.by - prev.ay) / L1)
    d2 = ((q.bx - q.ax) / L2, (q.by - q.ay) / L2)
    p1 = (prev.bx + d1[0] * R, prev.by + d1[1] * R)
    p2 = (q.ax - d2[0] * R, q.ay - d2[1] * R)
    cx, cy = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    r = max(R, _d(*p1, *p2) / 2)
    a1 = math.atan2(p1[1] - cy, p1[0] - cx)
    a2 = math.atan2(p2[1] - cy, p2[0] - cx)
    # дуга, проходящая через сторону выноса (направление d1)
    mid_dir = math.atan2(d1[1], d1[0])
    sweep = (a2 - a1) % (2 * math.pi)
    mid_ccw = a1 + sweep / 2
    if math.cos(mid_ccw - mid_dir) < 0:
        sweep = sweep - 2 * math.pi
    if abs(sweep) < 1e-3:
        sweep = math.pi
    pts = [p1]
    n = 10
    for k in range(1, n):
        a = a1 + sweep * k / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append(p2)
    pts.append((q.ax, q.ay))
    router = ctx.router_survey
    blocked = (not router.empty) and router.core.intersects(shapely.LineString(pts))
    if _d(*p1, *p2) > 3 * R or blocked:
        # дальний переход (другая полоса/колонка) или дуга задевает зону: вынос, перелет в обход, заход на галс
        mid = router.path(p1, p2)
        pts = [p1] + [tuple(x) for x in mid[1:-1]] + [p2, (q.ax, q.ay)]
    # огибание рельефа на всем переходе (дуги и дальние переходы), а не только в узловых точках
    x0, y0, _ = tb.last
    for (xa, ya), (xb, yb) in zip([(x0, y0)] + pts[:-1], pts):
        for (x, y, z) in _terrain_line(ctx, xa, ya, xb, yb, agl)[1:]:
            tb.move(x, y, z, ctx.vs_of(q.comp), "survey", ctx.model.climb_ms if z > tb.last[2] else ctx.model.sink_ms)


def build_trajectory(ctx: ModelCtx, sortie: Sortie, site_xy, layer_agl: float) -> TrajBuilder:
    m, H = ctx.model, ctx.geom.altitude_agl
    sx, sy = float(site_xy[0]), float(site_xy[1])
    g0 = float(ctx.terrain.h(sx, sy)[0])
    tb = TrajBuilder(sx, sy, g0, "ground")
    tb.mark("Взлет")
    P = sortie.pieces
    a0, bN = P[0], P[-1]
    R = m.turn_radius_m
    if m.is_plane:
        L0 = max(a0.length, 1e-6)
        d0 = ((a0.bx - a0.ax) / L0, (a0.by - a0.ay) / L0)
        entry = (a0.ax - d0[0] * R, a0.ay - d0[1] * R)
        LN = max(bN.length, 1e-6)
        dN = ((bN.bx - bN.ax) / LN, (bN.by - bN.ay) / LN)
        exitp = (bN.bx + dN[0] * R, bN.by + dN[1] * R)
    else:
        entry, exitp = (a0.ax, a0.ay), (bN.bx, bN.by)

    # взлет и набор эшелона
    path_out = ctx.router_transit.path((sx, sy), entry)
    z_out = ctx.terrain.max_along(path_out[:2], ctx.terrain_step) + layer_agl
    z_out = min(z_out, g0 + ctx.agl_cap - 8)  # потолок над площадкой действует во всех режимах
    if m.is_plane:
        tb.hold(m.launch_s, "ground")
        tb.move(sx + R, sy, g0 + 15, ctx.vt, "takeoff", m.climb_ms)
        _spiral(tb, sx, sy, R, z_out, m.climb_ms if z_out > tb.last[2] else m.sink_ms, ctx.vt, "takeoff")
    else:
        tb.move(sx, sy, z_out, 1.0, "takeoff", m.climb_ms)
    tb.mark("Эшелон перелета")
    _transit(tb, ctx, path_out, layer_agl, "transit_out")
    # снижение на рабочую высоту
    z_entry = float(ctx.terrain.h(*entry)[0]) + H
    if m.is_plane:
        # спираль снижения над началом галса (внутри участка, т.е. вне буфера запретных зон), затем выход на галс
        cx, cy = a0.ax + d0[0] * R, a0.ay + d0[1] * R
        tb.move(cx + R, cy, tb.last[2], ctx.vt_dir(tb.last[0], tb.last[1], cx + R, cy), "descent")
        _spiral(tb, cx, cy, R, z_entry, m.climb_ms if z_entry > tb.last[2] else m.sink_ms, ctx.vt, "descent")
        tb.move(entry[0], entry[1], z_entry, ctx.vt, "descent")
    else:
        tb.move(entry[0], entry[1], z_entry, 1.0, "descent", m.climb_ms if z_entry > tb.last[2] else m.sink_ms)
    # съемка
    prev = None
    for q in P:
        vq = ctx.vs_of(q.comp)
        if prev is not None:
            same_row = prev.comp == q.comp and prev.row == q.row
            start, end = (prev.bx, prev.by), (q.ax, q.ay)
            conn = [start, end]
            if not ctx.router_survey.empty and not ctx.router_survey._clear(start, end):
                # переход заблокирован (препятствие/запретная зона на рабочей высоте):
                # подъем на свой эшелон, перелет (в обход зон при необходимости), снижение
                hop = ctx.router_transit.path((prev.bx, prev.by), (q.ax, q.ay))
                z_hop = ctx.terrain.max_along(hop, ctx.terrain_step) + layer_agl
                if ctx.strict_ceiling or not m.is_plane:
                    z_hop = min(z_hop, float(ctx.terrain.h(prev.bx, prev.by)[0]) + ctx.agl_cap - 5,
                                float(ctx.terrain.h(q.ax, q.ay)[0]) + ctx.agl_cap - 5)
                    # безопасность важнее потолка: над рельефом и препятствиями на переходе
                    z_hop = max(z_hop, ctx.terrain.max_along(hop, ctx.terrain_step) + 30.0, _obst_top(ctx, hop))
                rate_up, rate_dn = m.climb_ms, m.sink_ms
                x0, y0, _ = tb.last
                if m.is_plane:
                    z_hop = min(z_hop, float(ctx.terrain.h(x0, y0)[0]) + ctx.agl_cap - 8)
                    tb.move(x0, y0, z_hop, ctx.vt, "climb", m.climb_ms if z_hop > tb.last[2] else m.sink_ms)
                    _transit(tb, ctx, hop, layer_agl, "transit_out")
                else:
                    # коптер: кусочно-постоянная высота, как на перелете — поднимается только там, где нужно
                    # (над возвышенностью или препятствием), и не выходит за потолок над низинами
                    _transit(tb, ctx, hop, layer_agl, "transit_out")
                tb.move(q.ax, q.ay, float(ctx.terrain.h(q.ax, q.ay)[0]) + H, ctx.vt if m.is_plane else 1.0, "descent", rate_dn)
            elif m.is_plane and not same_row:
                _plane_turn(tb, ctx, prev, q, H)
            else:
                if not m.is_plane and not same_row:
                    tb.hold(vq / max(m.accel_ms2, 0.5), "survey")
                for (x0, y0), (x1, y1) in zip(conn[:-1], conn[1:]):
                    for (x, y, z) in _terrain_line(ctx, x0, y0, x1, y1, H)[1:]:
                        tb.move(x, y, z, vq, "survey", m.climb_ms if z > tb.last[2] else m.sink_ms)
                if not m.is_plane and not same_row:
                    tb.hold(vq / max(m.accel_ms2, 0.5), "survey")
        else:
            tb.move(q.ax, q.ay, float(ctx.terrain.h(q.ax, q.ay)[0]) + H, vq, "survey", m.climb_ms if float(ctx.terrain.h(q.ax, q.ay)[0]) + H > tb.last[2] else m.sink_ms)
        tb.mark("Начало съемки" if prev is None else None)
        for (x, y, z) in _terrain_line(ctx, q.ax, q.ay, q.bx, q.by, H)[1:]:
            tb.move(x, y, z, vq, "survey", m.climb_ms if z > tb.last[2] else m.sink_ms)
        prev = q
    tb.mark("Конец съемки")
    # выход и набор
    path_back = ctx.router_transit.path(exitp, (sx, sy))
    z_back = ctx.terrain.max_along(path_back[:2], ctx.terrain_step) + layer_agl
    z_back = min(z_back, float(ctx.terrain.h(*exitp)[0]) + ctx.agl_cap - 8)
    if m.is_plane:
        z_exit = float(ctx.terrain.h(*exitp)[0]) + H
        tb.move(exitp[0], exitp[1], z_exit, ctx.vt, "climb", m.climb_ms if z_exit > tb.last[2] else m.sink_ms)
        cx, cy = bN.bx, bN.by  # exitp лежит на окружности радиуса R, а не 2R
        _spiral(tb, cx, cy, R, z_back, m.climb_ms if z_back > tb.last[2] else m.sink_ms, ctx.vt, "climb")
    else:
        tb.move(exitp[0], exitp[1], z_back, 1.0, "climb", m.climb_ms if z_back > tb.last[2] else m.sink_ms)
    _transit(tb, ctx, path_back, layer_agl, "transit_back")
    # посадка
    tb.mark("Заход на посадку")
    if m.is_plane:
        # The site is on the circle. Complete whole turns so parachute release is
        # exactly above it; the old circle centred on the site landed R metres away.
        _spiral(tb, sx - R, sy, R, g0 + m.parachute_alt_m, m.sink_ms, ctx.vt, "landing", full_turns=True)
        x, y, _ = tb.last
        tb.move(x, y, g0, 1.0, "landing", m.parachute_ms)
    else:
        tb.move(sx, sy, g0, 1.0, "landing", m.sink_ms)
    tb.mark("Посадка")
    tb.marks = [(i, l) for i, l in tb.marks if l]
    from .energy import energy_equiv_s
    tb.energy_s = energy_equiv_s(tb.P, tb.T, tb.PH, ctx.wind_t, m.is_plane, ctx.energy_mode, m.accel_ms2)
    return tb


def survey_length(sortie: Sortie) -> float:
    return float(sum(p.length for p in sortie.pieces))
