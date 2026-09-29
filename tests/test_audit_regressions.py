"""Adversarial cases: reject unsafe plans, not just accept the generated demo."""
import copy
import math
from datetime import timedelta

import numpy as np
import pytest
from shapely.geometry import box, mapping

from planner.catalog import load_catalog
from planner.dispatch import daylight_fit
from planner.environment import daylight_windows, sun_elevation_deg
from planner.geo import LocalFrame, ScenarioError, parse_geojson
from planner.models import MissionParams
from planner.optimizer import Planner, plan
from planner.validate import validate_plan


def params(**kw):
    return MissionParams(**{
        "fleet": [{"model": "geoscan_gemini", "count": 1}],
        "start_time": "2026-09-25T09:00:00+03:00", "wind_speed_ms": 0,
        "use_terrain": False, "respect_daylight": False,
        "search_effort": "fast", "optimizer": "grid", **kw,
    })


def scenario(extra=(), lat=55.0):
    fr = LocalFrame(38, lat)
    features = [
        {"type": "Feature", "properties": {"kind": "survey_area"},
         "geometry": mapping(fr.geom_to_ll(box(-300, -300, 300, 300)))},
        {"type": "Feature", "properties": {"kind": "site", "name": "S"},
         "geometry": {"type": "Point", "coordinates": [38, lat]}},
    ]
    return {"type": "FeatureCollection", "features": features + list(extra)}


def track(points, p=None, sid=1, drone="GEM-1"):
    p = p or params()
    fr = LocalFrame(38, 55)
    t0 = p.start_time.timestamp()
    W = [[*fr.to_ll(x, y), z, z, t0 + t, ph] for x, y, z, t, ph in points]
    return {"id": sid, "drone_id": drone, "model": "geoscan_gemini", "site": "S", "landing_site": "S",
            "t_takeoff": W[0][4], "t_landing": W[-1][4], "distance_km": 0, "waypoints": W}


def check(sorties, sc=None, p=None):
    return validate_plan({"sorties": sorties}, parse_geojson(sc or scenario()), p or params())


def named(result, name):
    return next(c for c in result["checks"] if c["name"] == name)


@pytest.mark.parametrize("kw", [
    {"fleet": [{"model": "geoscan_gemini", "count": 1}] * 2},
    {"reserve_pct": 10}, {"front_overlap": 0.6}, {"side_overlap": 0.4},
])
def test_reject_invalid_mission_contract(kw):
    with pytest.raises(ValueError):
        params(**kw)


@pytest.mark.parametrize("properties", [
    {"alt_min_m": 100, "alt_max_m": 50}, {"alt_min_m": "NaN"},
    {"alt_ref": "typo"},
    {"active_from": "2026-09-26T09:00:00Z", "active_to": "2026-09-25T09:00:00Z"},
])
def test_reject_invalid_zone(properties):
    f = scenario()["features"][0]
    f["properties"] = {"kind": "no_fly", **properties}
    with pytest.raises(ScenarioError):
        parse_geojson(scenario([f]))


def test_zone_naive_time_uses_same_timezone_as_mission():
    f = scenario()["features"][0]
    f["properties"] = {"kind": "no_fly", "active_from": "2026-09-25T10:00:00"}
    z = parse_geojson(scenario([f])).nofly[0]
    assert z.active_from.utcoffset() == timedelta(hours=3)
    assert z.active_during(params().start_time, params().start_time + timedelta(hours=2))


def test_partial_layer_coordinates_validated():
    data = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "site"},
         "geometry": {"type": "Point", "coordinates": [190, 55]}}]}
    with pytest.raises(ScenarioError):
        parse_geojson(data, partial=True)


def test_overlapping_sorties_on_same_drone_are_invalid():
    s = track([(0, 0, 0, 0, "ground"), (0, 0, 100, 30, "takeoff"),
               (0, 0, 100, 90, "survey"), (0, 0, 0, 130, "landing")])
    other = copy.deepcopy(s)
    other["id"] = 2
    assert not check([s, other])["passed"]


def test_duplicate_sortie_ids_are_invalid():
    s = track([(0, 0, 0, 0, "ground"), (0, 0, 100, 30, "takeoff"), (0, 0, 0, 70, "landing")])
    assert not check([s, copy.deepcopy(s)])["passed"]


def test_crossing_thin_zone_between_seconds_is_detected():
    fr = LocalFrame(38, 55)
    f = {"type": "Feature", "properties": {"kind": "no_fly"},
         "geometry": mapping(fr.geom_to_ll(box(2, -10, 3, 10)))}
    s = track([(0, 0, 0, 0, "ground"), (0, 0, 100, 30, "takeoff"),
               (10, 0, 100, 31, "survey"), (0, 0, 100, 32, "survey"), (0, 0, 0, 72, "landing")])
    assert not named(check([s], scenario([f])), "Бесполетные зоны")["passed"]


def test_obstacle_safety_margin_is_mandatory():
    f = {"type": "Feature", "properties": {"kind": "obstacle", "height_m": 80, "radius_m": 5},
         "geometry": {"type": "Point", "coordinates": [38, 55]}}
    s = track([(0, 0, 100, 0, "survey"), (0, 0, 100, 20, "survey")])
    assert not named(check([s], scenario([f])), "Высотные препятствия")["passed"]


def test_model_ceiling_not_only_global_corridor():
    s = track([(0, 0, 600, 0, "transit_out"), (0, 0, 600, 20, "transit_out")])
    assert not named(check([s]), "Воздушное пространство и высоты")["passed"]


def test_polar_night_does_not_disable_daylight():
    p = params(start_time="2026-12-20T09:00:00+03:00", respect_daylight=True)
    r = plan(parse_geojson(scenario(lat=80)), p)
    assert r["status"] == "infeasible"
    assert not r.get("sorties")


def test_missing_daylight_window_is_not_unrestricted():
    with pytest.raises(ValueError):
        daylight_fit(0, 60, [])
    with pytest.raises(ValueError):
        daylight_fit(0, 60, [(0, 30)])
    assert daylight_fit(0, 60, None) == 0


def test_daylight_window_ends_before_threshold_crossing():
    p = params()
    windows = daylight_windows(55, 38, p.start_time, 1, 10)
    assert windows
    from datetime import datetime, timezone
    for _, b in windows:
        assert sun_elevation_deg(55, 38, datetime.fromtimestamp(b - 0.1, timezone.utc)) >= 9.999


def test_late_plan_is_infeasible():
    r = plan(parse_geojson(scenario()), params(deadline_h=0.1))
    assert r["status"] == "infeasible"


def test_future_zone_not_removed_at_arbitrary_48_hour_horizon():
    f = scenario()["features"][0]
    f["properties"] = {"kind": "no_fly", "active_from": "2026-09-28T09:00:00+03:00",
                       "active_to": "2026-09-29T09:00:00+03:00"}
    pl = Planner(parse_geojson(scenario([f])), params())
    pl.prepare()
    assert len(pl.zones) == 1


def test_failed_validation_cannot_produce_ok(monkeypatch):
    monkeypatch.setattr("planner.validate.validate_plan", lambda *args: {
        "passed": False, "checks": [{"name": "test violation", "passed": False, "details": ["violation"]}]})
    assert plan(parse_geojson(scenario()), params())["status"] == "infeasible"


def test_invalid_plan_structure_returns_check_not_exception():
    s = track([(0, 0, 0, 0, "ground"), (0, 0, 100, 30, "takeoff")])
    s["waypoints"][1][4] = s["waypoints"][0][4]
    assert not check([s])["passed"]


def test_continuous_separation_between_integer_seconds():
    a = track([(-5, 0, 100, 30, "survey"), (5, 0, 100, 31, "survey")])
    b = track([(5, 49.8, 100, 30, "survey"), (-5, 49.8, 100, 31, "survey")], sid=2, drone="GEM-2")
    # Both sampled endpoints are >50 m apart; at t=30.5 the separation is 49.8 m.
    assert not named(check([a, b]), "Опасные сближения бортов")["passed"]


def test_agl_is_recomputed_instead_of_trusting_export():
    s = track([(0, 0, 600, 0, "transit_out"), (0, 0, 600, 20, "transit_out")])
    for w in s["waypoints"]:
        w[3] = 100
    assert not named(check([s]), "Воздушное пространство и высоты")["passed"]


def test_cloud_ceiling_applies_to_transit():
    s = track([(0, 0, 200, 0, "transit_out"), (0, 0, 200, 20, "transit_out")])
    assert not named(check([s], p=params(cloud_base_m=200)), "Воздушное пространство и высоты")["passed"]


def test_requested_but_unavailable_terrain_cannot_pass(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("planner.terrain.Terrain", lambda *a, **k: SimpleNamespace(
        enabled=False, warning="offline", elevation=lambda lon, lat: np.zeros_like(lon)))
    s = track([(0, 0, 100, 0, "survey"), (0, 0, 100, 20, "survey")])
    assert not named(check([s], p=params(use_terrain=True)), "Данные рельефа")["passed"]


def model_context(key="geoscan_201"):
    from planner.sensors import compute_survey_geom
    from planner.sortie import ModelCtx
    from planner.routing import Router
    from planner.terrain import Terrain, TerrainXY
    cat = load_catalog()
    m = cat.drones[key]
    g, _ = compute_survey_geom(cat, m, "rgb", 3.0, None, None, 0, 700)
    return ModelCtx(key, m, g, np.array([[0, 0]]), m.endurance_min * 48, math.inf,
                    router_transit=Router(None), router_survey=Router(None),
                    terrain=TerrainXY(Terrain((38, 55, 38, 55), enabled=False), LocalFrame(38, 55)))


def test_plane_parachute_ends_at_declared_landing_site():
    from planner.coverage import Piece
    from planner.sortie import Sortie, build_trajectory
    ctx = model_context()
    piece = Piece(model=ctx.key, comp=0, col=0, row=0, ax=300, ay=0, bx=600, by=0)
    tb = build_trajectory(ctx, Sortie(ctx.key, [piece], 0), (0, 0), 350)
    assert np.linalg.norm(np.array(tb.P[-1])[:2]) < 0.01
    assert tb.P[-1][2] == pytest.approx(0)
    assert np.all(np.diff(tb.T) > 0)


@pytest.mark.parametrize("strict, cap", [(False, 500), (True, 150)])
def test_steep_terrain_must_not_push_route_underground(strict, cap):
    from types import SimpleNamespace
    from planner.sortie import _chunks
    from planner.routing import RoutingError
    ctx = model_context("geoscan_gemini")
    # A 600 m cliff within 10 m cannot be traversed at constant AMSL under this ceiling.
    ctx.terrain = SimpleNamespace(enabled=True, h=lambda x, y: 60 * np.asarray(x))
    ctx.agl_cap, ctx.strict_ceiling = cap, strict
    with pytest.raises(RoutingError):
        _chunks(ctx, np.array([0.0, 0.0]), np.array([10.0, 0.0]), 100)


def test_plane_crab_uses_air_velocity_vector():
    g = model_context().geom
    assert g.crab_rad(8, 6, 22) == pytest.approx(math.atan2(8, 22 - 6))


def test_heading_rejected_if_crab_breaks_camera_trigger_limit():
    g = model_context().geom
    g.min_trigger_s = g.photo_base(0) / g.speed_ms
    assert g.survey_speed(0, 0) > 0
    assert g.survey_speed(0, 8) == 0


def test_narrow_island_gets_survey_line():
    from planner.coverage import Component, generate_pieces
    ctx = model_context("geoscan_gemini")
    region = box(0, 0, 100, 10)  # width < half the 90 m spacing
    comp = Component(idx=0, geom=region, theta=0, rot=region, vmin=0, umin=0, w_par=0, w_perp=0)
    pieces = generate_pieces(ctx.key, ctx.geom, comp, region, None, 0, 500, 0, ctx.sites_xy)
    assert pieces and sum(p.length for p in pieces) == pytest.approx(100)


def test_router_does_not_cut_half_metre_from_safety_buffer():
    from planner.routing import Router
    from shapely.geometry import LineString
    forbidden = box(0, 0, 100, 100)
    path = Router(forbidden).path((-10, 0.2), (110, 0.2))
    assert not LineString(path).intersects(forbidden.buffer(-0.02))


def test_disconnected_graph_fails_instead_of_crossing_zone(monkeypatch):
    from planner.routing import Router, RoutingError
    r = Router(box(0, 0, 100, 100))
    monkeypatch.setattr(r, "_nodes", lambda: np.zeros((0, 2)))
    with pytest.raises(RoutingError):
        r.path((-10, 50), (110, 50))


def test_terrain_sampling_never_exceeds_requested_step():
    from types import SimpleNamespace
    from planner.terrain import TerrainXY
    t = TerrainXY(SimpleNamespace(enabled=True), None)
    samples = []
    def heights(x, y):
        samples.extend(x)
        return np.zeros_like(x)
    t.h = heights
    t.max_along([(0, 0), (49, 0)], step=25)
    assert max(np.diff(samples)) <= 25


def test_copter_takeoff_ceiling_in_normal_mode():
    from planner.coverage import Piece
    from planner.sortie import Sortie, build_trajectory
    ctx = model_context("geoscan_gemini")
    ctx.terrain.t.enabled = True
    ctx.terrain.h = lambda x, y: np.maximum(np.atleast_1d(x), 0) / 3
    ctx.agl_cap = 400
    piece = Piece(model=ctx.key, comp=0, col=0, row=0, ax=300, ay=0, bx=600, by=0)
    tb = build_trajectory(ctx, Sortie(ctx.key, [piece], 0), (0, 0), 350)
    P = np.asarray(tb.P)
    assert np.max(P[:, 2] - ctx.terrain.h(P[:, 0], P[:, 1])) <= 400


def test_polar_terrain_returns_explicit_unavailable():
    from planner.terrain import Terrain
    t = Terrain((0, 89, 1, 90))
    assert not t.enabled and "85.05" in t.warning


def test_qgc_takeoff_and_camera_commands_follow_phases():
    from planner.export import to_qgc_plan
    s = track([(0, 0, 0, 0, "ground"), (0, 0, 300, 60, "takeoff"),
               (100, 0, 300, 70, "transit_out"), (100, 0, 150, 100, "descent"),
               (200, 0, 150, 110, "survey"), (200, 0, 300, 140, "climb"),
               (0, 0, 300, 160, "transit_back"), (0, 0, 0, 220, "landing")])
    s.update(survey_speed_ms=10, transit_speed_ms=14, photo_base_m=25)
    r = {"models": {s["model"]: {"photo_base_m": 30}}}
    items = to_qgc_plan(r, s, load_catalog())["mission"]["items"]
    assert items[0]["params"][4:7] == [s["waypoints"][1][1], s["waypoints"][1][0], 300]
    assert {x["params"][1] for x in items if x["command"] == 178} == {10, 14}
    camera = [x["params"] for x in items if x["command"] == 206]
    assert camera == [[25, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0]]
