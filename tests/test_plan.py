"""Сквозные проверки планировщика на демо-сценарии («crash-тест»)."""
import copy
import json

import pytest

from planner.geo import ScenarioError, parse_geojson, parse_kml
from planner.models import MissionParams
from planner.optimizer import plan
from tests.conftest import square

FLEET = [{"model": "geoscan_201", "count": 2}, {"model": "geoscan_gemini", "count": 4}, {"model": "geoscan_801", "count": 2}]


def params(**kw):
    base = dict(fleet=FLEET, start_time="2026-09-25T09:00:00+03:00", wind_speed_ms=5, wind_dir_deg=250,
                search_effort="fast", use_terrain=False)
    base.update(kw)
    return MissionParams(**base)


@pytest.fixture(scope="module")
def plan_time(toksovo):
    return plan(parse_geojson(copy.deepcopy(toksovo)), params(objective="time"))


def test_demo_plan_valid(plan_time):
    r = plan_time
    assert r["status"] == "ok"
    v = r["validation"]
    assert v["passed"], [c for c in v["checks"] if not c["passed"]]
    assert r["summary"]["coverage_allowed_pct"] > 99.0


def test_energy_reserve(plan_time):
    from planner.catalog import load_catalog
    cat = load_catalog()
    for s in plan_time["sorties"]:
        m = cat.drones[s["model"]]
        assert s["duration_min"] <= m.endurance_min * 0.8 + 0.05


def test_temporary_zone_respected(plan_time):
    # вылеты, проходящие через временную зону, — только вне 12:00–16:00
    v = {c["name"]: c for c in plan_time["validation"]["checks"]}
    assert v["Бесполетные зоны"]["passed"]


def test_flight_objective_not_more_flight_hours(toksovo, plan_time):
    r2 = plan(parse_geojson(copy.deepcopy(toksovo)), params(objective="flight"))
    assert r2["validation"]["passed"]
    assert r2["summary"]["flight_hours"] <= plan_time["summary"]["flight_hours"] * 1.02


def test_infeasible_gives_recommendation():
    sc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "survey_area"}, "geometry": {"type": "Polygon", "coordinates": [square(30.9, 60.3, 2)]}},
        {"type": "Feature", "properties": {"kind": "site"}, "geometry": {"type": "Point", "coordinates": [30.5, 60.1]}},
    ]}
    r = plan(parse_geojson(sc), params(fleet=[{"model": "geoscan_gemini", "count": 2}]))
    assert r["status"] == "infeasible"
    assert any("Добавьте площадку" in (x.get("text") or "") for x in r["recommendations"])


def test_invalid_input_rejected():
    with pytest.raises(ScenarioError):
        parse_geojson({"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"kind": "site"}, "geometry": {"type": "Point", "coordinates": [30, 60]}}]})
    with pytest.raises(ValueError):
        params(fleet=[{"model": "geoscan_gemini", "count": 0}])


def test_kml_input():
    ring = " ".join(f"{x},{y},0" for x, y in square(30.5, 60.15, 1.5))
    hole = " ".join(f"{x},{y},0" for x, y in square(30.5, 60.15, 0.3))
    kml = f"""<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Folder><name>Область съемки</name><Placemark><name>A</name><Polygon><outerBoundaryIs><LinearRing><coordinates>{ring}</coordinates></LinearRing></outerBoundaryIs>
        <innerBoundaryIs><LinearRing><coordinates>{hole}</coordinates></LinearRing></innerBoundaryIs></Polygon></Placemark></Folder>
      <Folder><name>Площадки</name><Placemark><name>Старт</name><Point><coordinates>30.49,60.14,0</coordinates></Point></Placemark></Folder>
      <Folder><name>Высотные препятствия</name><Placemark><name>Вышка</name><ExtendedData><Data name="height_m"><value>90</value></Data></ExtendedData>
        <Point><coordinates>30.505,60.155,0</coordinates></Point></Placemark></Folder>
    </Document></kml>"""
    sc = parse_kml(kml)
    assert len(sc.areas) == 1 and len(sc.sites) == 1 and sc.obstacles[0].height_m == 90
    r = plan(sc, params(fleet=[{"model": "geoscan_gemini", "count": 2}]))
    assert r["status"] == "ok" and r["validation"]["passed"]


def test_exports(plan_time):
    from planner import export
    from planner.catalog import load_catalog
    gj = export.to_geojson(plan_time)
    routes = [f for f in gj["features"] if f["properties"]["feature_type"] == "route"]
    assert len(routes) == plan_time["summary"]["sorties"]
    assert all(len(f["properties"]["coord_times"]) == len(f["geometry"]["coordinates"]) for f in routes)
    import xml.etree.ElementTree as ET
    ET.fromstring(export.to_kml(plan_time).encode())
    qgc = export.to_qgc_plan(plan_time, plan_time["sorties"][0], load_catalog())
    assert qgc["mission"]["items"][0]["command"] == 22 and qgc["mission"]["items"][-1]["command"] == 21
    assert len(export.to_zip(plan_time, load_catalog())) > 1000


def test_bayes_optimizer_valid(toksovo):
    r = plan(parse_geojson(copy.deepcopy(toksovo)), params(objective="flight", optimizer="bayes"))
    assert r["validation"]["passed"]
    assert r["summary"]["optimizer"] == "bayes" and r["summary"]["bayes"]["trials"] == 20
    assert any(x.get("method") == "bayes" for x in r["search_log"])


def test_no_flight_plan_mode(toksovo):
    """ФП ИВП без плана полета: потолок 150 м, самолет исключен, прямая видимость, рекомендация площадок."""
    r = plan(parse_geojson(copy.deepcopy(toksovo)), params(airspace_mode="no_flight_plan", vlos_radius_km=2.0))
    assert "geoscan_201" in r["excluded"]
    assert r["summary"]["ceiling_agl_m"] <= 150
    assert all(m["altitude_agl_m"] <= 120 for m in r["models"].values())
    names = {c["name"]: c for c in r["validation"]["checks"]}
    assert "Прямая видимость (ФП ИВП, без плана полета)" in names
    assert r["validation"]["passed"], [c for c in r["validation"]["checks"] if not c["passed"]]
    assert any(x.get("kind") == "vlos" for x in r["recommendations"])  # площадки для покрытия кругами видимости


def test_site_inside_nofly_not_used(toksovo):
    """Площадка внутри постоянной запретной зоны не используется: вертикальный взлет нарушил бы зону."""
    sc = copy.deepcopy(toksovo)
    site = next(f for f in sc["features"] if f["properties"].get("kind") == "site")
    lon, lat = site["geometry"]["coordinates"][:2]
    d = 0.004
    ring = [[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]
    sc["features"].append({"type": "Feature", "properties": {"kind": "no_fly", "name": "вокруг площадки"},
                           "geometry": {"type": "Polygon", "coordinates": [ring]}})
    r = plan(parse_geojson(sc), params())
    name = site["properties"]["name"]
    assert any(name in w and "не используется" in w for w in r["warnings"])
    assert all(s["site"] != name for s in r["sorties"])
    assert r["validation"]["passed"], [c for c in r["validation"]["checks"] if not c["passed"]]
