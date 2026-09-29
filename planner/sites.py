"""Рекомендации по площадкам взлета/посадки.

1. Для невыполнимых участков (дальше радиуса действия с резервом 20%) — площадка на
   расстоянии ~60% радиуса от кластера точек в сторону существующей площадки, вне запретных зон.
2. Оптимизация расположения: взвешенный k-means (k-медианы в приближении) по точкам области
   съемки — минимизирует средний перелет; предлагаем, если выигрыш заметен.
"""
from __future__ import annotations

import math
from typing import List

import numpy as np
import shapely
from shapely.geometry import Point
from shapely.ops import nearest_points


def _push_out(x, y, forbidden, margin=60.0):
    if forbidden is None or forbidden.is_empty:
        return x, y
    p = Point(x, y)
    if not forbidden.buffer(margin).contains(p):
        return x, y
    q = nearest_points(forbidden.buffer(margin + 5).boundary, p)[0]
    return q.x, q.y


def sample_area(geom, step=250.0, max_pts=6000) -> np.ndarray:
    minx, miny, maxx, maxy = geom.bounds
    area = geom.area
    step = max(step, math.sqrt(area / max_pts))
    xs = np.arange(minx + step / 2, maxx, step)
    ys = np.arange(miny + step / 2, maxy, step)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    if len(pts) == 0:
        c = geom.centroid
        return np.array([[c.x, c.y]])
    inside = shapely.contains_xy(geom, pts[:, 0], pts[:, 1])
    pts = pts[inside]
    if len(pts) == 0:
        c = geom.representative_point()
        pts = np.array([[c.x, c.y]])
    return pts


def kmeans(pts: np.ndarray, k: int, init: np.ndarray, iters: int = 40) -> np.ndarray:
    C = init[:k].copy() if len(init) >= k else pts[np.linspace(0, len(pts) - 1, k).astype(int)].copy()
    if len(C) < k:
        C = np.vstack([C, pts[np.random.default_rng(0).choice(len(pts), k - len(C), replace=False)]])
    for _ in range(iters):
        d = np.hypot(pts[:, None, 0] - C[None, :, 0], pts[:, None, 1] - C[None, :, 1])
        lab = d.argmin(1)
        newC = np.array([np.median(pts[lab == i], axis=0) if np.any(lab == i) else C[i] for i in range(k)])
        if np.allclose(newC, C, atol=1.0):
            break
        C = newC
    return C


def mean_dist(pts, C):
    return float(np.hypot(pts[:, None, 0] - C[None, :, 0], pts[:, None, 1] - C[None, :, 1]).min(1).mean())


def suggest_sites(coverable, sites_xy: np.ndarray, forbidden, frame) -> List[dict]:
    pts = sample_area(coverable)
    cur = mean_dist(pts, sites_xy)
    out = []
    k = len(sites_xy)
    dmin = np.hypot(pts[:, None, 0] - sites_xy[None, :, 0], pts[:, None, 1] - sites_xy[None, :, 1]).min(1)
    farthest = pts[int(np.argmax(dmin))]
    for kk in (k, k + 1):
        init = sites_xy if kk == k else np.vstack([sites_xy, farthest])
        C = kmeans(pts, kk, init)
        C = np.array([_push_out(x, y, forbidden) for x, y in C])
        md = mean_dist(pts, C)
        gain = 1 - md / cur if cur > 0 else 0
        if gain > 0.15:
            ll = [frame.to_ll(x, y) for x, y in C]
            out.append({
                "kind": "optimize",
                "site_action": "replace",
                "sites": [{"lon": round(lo, 6), "lat": round(la, 6)} for lo, la in ll],
                "mean_transit_km_before": round(cur / 1000, 2), "mean_transit_km_after": round(md / 1000, 2),
                "reason": (f"{'Перенос' if kk == k else 'Добавление'} площадок: средний перелет до точки съемки "
                           f"{cur / 1000:.1f} → {md / 1000:.1f} км (−{gain * 100:.0f}%)"),
            })
    return out


def infeasible_recommendations(bad_pts, sites_xy, forbidden, frame, model_titles, radii: dict) -> List[dict]:
    """bad_pts: [(x, y, model_key)]. radii: model_key -> радиус действия, м."""
    if not bad_pts:
        return []
    arr = np.array([[p[0], p[1]] for p in bad_pts])
    keys = [p[2] for p in bad_pts]
    # кластеризация сеткой 3 км
    cell = 3000.0
    groups = {}
    for (x, y), k in zip(arr, keys):
        groups.setdefault((int(x // cell), int(y // cell), k), []).append((x, y))
    recs = []
    best_model = max(radii, key=lambda k: radii[k]) if radii else None
    for (_, _, k), pts in list(groups.items())[:12]:
        c = np.mean(pts, axis=0)
        d = np.hypot(sites_xy[:, 0] - c[0], sites_xy[:, 1] - c[1])
        i = int(np.argmin(d))
        r = radii.get(k, 0.0)
        dist = float(d[i])
        target = 0.6 * r
        if dist > target and dist > 0:
            nx = c[0] + (sites_xy[i, 0] - c[0]) * target / dist
            ny = c[1] + (sites_xy[i, 1] - c[1]) * target / dist
        else:
            nx, ny = c
        nx, ny = _push_out(nx, ny, forbidden)
        lon, lat = frame.to_ll(nx, ny)
        clon, clat = frame.to_ll(*c)
        txt = (f"Участок около ({clat:.5f}, {clon:.5f}) удален на {dist / 1000:.1f} км от ближайшей площадки, "
               f"а радиус действия {model_titles.get(k, k)} с резервом ≈ {r / 1000:.1f} км. "
               f"Добавьте площадку около ({lat:.5f}, {lon:.5f})")
        if best_model and best_model != k and radii[best_model] > dist:
            txt += f" или назначьте на участок {model_titles.get(best_model)} (радиус ≈ {radii[best_model] / 1000:.0f} км)"
        recs.append({"kind": "infeasible", "text": txt, "sites": [{"lon": round(lon, 6), "lat": round(lat, 6)}]})
    return recs


def auto_sites(area, forbidden, R: float, frame, max_sites: int = 40, target: float = 0.995) -> list:
    """Площадки, если в задании их нет (пример организаторов «Границы полетов.kml» — только области).

    1) жадное покрытие множества: круги досягаемости радиуса R покрывают точки области (ln n-приближение минимума);
    2) уточнение k-медианами — минимум среднего перелета до точек съемки;
    3) площадка выносится из запретных зон (с запасом). Возвращает [(lon, lat)].
    """
    pts = sample_area(area, step=max(200.0, R / 6))
    if len(pts) == 0:
        return []
    cand = sample_area(area.buffer(R * 0.25), step=max(250.0, R / 3))
    if forbidden is not None and not forbidden.is_empty:
        cand = cand[~shapely.contains_xy(forbidden.buffer(100), cand[:, 0], cand[:, 1])]
    if len(cand) == 0:
        cand = pts
    covered = np.zeros(len(pts), dtype=bool)
    chosen = []
    while covered.mean() < target and len(chosen) < max_sites:
        best, best_gain = None, 0
        for i in range(0, len(cand), max(1, len(cand) // 1500)):
            g = int((~covered & (np.hypot(pts[:, 0] - cand[i, 0], pts[:, 1] - cand[i, 1]) <= R)).sum())
            if g > best_gain:
                best, best_gain = i, g
        if best is None:
            break
        chosen.append(cand[best])
        covered |= np.hypot(pts[:, 0] - cand[best, 0], pts[:, 1] - cand[best, 1]) <= R
    if not chosen:
        c = area.representative_point()
        chosen = [np.array([c.x, c.y])]
    C = kmeans(pts, len(chosen), np.array(chosen))
    C = [_push_out(x, y, forbidden, 120.0) for x, y in C]
    return [frame.to_ll(x, y) for x, y in C]


def vlos_sites(coverable, sites_xy: np.ndarray, R: float, forbidden, frame, max_sites: int = 40, target: float = 0.98) -> dict | None:
    """Площадки для работы без плана полета: круги прямой видимости радиуса R должны покрыть участок.

    Жадное покрытие множества (ln n-приближение к минимуму): кандидаты — узлы сетки внутри участка
    и рядом с ним (вне запретных зон); на каждом шаге берется кандидат, покрывающий больше всего
    еще не покрытых точек участка.
    """
    pts = sample_area(coverable, step=max(150.0, R / 6))
    if len(pts) == 0:
        return None
    covered = np.zeros(len(pts), dtype=bool)
    for sx, sy in sites_xy:
        covered |= np.hypot(pts[:, 0] - sx, pts[:, 1] - sy) <= R
    if covered.mean() >= target:
        return None
    cand = sample_area(coverable.buffer(R * 0.3), step=max(200.0, R / 3))
    if forbidden is not None and not forbidden.is_empty:
        ok = ~shapely.contains_xy(forbidden.buffer(60), cand[:, 0], cand[:, 1])
        cand = cand[ok]
    if len(cand) == 0:
        return None
    D = np.hypot(cand[:, None, 0] - pts[None, :, 0], cand[:, None, 1] - pts[None, :, 1]) <= R
    chosen = []
    while covered.mean() < target and len(chosen) < max_sites:
        gain = (D & ~covered[None, :]).sum(1)
        k = int(np.argmax(gain))
        if gain[k] == 0:
            break
        chosen.append(cand[k])
        covered |= D[k]
    if not chosen:
        return None
    ll = [frame.to_ll(x, y) for x, y in chosen]
    return {"kind": "vlos", "coverage_pct": round(float(covered.mean()) * 100, 1),
            "sites": [{"lon": round(lo, 6), "lat": round(la, 6)} for lo, la in ll],
            "reason": (f"Без плана полета (прямая видимость ≤ {R / 1000:g} км) {len(chosen)} дополнительных "
                       f"площадок увеличивают покрытие кругами видимости до {covered.mean() * 100:.1f}% доступной площади. "
                       + ("Этого недостаточно для полного покрытия. " if covered.mean() < target else "")
                       + "Альтернатива — режим с планом полета")}
