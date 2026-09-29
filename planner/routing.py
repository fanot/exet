"""Маршруты перелетов в обход запретных зон: граф видимости + Дейкстра.

Узлы графа — вершины запретных зон, раздутых на защитный буфер типа БВС и упрощенных
(вершины лежат снаружи зоны). Ребро допустимо, если отрезок не пересекает внутренность
зоны. Кратчайший путь в графе видимости — точный кратчайший путь на плоскости среди
многоугольных препятствий (с точностью до упрощения контура).

Масштаб. Граф строится лениво — при первом заблокированном отрезке (большинство перелетов прямые).
До ~1500 узлов проверяются все пары (полный граф видимости). В больших сценах (пример организаторов:
сотни зон и тысячи препятствий Московской области) — разреженный граф: каждый узел соединяется с k ближайшими
видимыми узлами. Все ребра по-прежнему проверены на пересечение с зонами, поэтому путь безопасен; длина может быть
на несколько процентов больше кратчайшей. Контуры крупных зон упрощаются с допуском, пропорциональным размеру
(вершины остаются снаружи зоны).
"""
from __future__ import annotations

import heapq
import math
from typing import Dict, List, Tuple

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

FULL_GRAPH_MAX = 700    # до стольких узлов — полный граф видимости
VISIBLE_NEAREST = 400   # в большом графе видимость точки проверяется до стольких ближайших узлов
K_NEAREST = 28          # в разреженном графе — видимых соседей на узел


class RoutingError(ValueError):
    """A safe path was not found; never substitute a segment through a zone."""


class Router:
    def __init__(self, forbidden, node_offset: float = 15.0):
        self.empty = forbidden is None or forbidden.is_empty
        self.cache: Dict[tuple, List[tuple]] = {}
        self.failed = 0
        self.node_offset = node_offset
        self._graph = False
        self._sssp: Dict[tuple, tuple] = {}
        self._neighbors = K_NEAREST
        self._visible_limit = VISIBLE_NEAREST
        if self.empty:
            return
        self.forbidden = forbidden
        # Only a centimetre of numerical tolerance: the old 0.5 m shrink allowed
        # straight connectors to cut through mandatory obstacle clearance zones.
        self.core = forbidden.buffer(-0.01)
        shapely.prepare(self.core)

    # ------------------------------------------------------------------ граф (лениво)
    def _nodes(self) -> np.ndarray:
        off = self.node_offset
        pts = []
        for p in getattr(self.forbidden, "geoms", [self.forbidden]):
            if p.geom_type != "Polygon" or p.area <= 0:
                continue
            # допуск упрощения растет с размером контура: у зоны в десятки км — десятки метров, у опоры — 6 м
            tol = max(off * 0.4, min(80.0, math.sqrt(p.area) / 150.0))
            outer = p.buffer(off + tol, quad_segs=2).simplify(tol)
            for q in getattr(outer, "geoms", [outer]):
                if q.geom_type != "Polygon":
                    continue
                for ring in [q.exterior, *q.interiors]:
                    pts.extend(list(ring.coords)[:-1])
        pts = np.array(pts) if pts else np.zeros((0, 2))
        if len(pts):
            pts = pts[~shapely.intersects(self.core, shapely.points(pts))]
        return pts

    def _ensure_graph(self, neighbors=None):
        neighbors = self._neighbors if neighbors is None else neighbors
        if self._graph and neighbors <= self._neighbors:
            return
        pts = self.nodes if self._graph else self._nodes()
        self._graph = True
        self._neighbors = neighbors
        self.nodes = pts
        self._sssp.clear()
        if hasattr(self, "_csr_m"):
            del self._csr_m
        n = len(pts)
        self.adj: List[List[Tuple[int, float]]] = [[] for _ in range(n)]
        if n < 2:
            return
        if n <= FULL_GRAPH_MAX:
            ii, jj = np.triu_indices(n, k=1)
        else:
            ii, jj = self._knn_pairs(pts, neighbors)
        # Bound temporary GEOS allocations when a disconnected graph grows.
        for start in range(0, len(ii), 8192):
            a, b = ii[start:start + 8192], jj[start:start + 8192]
            lines = shapely.linestrings(np.stack([pts[a], pts[b]], axis=1))
            ok = ~shapely.intersects(self.core, lines)
            d = np.hypot(*(pts[a] - pts[b]).T)
            for i, j, dd in zip(a[ok], b[ok], d[ok]):
                self.adj[i].append((j, dd))
                self.adj[j].append((i, dd))

    @staticmethod
    def _knn_pairs(pts: np.ndarray, k: int):
        """Пары «узел — k ближайших» блоками (без O(n²) памяти)."""
        n = len(pts)
        k = min(k, n - 1)
        P = pts.astype(np.float64)
        I, J = [], []
        for a in range(0, n, 768):
            b = min(n, a + 768)
            d = np.hypot(P[a:b, None, 0] - P[None, :, 0], P[a:b, None, 1] - P[None, :, 1])
            d[np.arange(b - a), np.arange(a, b)] = np.inf
            nb = np.argpartition(d, k, axis=1)[:, :k]
            I.append(np.repeat(np.arange(a, b), k))
            J.append(nb.ravel())
        I, J = np.concatenate(I), np.concatenate(J)
        lo, hi = np.minimum(I, J), np.maximum(I, J)
        key = np.unique(lo.astype(np.int64) * n + hi)
        return (key // n).astype(int), (key % n).astype(int)

    # ------------------------------------------------------------------ расстояния от площадки до множества точек
    def _csr(self):
        if not hasattr(self, "_csr_m"):
            from scipy.sparse import csr_matrix
            n = len(self.nodes)
            rows, cols, w = [], [], []
            for i, lst in enumerate(self.adj):
                for j, d in lst:
                    rows.append(i)
                    cols.append(j)
                    w.append(d)
            self._csr_m = (np.array(rows, dtype=np.int64), np.array(cols, dtype=np.int64), np.array(w), n)
        return self._csr_m

    def sssp(self, s):
        """Кратчайшие расстояния от точки s до всех узлов графа (одно дерево на площадку, scipy Dijkstra)."""
        key = (round(float(s[0])), round(float(s[1])))
        if key in self._sssp:
            return self._sssp[key]
        self._ensure_graph()
        n = len(self.nodes)
        if n == 0:
            res = (np.zeros(0),)
        else:
            from scipy.sparse import csr_matrix
            from scipy.sparse.csgraph import dijkstra
            rows, cols, w, _ = self._csr()
            vs = np.nonzero(self._visible(tuple(s)))[0]
            ds = np.hypot(self.nodes[vs, 0] - s[0], self.nodes[vs, 1] - s[1])
            # виртуальный узел n — сама точка s, ребра к видимым узлам
            R = np.concatenate([rows, np.full(len(vs), n)])
            C = np.concatenate([cols, vs])
            W = np.concatenate([w, ds + 1e-9])
            g = csr_matrix((W, (R, C)), shape=(n + 1, n + 1))
            res = (dijkstra(g, directed=True, indices=n)[:n],)
        self._sssp[key] = res
        return res

    def routed_dist(self, s, t, k: int = 48) -> float:
        """Длина кратчайшего маршрута s → t в обход зон (приближение через ближайшие видимые узлы графа)."""
        s, t = (float(s[0]), float(s[1])), (float(t[0]), float(t[1]))
        direct = math.hypot(t[0] - s[0], t[1] - s[1])
        if self.empty or self._clear(s, t):
            return direct
        if self.core.contains(Point(t)):
            return direct  # точка в зоне: галсов там нет, оценка не нужна
        (dist,) = self.sssp(s)
        if len(dist) == 0:
            return direct
        dt = np.hypot(self.nodes[:, 0] - t[0], self.nodes[:, 1] - t[1])
        idx = np.argpartition(dt, min(k, len(dt) - 1))[:k] if len(dt) > k else np.arange(len(dt))
        idx = idx[np.isfinite(dist[idx])]
        if len(idx):
            lines = shapely.linestrings(np.stack([np.repeat([t], len(idx), axis=0), self.nodes[idx]], axis=1))
            vis = idx[~shapely.intersects(self.core, lines)]
            if len(vis):
                return float(np.min(dist[vis] + dt[vis]))
        return path_length(self.path(s, t))

    # ------------------------------------------------------------------ запросы
    def _clear(self, a, b) -> bool:
        return not self.core.intersects(LineString([a, b]))

    def _escape(self, p):
        """Если точка внутри зоны (площадка в буфере) — выход к ближайшей границе."""
        pt = Point(p)
        if not self.core.contains(pt):
            return [tuple(p)]
        q = nearest_points(self.forbidden.boundary, pt)[0]
        dx, dy = q.x - p[0], q.y - p[1]
        d = math.hypot(dx, dy) or 1.0
        return [tuple(p), (q.x + dx / d * 3, q.y + dy / d * 3)]

    def path(self, a, b) -> List[tuple]:
        a, b = (float(a[0]), float(a[1])), (float(b[0]), float(b[1]))
        if self.empty:
            return [a, b]
        key = (round(a[0], 1), round(a[1], 1), round(b[0], 1), round(b[1], 1))
        if key in self.cache:
            return self.cache[key]
        ea, eb = self._escape(a), self._escape(b)
        s, t = ea[-1], eb[-1]
        if self._clear(s, t):
            res = ea + eb[::-1]
        else:
            res = ea[:-1] + self._dijkstra(s, t) + eb[::-1][1:]
        self.cache[key] = res
        return res

    def _visible(self, p) -> np.ndarray:
        n = len(self.nodes)
        if n == 0:
            return np.zeros(0, dtype=bool)
        idx = np.arange(n)
        if n > FULL_GRAPH_MAX:
            # большой граф: вход в граф — через ближайшие видимые узлы (дальше путь идет по ребрам графа)
            d = np.hypot(self.nodes[:, 0] - p[0], self.nodes[:, 1] - p[1])
            limit = min(self._visible_limit, n)
            idx = np.argpartition(d, limit - 1)[:limit]
        lines = shapely.linestrings(np.stack([np.repeat([p], len(idx), axis=0), self.nodes[idx]], axis=1))
        out = np.zeros(n, dtype=bool)
        out[idx] = ~shapely.intersects(self.core, lines)
        return out

    def _dijkstra(self, s, t) -> List[tuple]:
        self._ensure_graph()
        n = len(self.nodes)
        while True:
            result = self._graph_path(s, t)
            if result is not None:
                return result
            if n > FULL_GRAPH_MAX and self._neighbors < min(n - 1, K_NEAREST * 8):
                self._ensure_graph(min(n - 1, self._neighbors * 2))
                continue
            if self._visible_limit < n:
                self._visible_limit = n
                self._sssp.clear()
                continue
            self.failed += 1
            raise RoutingError("Не найден маршрут в обход запретных зон; добавьте площадку в доступной части области")

    def _graph_path(self, s, t):
        n = len(self.nodes)
        vs, vt = self._visible(s), self._visible(t)
        S, T = n, n + 1
        nodes = self.nodes
        # A*: эвристика — прямое расстояние до цели (допустима — кратчайший путь не короче прямой)
        h = lambda u: 0.0 if u == T else math.hypot(nodes[u][0] - t[0], nodes[u][1] - t[1]) if u < n else math.hypot(s[0] - t[0], s[1] - t[1])
        dist = {S: 0.0}
        prev = {}
        pq = [(h(S), 0.0, S)]
        while pq:
            _, d, u = heapq.heappop(pq)
            if u == T:
                break
            if d > dist.get(u, math.inf):
                continue
            if u == S:
                nbrs = [(j, math.hypot(nodes[j][0] - s[0], nodes[j][1] - s[1])) for j in np.nonzero(vs)[0]]
            else:
                nbrs = list(self.adj[u])
                if vt[u]:
                    nbrs.append((T, math.hypot(nodes[u][0] - t[0], nodes[u][1] - t[1])))
            for v, w in nbrs:
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd + h(v), nd, v))
        if T not in dist:
            return None
        out, u = [t], T
        while u != S:
            u = prev[u]
            out.append(s if u == S else (float(nodes[u][0]), float(nodes[u][1])))
        return out[::-1]


def path_length(pts) -> float:
    p = np.asarray(pts, dtype=float)
    if len(p) < 2:
        return 0.0
    return float(np.sum(np.hypot(*np.diff(p, axis=0).T)))
