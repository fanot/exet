"""Acceptance checks added for the 2026-09-28 full project review.

Includes regressions for defects found during the review and browser checks.
API history is isolated in pytest temporary directories.
"""
from __future__ import annotations

import copy
import importlib
import io
import json
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box

from planner import export
from planner.catalog import load_catalog
from planner.geo import parse_geojson, parse_scenario_bytes
from planner.models import MissionParams
from planner.optimizer import plan
from planner.validate import validate_plan
from scripts.qa_acceptance import FIXTURES, feature, fixture_scenarios, parameters, small_scenario


@pytest.fixture
def qa_client(tmp_path, monkeypatch):
    api = importlib.import_module("api.app")
    plans = tmp_path / "plans"
    scenarios = plans / "scenarios"
    scenarios.mkdir(parents=True)
    monkeypatch.setattr(api, "PLANS", plans)
    monkeypatch.setattr(api, "PLAN_SCENARIOS", scenarios)
    monkeypatch.setattr(api, "JOBS", {})
    with TestClient(api.app, raise_server_exceptions=False) as client:
        yield client, api


@pytest.fixture(scope="module")
def basic_plan():
    p = parameters()
    sc = parse_geojson(small_scenario())
    r = plan(sc, p)
    assert r["status"] == "ok" and r["validation"]["passed"]
    return r, sc, p


def upload(client, scenario, partial=False):
    return client.post("/api/scenario/parse", files=[
        ("files", ("scenario.geojson", json.dumps(scenario).encode("utf-8"), "application/json"))
    ], data={"partial": str(partial).lower()})


def wait_job(client, api, job_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        st = response.json()
        assert 0 <= st["progress"] <= 1
        if st["status"] in {"done", "error", "cancelled"} and "elapsed_final" in api.JOBS[job_id]:
            return st
        time.sleep(0.02)
    raise AssertionError(f"Job {job_id} did not finish within {timeout} seconds")


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/static/style.css", "/docs", "/redoc", "/openapi.json"])
def test_public_pages_available(qa_client, path):
    client, _ = qa_client
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("sid", ["demo_toksovo", "demo_pipeline", "demo_primorye", "demo_moscow_task"])
def test_every_demo_is_parseable(qa_client, sid):
    client, _ = qa_client
    response = client.get(f"/api/scenarios/{sid}")
    assert response.status_code == 200
    assert parse_geojson(response.json()).areas


@pytest.mark.parametrize("override", [
    {"fleet": []}, {"fleet": [{"model": "unknown", "count": 1}]},
    {"fleet": [{"model": "geoscan_gemini", "count": -1}]},
    {"fleet": [{"model": "geoscan_gemini", "count": 20}, {"model": "geoscan_801", "count": 20}]},
    {"gsd_cm": 0}, {"wind_speed_ms": -1}, {"wind_dir_deg": 360},
    {"cloud_base_m": 40}, {"deadline_h": 0}, {"vlos_radius_km": 0.1},
    {"front_overlap": 0.69}, {"side_overlap": 0.49}, {"reserve_pct": 19.9},
])
def test_invalid_parameters_rejected_before_job(qa_client, override):
    client, api = qa_client
    p = parameters().model_dump(mode="json")
    response = client.post("/api/plan", json={"scenario": small_scenario(), "params": {**p, **override}})
    assert response.status_code == 422
    assert not api.JOBS


@pytest.mark.parametrize("filename,content", [
    ("broken.geojson", b'{"type":'), ("broken.kml", b"<kml>"),
    ("utf16.geojson", '{"type":"FeatureCollection"}'.encode("utf-16")),
    ("bad.geojson", b'[]'),
])
def test_invalid_uploaded_files_are_readable_errors(qa_client, filename, content):
    client, _ = qa_client
    response = client.post("/api/scenario/parse", files=[("files", (filename, content))])
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_upload_layers_and_partial_mode(qa_client):
    client, _ = qa_client
    files = [("files", (p.name, p.read_bytes())) for p in [
        FIXTURES / "layer_area.kml", FIXTURES / "layer_zones.kml", FIXTURES / "layer_obstacles.kml"]]
    response = client.post("/api/scenario/parse", files=files, data={"partial": "false"})
    assert response.status_code == 200
    sc = parse_geojson(response.json())
    assert len(sc.areas) == len(sc.nofly) == len(sc.obstacles) == 1
    obstacle_layer = {"type": "FeatureCollection", "features": [feature("obstacle", Point(0, 0), height_m=80)]}
    assert upload(client, obstacle_layer, partial=True).status_code == 200
    assert upload(client, obstacle_layer, partial=False).status_code == 422


def test_utf8_bom_and_geometry_repair():
    sc = parse_scenario_bytes(b"\xef\xbb\xbf" + json.dumps(small_scenario()).encode(), "area.geojson")
    assert sc.areas
    malformed = json.loads(json.dumps(small_scenario()))
    ring = malformed["features"][0]["geometry"]["coordinates"][0]
    ring[1], ring[2] = ring[2], ring[1]
    repaired = parse_geojson(malformed)
    assert all(g.is_valid for _, g in repaired.areas)


def test_upload_file_count_limit(qa_client):
    client, _ = qa_client
    payload = json.dumps(small_scenario()).encode()
    response = client.post("/api/scenario/parse", files=[
        ("files", (f"area{i}.geojson", payload)) for i in range(65)])
    assert response.status_code == 413


@pytest.mark.parametrize("path", [
    "/api/jobs/unknown", "/api/jobs/unknown/cancel", "/api/scenarios/unknown",
    "/api/plans/unknown", "/api/plans/unknown/scenario", "/api/plans/unknown/export/kml",
    "/api/plans/unknown/brief", "/api/plans/C%3Asecret", "/api/scenarios/C%3Asecret",
])
def test_unknown_and_invalid_identifiers(qa_client, path):
    client, _ = qa_client
    response = client.post(path) if path.endswith("/cancel") else client.get(path)
    assert response.status_code == 404


def test_full_api_lifecycle_including_delete(qa_client):
    client, api = qa_client
    sc, p = small_scenario(), parameters().model_dump(mode="json")
    response = client.post("/api/plan/upload", files=[
        ("files", ("area.geojson", json.dumps(sc).encode()))], data={"params": json.dumps(p)})
    assert response.status_code == 200
    st = wait_job(client, api, response.json()["job_id"])
    assert st["status"] == "done" and st["progress"] == 1
    assert st["trace"] and st["evaluations"] > 0
    assert "planner" not in api.JOBS[st["job_id"]]
    pid = st["plan_id"]
    r = client.get(f"/api/plans/{pid}").json()
    assert r["status"] == "ok" and r["validation"]["passed"]
    saved = client.get(f"/api/plans/{pid}/scenario").json()
    assert saved["name"] == sc["name"]
    assert saved["features"] == json.loads(json.dumps(sc["features"]))
    assert r["params"] == p
    assert client.get("/api/plans").json()[0]["id"] == pid
    validation = client.post("/api/validate", json={"scenario": sc, "params": p, "plan": r})
    assert validation.status_code == 200 and validation.json()["passed"]
    for fmt in ["kml", "geojson", "csv", "zip"]:
        response = client.get(f"/api/plans/{pid}/export/{fmt}")
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
    assert client.get(f"/api/plans/{pid}/export/unsupported").status_code == 400
    assert client.get(f"/api/plans/{pid}/export/qgc").status_code == 404
    assert client.get(f"/api/plans/{pid}/brief", params={"drone": "NOPE"}).status_code == 404
    assert client.post(f"/api/jobs/{st['job_id']}/cancel").json()["status"] == "done"
    assert client.delete(f"/api/plans/{pid}").status_code == 200
    assert client.get(f"/api/plans/{pid}").status_code == 404
    assert client.get("/api/plans").json() == []
    assert not list(api.PLAN_SCENARIOS.glob("*.json"))


def test_infeasible_api_result_has_no_exports(qa_client):
    client, api = qa_client
    sc = small_scenario()
    p = parameters(survey_type="ir").model_dump(mode="json")
    st = wait_job(client, api, client.post("/api/plan", json={"scenario": sc, "params": p}).json()["job_id"])
    assert st["status"] == "done"
    pid = st["plan_id"]
    r = client.get(f"/api/plans/{pid}").json()
    assert r["status"] == "infeasible" and r["recommendations"]
    assert client.get(f"/api/plans/{pid}/export/kml").status_code == 409
    assert client.get(f"/api/plans/{pid}/brief").status_code == 409


def test_fleet_advice_api_and_full_recheck(qa_client):
    client, api = qa_client
    p = parameters(deadline_h=3).model_dump(mode="json")
    st = wait_job(client, api, client.post("/api/advise", json={"scenario": small_scenario(), "params": p}).json()["job_id"])
    assert st["status"] == "done" and st["advice"]["recommended"]
    advice = st["advice"]
    assert [pt["n"] for pt in advice["points"]] == [1, 2]
    rec = advice["recommended"]
    p["fleet"] = [{"model": k, "count": n} for k, n in rec["fleet"].items() if n]
    result = plan(parse_geojson(small_scenario()), MissionParams.model_validate(p))
    assert result["status"] == "ok" and result["validation"]["passed"]


@pytest.mark.parametrize("case_id,expected", [
    ("02_no_sites", "ok"), ("03_permanent_zone", "ok"), ("04_temporary_zone", "ok"),
    ("05_high_zone", "ok"), ("06_obstacle", "ok"), ("07_remote_site", "infeasible"),
    ("08_airspace", "ok"), ("09_polygon_hole", "ok"), ("13_separate_areas", "ok"),
    ("14_unknown_temporary", "infeasible"),
])
def test_prepared_scenarios(case_id, expected):
    p = parameters()
    sc = parse_geojson(fixture_scenarios()[case_id])
    r = plan(sc, p)
    assert r["status"] == expected
    if expected == "ok":
        assert validate_plan(r, sc, p)["passed"]
        assert r["summary"]["coverage_allowed_pct"] >= 95
    else:
        assert r["recommendations"]
    if case_id == "02_no_sites":
        assert r["sites"] and all(st["auto"] for st in r["sites"])
    if case_id == "04_temporary_zone":
        t = datetime.fromisoformat("2026-09-25T11:00:00+03:00").timestamp()
        assert min(s["t_takeoff"] for s in r["sorties"]) > t
    if case_id == "08_airspace":
        assert max(w[3] for s in r["sorties"] for w in s["waypoints"]) <= 180.2


def test_unknown_temporary_can_be_explicitly_ignored():
    sc = parse_geojson(fixture_scenarios()["14_unknown_temporary"])
    r = plan(sc, parameters(temp_restrictions="ignore"))
    assert r["status"] == "ok" and r["validation"]["passed"]


@pytest.mark.parametrize("survey,model", [("multispectral", "geoscan_gemini"),
    ("ir", "geoscan_801"), ("lidar", "geoscan_gemini"), ("geophysics", "geoscan_gemini")])
def test_all_payload_types(survey, model):
    r = plan(parse_geojson(small_scenario()), parameters(survey_type=survey, fleet=[{"model": model, "count": 2}]))
    assert r["status"] == "ok" and r["validation"]["passed"]
    assert set(r["models"]) == {model}
    if survey in {"multispectral", "ir"}:
        assert r["summary"]["photos"] > 0
    else:
        assert r["summary"]["photos"] == 0


@pytest.mark.parametrize("reserve", [20, 30, 60])
def test_configurable_battery_reserve(reserve):
    r = plan(parse_geojson(small_scenario()), parameters(reserve_pct=reserve))
    assert r["status"] == "ok" and r["validation"]["passed"]
    assert max(s["battery_used_pct"] for s in r["sorties"]) <= 100 - reserve + 0.1


def test_export_values_and_complete_archive(basic_plan):
    r, _, _ = basic_plan
    cat = load_catalog()
    gj = export.to_geojson(r)
    routes = [f for f in gj["features"] if f["properties"]["feature_type"] == "route"]
    assert len(routes) == len(r["sorties"])
    for route, sortie in zip(routes, r["sorties"]):
        assert route["geometry"]["coordinates"] == [w[:3] for w in sortie["waypoints"]]
        assert len(route["properties"]["coord_times"]) == len(sortie["waypoints"])
        assert route["properties"]["survey_speed_ms"] == sortie["survey_speed_ms"]
    tree = ET.fromstring(export.to_kml(r))
    ns = {"k": "http://www.opengis.net/kml/2.2", "gx": "http://www.google.com/kml/ext/2.2"}
    tracks = tree.findall(".//gx:Track", ns)
    assert len(tracks) == len(r["sorties"])
    for tr, s in zip(tracks, r["sorties"]):
        assert len(tr.findall("k:when", ns)) == len(tr.findall("gx:coord", ns)) == len(s["waypoints"])
    archive = zipfile.ZipFile(io.BytesIO(export.to_zip(r, cat)))
    assert archive.testzip() is None
    assert {"plan.geojson", "plan.kml", "schedule.csv", "summary.json", "brief.html", "README.txt"} <= set(archive.namelist())
    assert len([n for n in archive.namelist() if n.startswith("qgc/")]) == len(r["sorties"])
    for d in r["drones"]:
        if not d["sorties"]:
            continue
        one = export.subset(r, d["id"])
        assert all(s["drone_id"] == d["id"] for s in one["sorties"])
        assert f"drones/{d['id']}_brief.html" in archive.namelist()
    for s in r["sorties"]:
        qgc = export.to_qgc_plan(r, s, cat)
        assert qgc["mission"]["plannedHomePosition"] == [s["waypoints"][0][1], s["waypoints"][0][0], s["waypoints"][0][2]]
        ids = [i["doJumpId"] for i in qgc["mission"]["items"]]
        assert ids == list(range(1, len(ids) + 1))


def test_brief_escapes_user_content(basic_plan):
    from planner.brief import to_brief_html
    r = copy.deepcopy(basic_plan[0])
    r["scenario_name"] = '<script>alert("qa")</script>'
    html = to_brief_html(r)
    assert '<script>alert("qa")</script>' not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize("kind", ["no_fly", "airspace"])
@pytest.mark.parametrize("geom", [Point(0, 0), LineString([(-100, 0), (100, 0)])], ids=["point", "line"])
def test_safety_layer_with_wrong_geometry_must_be_rejected(qa_client, kind, geom):
    client, _ = qa_client
    response = upload(client, small_scenario(extra=[feature(kind, geom)]))
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("kind", ["site", "reserve_site"])
def test_empty_site_must_be_validation_error(qa_client, kind):
    client, _ = qa_client
    sc = small_scenario()
    sc["features"].append({"type": "Feature", "properties": {"kind": kind},
                           "geometry": {"type": "Point", "coordinates": []}})
    assert upload(client, sc).status_code == 422


def test_duplicate_site_names_must_be_rejected(qa_client):
    client, _ = qa_client
    assert upload(client, fixture_scenarios()["15_duplicate_sites"]).status_code == 422


def test_damaged_history_entry_must_not_break_list(qa_client):
    client, api = qa_client
    (api.PLANS / "damaged.json").write_text('{"summary": {}}', encoding="utf-8")
    response = client.get("/api/plans")
    assert response.status_code == 200
    assert response.json() == []


def test_validator_rejects_impossible_speed(basic_plan):
    r, sc, p = basic_plan
    bad = {"sorties": [copy.deepcopy(r["sorties"][0])]}
    sortie = bad["sorties"][0]
    t0 = sortie["t_takeoff"]
    for w in sortie["waypoints"]:
        w[4] = t0 + (w[4] - t0) / 100
    sortie["t_landing"] = sortie["waypoints"][-1][4]
    assert not validate_plan(bad, sc, p)["passed"]


@pytest.mark.parametrize("status,passed", [("infeasible", True), ("ok", False)])
def test_partial_or_unvalidated_plan_cannot_be_exported(qa_client, basic_plan, status, passed):
    client, api = qa_client
    r = copy.deepcopy(basic_plan[0])
    r.update(id="partial", status=status)
    r["validation"]["passed"] = passed
    (api.PLANS / "partial.json").write_text(json.dumps(r), encoding="utf-8")
    for suffix in ["export/kml", "export/geojson", "export/csv", "export/zip", "export/qgc?sortie=1", "brief"]:
        assert client.get(f"/api/plans/partial/{suffix}").status_code == 409


@pytest.mark.parametrize("entry", [[], None, {"id": "broken", "params": None}, {"id": "other"}])
def test_history_skips_invalid_records(qa_client, entry):
    client, api = qa_client
    (api.PLANS / "broken.json").write_text(json.dumps(entry), encoding="utf-8")
    assert client.get("/api/plans").json() == []


def test_search_survives_one_unschedulable_allocation(monkeypatch):
    optimizer = importlib.import_module("planner.optimizer")
    original = optimizer.schedule_estimate
    calls = 0

    def one_bad_allocation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise optimizer.SchedulingError("No daylight window for this allocation")
        return original(*args, **kwargs)

    monkeypatch.setattr(optimizer, "schedule_estimate", one_bad_allocation)
    planner = optimizer.Planner(parse_geojson(small_scenario()), parameters())
    r = planner.run()
    assert calls > 1 and planner.search_log[0]["scheduling_error"]
    assert r["status"] == "ok" and r["validation"]["passed"]


def test_validator_rejects_excessive_vertical_rate(basic_plan):
    r, sc, p = basic_plan
    bad = {"sorties": [copy.deepcopy(r["sorties"][0])]}
    s = bad["sorties"][0]
    i = next(i for i, (a, b) in enumerate(zip(s["waypoints"], s["waypoints"][1:])) if b[2] - a[2] > 20)
    a, b = s["waypoints"][i:i + 2]
    shift = (b[4] - a[4]) * .99
    for w in s["waypoints"][i + 1:]:
        w[4] -= shift
    s["t_landing"] -= shift
    check = next(c for c in validate_plan(bad, sc, p)["checks"] if c["name"] == "Скорости и вертикальная динамика")
    assert not check["passed"]


def test_remote_site_refusal_is_valid_json_over_api(qa_client):
    client, api = qa_client
    sc = fixture_scenarios()["07_remote_site"]
    st = wait_job(client, api, client.post("/api/plan", json={"scenario": sc, "params": parameters().model_dump(mode="json")}).json()["job_id"])
    response = client.get(f"/api/plans/{st['plan_id']}")
    assert response.status_code == 200
    r = response.json()
    assert r["status"] == "infeasible" and not r["sorties"]
    json.dumps(r, allow_nan=False)


def test_cloud_ceiling_uses_safe_descent_rate():
    r = plan(parse_geojson(small_scenario()), parameters(cloud_base_m=200))
    assert r["status"] == "ok" and r["validation"]["passed"]


def test_independent_validation_restores_parameter_sites(qa_client):
    client, api = qa_client
    sc = small_scenario()
    site = sc["features"].pop()
    p = parameters(extra_sites=[{"name": "Moved", "lon": site["geometry"]["coordinates"][0],
                                 "lat": site["geometry"]["coordinates"][1]}]).model_dump(mode="json")
    st = wait_job(client, api, client.post("/api/plan", json={"scenario": sc, "params": p}).json()["job_id"])
    r = client.get(f"/api/plans/{st['plan_id']}").json()
    assert r["status"] == "ok"
    response = client.post("/api/validate", json={"plan": r, "scenario": sc, "params": p})
    assert response.status_code == 200 and response.json()["passed"]


def test_auto_sites_validate_against_original_input(qa_client):
    client, api = qa_client
    sc = fixture_scenarios()["02_no_sites"]
    p = parameters().model_dump(mode="json")
    st = wait_job(client, api, client.post("/api/plan", json={"scenario": sc, "params": p}).json()["job_id"])
    r = client.get(f"/api/plans/{st['plan_id']}").json()
    assert r["status"] == "ok" and all(s["auto"] for s in r["sites"])
    v = client.post("/api/validate", json={"plan": r, "scenario": sc, "params": p})
    assert v.status_code == 200 and v.json()["passed"]
