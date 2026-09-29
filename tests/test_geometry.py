"""Покрытие: сканлайн с отверстиями, запретные зоны, маршрутизация в обход."""
import math

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box

from planner.coverage import Component, _edges, generate_pieces, rotate_geom, scan_intervals
from planner.routing import Router


class G:
    def spacing(self, crab=0.0):
        return 50.0


def test_scanline_with_hole():
    poly = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)], [[(400, 400), (600, 400), (600, 600), (400, 600)]])
    iv = scan_intervals(_edges(poly), 500.0)
    assert iv == [(0.0, 400.0), (600.0, 1000.0)]
    assert scan_intervals(_edges(poly), 100.0) == [(0.0, 1000.0)]


def test_pieces_cover_polygon_and_avoid_forbidden():
    poly = box(0, 0, 2000, 1000)
    forb = Point(1000, 500).buffer(150)
    region = poly.difference(forb)
    comp = Component(idx=0, geom=region, theta=0.0, rot=region, vmin=0.0, umin=0.0)
    ps = generate_pieces("m", G(), comp, region, forb, 0.0, 800.0, 30.0, np.array([[0.0, 0.0]]))
    lines = [LineString([(p.ax, p.ay), (p.bx, p.by)]) for p in ps]
    assert all(not l.intersects(forb.buffer(-0.5)) for l in lines)
    rows = sorted({round(p.ay) for p in ps})
    assert rows[0] == 25 and rows[-1] == 975 and all(b - a == 50 for a, b in zip(rows, rows[1:]))
    # полосы (5 м по краям галсов) покрывают всю разрешенную область
    covered = lines[0].buffer(25.5, cap_style="flat")
    for l in lines[1:]:
        covered = covered.union(l.buffer(25.5, cap_style="flat"))
    assert region.difference(covered).area / region.area < 0.002


def test_router_avoids_zone():
    zone = Point(0, 0).buffer(500)
    r = Router(zone)
    path = r.path((-2000, 0), (2000, 0))
    assert len(path) > 2
    assert not LineString(path).intersects(zone.buffer(-1))
    L = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
    assert L < 4000 + 2 * 500  # не хуже обхода по полуокружности


def test_bent_corridor_split_into_straight_parts():
    """Коридор «Г»: без разбиения один курс галсов; с разбиением — разные курсы по участкам."""
    from planner.coverage import _split_nonconvex
    leg1 = LineString([(0, 0), (8000, 0), (8000, 8000)]).buffer(250, cap_style="flat", join_style="mitre")
    parts = _split_nonconvex(leg1, 20000)
    assert len(parts) >= 2
    assert abs(sum(p.area for p in parts) - leg1.area) < 1.0
    assert all(p.area / p.convex_hull.area > 0.75 for p in parts)
    # выпуклый прямоугольник не режется
    assert len(_split_nonconvex(box(0, 0, 5000, 3000), 20000)) == 1
