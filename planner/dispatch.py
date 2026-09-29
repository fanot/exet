"""Расписание вылетов и 4D-разведение бортов.

Назначение: список-расписание (list scheduling) по правилу «самое раннее возможное начало».
Ограничения старта вылета на борт:
  * борт свободен (посадка + обслуживание: замена АКБ, выгрузка данных, для 201 — укладка парашюта);
  * есть заряженная АКБ (у борта B комплектов, каждый заряжается charge_min после посадки);
  * переезд расчета между площадками (расстояние·1.3 / скорость авто + сборка);
  * вылет целиком укладывается в светлое окно (высота Солнца ≥ порога), иначе переносится.

Разведение (гарантия отсутствия опасных сближений):
  1. Каждому борту выделен свой эшелон перелета выше всех съемочных высот
     (база = max H_съемки + Δ, шаг Δ = 30 м) — перелеты не пересекают съемку и друг друга по высоте.
  2. Съемочные участки разных вылетов не пересекаются (ячейки/колонки).
  3. Остаточные конфликты (взлет/посадка на одной площадке, набор/снижение сквозь чужой эшелон,
     сход колонок) ищутся 4D-проверкой: траектории дискретизируются с шагом 1 с, конфликт —
     одновременно горизонтальное расстояние < R_разв (+ путь за шаг) и вертикальное < 30 м.
     Конфликт снимается задержкой старта (удержание на земле) — это всегда разрешимо и не требует
     маневров в воздухе.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .sortie import ModelCtx, Sortie, TrajBuilder, _sub, build_trajectory, pack
from .routing import RoutingError


@dataclass
class DroneState:
    id: str
    model: str
    title: str
    layer: float
    batteries: List[float]
    charge_s: float
    turnaround_s: float
    free_at: float
    site: Optional[int] = None
    flight_s: float = 0.0
    n_sorties: int = 0


@dataclass
class Assigned:
    sortie: Sortie
    drone: str
    start: float
    end: float
    traj: Optional[TrajBuilder] = None
    delay_s: float = 0.0


TAIL_JOIN_M = 2500.0  # хвост после ремонта присоединяется к следующему вылету, если его начало ближе


class SchedulingError(ValueError):
    """No feasible departure in the planning horizon."""


def _d2(ax, ay, bx, by) -> float:
    return math.hypot(ax - bx, ay - by)


def daylight_fit(start: float, dur: float, windows: Optional[List[Tuple[float, float]]]) -> float:
    if windows is None:
        return start
    for a, b in windows:
        s = math.ceil(max(start, a))
        if s + dur <= b:
            return s
    raise SchedulingError("Вылет не помещается ни в одно светлое окно; измените дату или параметры съемки")


def reloc_s(sites_xy, a: Optional[int], b: int, glob) -> float:
    if a is None or a == b:
        return 0.0
    d = float(np.hypot(*(sites_xy[a] - sites_xy[b])))
    return d * 1.3 / (glob["ground_speed_kmh"] / 3.6) + glob["relocation_pack_min"] * 60


def fit_windows(t: float, dur: float, windows, blocked) -> float:
    """Ближайший старт ≥ t: вылет в светлом окне и вне окон действия задеваемых временных зон."""
    for _ in range(100):
        t2 = daylight_fit(t, dur, windows)
        moved = False
        for a, b in blocked or ():
            if t2 < b and t2 + dur > a:
                if b >= 253402300799:
                    raise SchedulingError("Маршрут пересекает бессрочную действующую запретную зону")
                t2 = b + 60.0
                moved = True
        if not moved:
            return t2
        t = t2
    raise SchedulingError("Не найдено окно вылета вне временных ограничений")


def earliest_start(dr: DroneState, s: Sortie, dur: float, sites_xy, glob, windows) -> float:
    t = dr.free_at + reloc_s(sites_xy, dr.site, s.site, glob)
    t = max(t, min(dr.batteries))
    return fit_windows(t, dur, windows, s.blocked)


def _commit(dr: DroneState, s: Sortie, start: float, end: float):
    i = int(np.argmin(dr.batteries))
    dr.batteries[i] = end + dr.charge_s
    dr.free_at = end + dr.turnaround_s
    dr.site = s.site
    dr.flight_s += end - start
    dr.n_sorties += 1


LOOKAHEAD = 24  # сколько ближайших вылетов очереди рассматривается при выборе следующего для борта


def _pick(q, d: DroneState, sites_xy, glob, windows, dur_of):
    """Следующий вылет для борта среди первых LOOKAHEAD в очереди: самый ранний старт, при равенстве — с текущей
    площадки борта. Без этого на задании с десятками площадок борт переезжал почти после каждого вылета
    (переезд — дорога плюс 15 мин сборки)."""
    best = None
    for i in range(min(LOOKAHEAD, len(q))):
        s = q[i]
        t = earliest_start(d, s, dur_of(s), sites_xy, glob, windows)
        key = (t + (0.0 if d.site is None or d.site == s.site else 1.0) + i * 1e-3, i)
        if best is None or key < best[0]:
            best = (key, i, t)
    return best[1], best[2]


def schedule_estimate(sorties: Dict[str, List[Sortie]], drones: List[DroneState], sites_xy, glob, windows):
    """Быстрая оценка расписания для поиска (без траекторий и разведения)."""
    by_model: Dict[str, List[DroneState]] = {}
    for d in drones:
        by_model.setdefault(d.model, []).append(d)
    end = 0.0
    flight = 0.0
    for m, lst in sorties.items():
        ds = by_model.get(m, [])
        if not ds:
            continue
        q = deque(lst)
        while q:
            best = None
            for d in ds:
                i, t = _pick(q, d, sites_xy, glob, windows, lambda s: s.est_s)
                if best is None or t < best[0] - 1e-6:
                    best = (t, d, i)
            bt, d, i = best
            s = q[i]
            del q[i]
            _commit(d, s, bt, bt + s.est_s)
            end = max(end, bt + s.est_s)
            flight += s.est_s
    return end, flight


# ------------------------------------------------------------ 4D разведение

@dataclass
class Track:
    drone: str
    t0: int
    X: np.ndarray
    Y: np.ndarray
    Z: np.ndarray
    A: np.ndarray  # в воздухе
    sep: float
    vmax: float


def sample_track(tb: TrajBuilder, start: float, terrain, drone: str, sep: float, vmax: float) -> Track:
    T = np.asarray(tb.T) + start
    P = np.asarray(tb.P)
    t0 = int(math.ceil(T[0]))
    t1 = int(math.floor(T[-1]))
    ts = np.arange(t0, t1 + 1, dtype=float)
    X = np.interp(ts, T, P[:, 0])
    Y = np.interp(ts, T, P[:, 1])
    Z = np.interp(ts, T, P[:, 2])
    agl = Z - terrain.h(X, Y)
    # «в воздухе» — с 1 м, строже независимой проверки (3 м): запас на расхождение выборок ЦМР у земли
    return Track(drone=drone, t0=t0, X=X, Y=Y, Z=Z, A=agl > 1.0, sep=sep, vmax=vmax)


def shift_track(tr: Track, dt: int) -> Track:
    return Track(tr.drone, tr.t0 + dt, tr.X, tr.Y, tr.Z, tr.A, tr.sep, tr.vmax)


def first_conflict(new: Track, accepted: List[Track], vsep: float):
    n1 = new.t0 + len(new.X)
    for tr in accepted:
        if tr.t0 >= n1 or tr.t0 + len(tr.X) <= new.t0:
            continue
        if tr.drone == new.drone:
            continue
        a1 = tr.t0 + len(tr.X)
        i0, i1 = max(new.t0, tr.t0), min(n1, a1)
        if i1 <= i0:
            continue
        sn = slice(i0 - new.t0, i1 - new.t0)
        sa = slice(i0 - tr.t0, i1 - tr.t0)
        hs = max(new.sep, tr.sep) + (new.vmax + tr.vmax) * 0.5
        dh = np.hypot(new.X[sn] - tr.X[sa], new.Y[sn] - tr.Y[sa])
        dz = np.abs(new.Z[sn] - tr.Z[sa])
        bad = (dh < hs) & (dz < vsep) & new.A[sn] & tr.A[sa]
        if bad.any():
            k = int(np.argmax(bad))
            return i0 + k, tr.drone, float(dh[k]), float(dz[k])
    return None


def dispatch(ctxs: Dict[str, ModelCtx], sorties: Dict[str, List[Sortie]], drones: List[DroneState], sites_xy,
             glob, windows, terrain, progress=None):
    """Финальное расписание: точные траектории, ремонт по энергии, разведение задержками."""
    pending = {m: deque(lst) for m, lst in sorties.items() if lst}
    by_model: Dict[str, List[DroneState]] = {}
    for d in drones:
        by_model.setdefault(d.model, []).append(d)
    accepted: List[Track] = []
    out: List[Assigned] = []
    vsep = glob["vertical_sep_m"] + 3.0  # запас на дискретизацию и округление
    total = sum(len(v) for v in pending.values())
    conflicts_resolved = 0
    daylight_violations = 0
    unreachable = []
    while any(pending.values()):
        best = None
        for m, q in pending.items():
            if not q:
                continue
            for d in by_model[m]:
                i, t = _pick(q, d, sites_xy, glob, windows, lambda s: s.est_s)
                if best is None or t < best[0] - 1e-6:
                    best = (t, m, d, i)
        _, m, d, i = best
        s = pending[m][i]
        del pending[m][i]
        ctx = ctxs[m]
        def trajectory():
            # A failed leg is local to a site/sortie. Try another usable site
            # before rejecting its pieces or the complete fleet allocation.
            original_site = s.site
            p0 = s.pieces[0]
            order = [original_site] + [int(j) for j in np.argsort(np.hypot(sites_xy[:, 0] - p0.ax, sites_xy[:, 1] - p0.ay))
                                       if j != original_site]
            error = None
            for site in order:
                if ctx.max_site_dist < math.inf:
                    sx, sy = sites_xy[site]
                    if any(max(_d2(p.ax, p.ay, sx, sy), _d2(p.bx, p.by, sx, sy)) > ctx.max_site_dist for p in s.pieces):
                        continue
                s.site = site
                try:
                    trajectory_result = build_trajectory(ctx, s, sites_xy[site], d.layer)
                    trajectory_result.site_index = site
                    return trajectory_result
                except RoutingError as exc:
                    error = exc
            s.site = original_site
            raise error or RoutingError("Нет доступной площадки для вылета")

        try:
            tb = trajectory()
        except RoutingError:
            if len(s.pieces) > 1:
                mid = len(s.pieces) // 2
                for pieces in (s.pieces[mid:], s.pieces[:mid]):
                    pending[m].appendleft(Sortie(m, pieces, s.site, est_s=s.est_s,
                                                blocked=list(s.blocked), origin=s.origin))
                total += 1
            else:
                p = s.pieces[0]
                if not p.optional:
                    unreachable.append((p.ax, p.ay, m, p.length))
            if progress:
                progress(len(out) / max(total, 1))
            continue
        # ремонт по энергии: если точная траектория длиннее бюджета — отрезаем хвост в новый вылет
        guard = 0
        dropped = False
        while tb.energy_s > ctx.budget_s or tb.dist > ctx.budget_m:
            guard += 1
            tb_fit = None
            if len(s.pieces) > 1:
                # сколько галсов оставить: наибольшее k, при котором точная траектория укладывается в АКБ.
                # Обычно хватает снять один галс (одна перестройка), иначе — двоичный поиск (log₂ n перестроек)
                P = s.pieces

                def fits_k(k):
                    s.pieces = P[:k]
                    try:
                        t = trajectory()
                    except RoutingError:
                        return False, None
                    return t.energy_s <= ctx.budget_s and t.dist <= ctx.budget_m, t

                ok, t = fits_k(len(P) - 1)
                if ok:
                    k, tb_fit = len(P) - 1, t
                else:
                    k, lo, hi = 1, 1, len(P) - 2
                    while lo <= hi:
                        mid = (lo + hi) // 2
                        ok, t = fits_k(mid)
                        if ok:
                            k, tb_fit, lo = mid, t, mid + 1
                        else:
                            hi = mid - 1
                s.pieces, tail = P[:k], P[k:]
            elif s.pieces[0].length > 150 and guard < 12:
                p = s.pieces[0]
                s.pieces = [_sub(p, 0, 0.5)]
                tail = [_sub(p, 0.5, 1)]
            else:
                p = s.pieces[0]
                if not p.optional:
                    unreachable.append((p.ax, p.ay, m, p.length))
                dropped = True
                break
            nxt = pending[m][0] if pending[m] else None
            R = ctx.max_site_dist
            if nxt is not None and R < math.inf:
                # без плана полета: хвост должен остаться в прямой видимости площадки следующего вылета
                sx, sy = sites_xy[nxt.site]
                if any(max(_d2(p.ax, p.ay, sx, sy), _d2(p.bx, p.by, sx, sy)) > R for p in tail):
                    nxt = None
            if nxt is not None and _d2(tail[-1].bx, tail[-1].by, nxt.pieces[0].ax, nxt.pieces[0].ay) < TAIL_JOIN_M:
                # хвост — в начало следующего вылета рядом (он тоже пройдет точную проверку и при необходимости
                # передаст свой хвост дальше), а не отдельным коротким вылетом ради одного галса
                nxt.pieces = tail + nxt.pieces
                nxt.blocked = sorted(set(nxt.blocked) | set(s.blocked))
                nxt.est_s += sum(p.length for p in tail) / ctx.vs_of(tail[0].comp)
            else:
                extra, bad = pack(ctx, tail, d.layer)
                unreachable.extend((x, y, m, L) for x, y, L, opt in bad if not opt)
                for e in extra:
                    e.blocked = list(s.blocked)
                    e.origin = "tail"
                for e in reversed(extra):
                    pending[m].appendleft(e)
                total += len(extra)
            try:
                if tb_fit is not None:
                    tb = tb_fit
                    s.site = tb.site_index
                else:
                    tb = trajectory()
            except RoutingError:
                unreachable.extend((p.ax, p.ay, m, p.length) for p in s.pieces if not p.optional)
                dropped = True
                break
        if dropped:
            continue
        if ctx.temp_zones:
            # окна запрета — по фактической траектории (переходы/перелеты тоже не должны задевать зону в окне)
            import shapely
            track = shapely.LineString([(p[0], p[1]) for p in tb.P])
            for g, a, b in ctx.temp_zones:
                if (a, b) not in s.blocked and g.buffer(30).intersects(track):
                    s.blocked.append((a, b))
        dur = tb.T[-1]
        start = earliest_start(d, s, dur, sites_xy, glob, windows)
        base_track = sample_track(tb, 0.0, terrain, d.id, ctx.model.separation_m, ctx.model.max_speed_ms)
        t_first = start
        horizon = [a for a in accepted if a.t0 + len(a.X) > start]
        for _ in range(3000):
            tr = shift_track(base_track, int(math.ceil(start)))
            c = first_conflict(tr, horizon, vsep)
            if c is None:
                break
            conflicts_resolved += 1
            start = fit_windows(start + 20.0, dur, windows, s.blocked)
        else:
            raise SchedulingError("Не удалось развести борта в допустимом времени; измените парк или площадки")
        start = float(int(math.ceil(start)))
        if s.blocked and any(start < b and start + dur > a for a, b in s.blocked):
            daylight_violations += 1
        if windows and not any(a <= start and start + dur <= b for a, b in windows):
            daylight_violations += 1
        tr = shift_track(base_track, int(start))
        accepted.append(tr)
        _commit(d, s, start, start + dur)
        out.append(Assigned(sortie=s, drone=d.id, start=start, end=start + dur, traj=tb, delay_s=start - t_first))
        if progress:
            progress(len(out) / max(total, 1))
    return out, accepted, {"conflict_checks_resolved": conflicts_resolved, "daylight_violations": daylight_violations,
                           "unreachable": unreachable}
