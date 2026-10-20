import hashlib
import importlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from planner.geo import parse_geojson
from planner.optimizer import Planner, plan
from planner.report import mission_report, report_html, report_analytics
from scripts.qa_acceptance import parameters, small_scenario


def test_balanced_search_and_dispatch_share_fixed_normalization():
    p = parameters().model_copy(update={"objective": "balanced"})
    optimizer = Planner(parse_geojson(small_scenario()), p)
    candidates = [(10, 30), (15, 15), (30, 10)]
    optimizer.search_log = [dict(makespan=t, flight=f, n_sorties=1, bad_m=0,
                                score=optimizer._mission_score(t, f, 1)) for t, f in candidates]
    optimizer._calibrate_balance()
    assert optimizer.balance_reference["time_s"] == 10
    assert optimizer.balance_reference["time_max_s"] == 30
    assert optimizer.balance_reference["flight_s"] == 10
    assert optimizer.balance_reference["flight_max_s"] == 30
    assert min(optimizer.search_log, key=lambda r: r["score"])["makespan"] == 15
    optimizer.t0 = 0
    assigned = [SimpleNamespace(start=600, end=615)]
    assert optimizer._actual_score(assigned) == optimizer._mission_score(1215, 15, 1)
    assert optimizer._mission_score(20, 25, 1) < optimizer._mission_score(25, 30, 1)


@pytest.mark.parametrize("method", ["grid", "bayes"])
def test_balanced_plan_passes_independent_validation(method):
    p = parameters().model_copy(update={"objective": "balanced", "optimizer": method})
    r = plan(parse_geojson(small_scenario()), p)
    assert r["status"] == "ok" and r["validation"]["passed"]
    assert r["summary"]["balance_reference"]["time_weight"] == 0.5
    assert r["summary"]["balance_reference"]["method"] == "augmented_chebyshev"
    assert r["summary"]["coverage_allowed_pct"] > 99


def test_report_refusal_missing_values_and_escaping():
    r = {"id": "test", "scenario_name": '<script>alert("x")</script>', "status": "infeasible",
         "error": "Слишком сильный ветер", "params": {"objective": "balanced", "wind_speed_ms": 0},
         "validation": {"passed": False, "checks": [{"name": "Ветер", "passed": False, "details": ["Превышен предел"]}]}}
    report = mission_report(r)
    assert not report["ready"] and report["checks_passed"] == 0
    assert report["metrics"][0][1] == "—"
    assert report["conditions"][0][1] == "0,0 м/с"
    assert "Слишком сильный ветер" in report["issues"]
    html = report_html(r)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "телеметрия не анализировались" in html


def test_report_analytics_weights_time_and_tracks_worst_battery():
    p = {"id": "analytics", "params": {"reserve_pct": 25}, "sorties": [
        {"drone_id": "A", "t_takeoff": 0, "t_landing": 3600, "battery_used_pct": 60,
         "phases": [{"phase": "survey", "t_start": 0, "t_end": 2700},
                    {"phase": "transit_back", "t_start": 2700, "t_end": 3600}]},
        {"drone_id": "A", "t_takeoff": 5000, "t_landing": 6800, "battery_used_pct": 80,
         "phases": [{"phase": "survey", "t_start": 5000, "t_end": 5900},
                    {"phase": "landing", "t_start": 5900, "t_end": 6800}]},
        {"drone_id": "B", "duration_min": 30, "battery_used_pct": 0},
        {"drone_id": "B", "duration_min": 0},
    ]}
    a = report_analytics(p)
    assert a["drones"][0] == {"id": "A", "sorties": 2, "flight_h": 1.5,
                              "remaining_pct": 20, "energy_samples": 2}
    assert a["drones"][1]["remaining_pct"] == 100
    phases = {phase["key"]: phase for phase in a["phases"]}
    assert phases["survey"]["pct"] == 50
    assert phases["unknown"]["hours"] == 0.5
    assert sum(x["pct"] for x in a["phases"]) == 100
    html = report_html(p)
    assert "ниже резерва" in html
    assert "Данные АКБ есть не для всех вылетов" in html
    assert "заданный резерв: 25,0 %" in html


def test_report_charts_preserve_missing_values_and_escape_drone_ids():
    p = {"id": "empty", "params": {}, "sorties": []}
    a = report_analytics(p)
    assert a["drones"] == a["phases"] == []
    assert a["reserve_pct"] is None
    assert "Нет данных об этапах" in report_html(p)
    p["sorties"] = [{"drone_id": "<img src=x>", "duration_min": 0}]
    html = report_html(p)
    assert "<img src=x>" not in html and "&lt;img src=x&gt;" in html
    assert 'width="0.000"' in html


def test_report_phase_chart_excludes_known_ground_time():
    a = report_analytics({"sorties": [{"drone_id": "A", "duration_min": 10,
        "phases": [{"phase": "ground", "t_start": 0, "t_end": 30},
                   {"phase": "survey", "t_start": 30, "t_end": 600}]}]})
    assert a["phases"] == [{"key": "survey", "label": "Съёмка", "hours": 570 / 3600, "pct": 100}]


def test_coverage_timeline_counts_unique_survey_area_and_ignores_transit():
    from shapely.geometry import box, mapping
    from planner.geo import LocalFrame
    from planner.progress import coverage_timeline

    frame = LocalFrame(30, 60)
    ll = lambda x, y: list(frame.to_ll(x, y))
    plan = {"geo": {"requested": mapping(frame.geom_to_ll(box(-20, -20, 120, 30)))},
            "summary": {"coverage_allowed_pct": 90, "area_allowed_km2": 0.007},
            "models": {"test": {"swath_m": 10}}, "sorties": [
                {"model": "test", "t_takeoff": 0, "t_landing": 20,
                 "waypoints": [[*ll(0, 0), 0, 0, 0, "transit_out"],
                               [*ll(100, 0), 0, 0, 10, "survey"],
                               [*ll(100, 20), 0, 0, 20, "transit_back"]]},
                {"model": "test", "t_takeoff": 30, "t_landing": 40,
                 "waypoints": [[*ll(0, 0), 0, 0, 30, "takeoff"],
                               [*ll(100, 0), 0, 0, 40, "survey"]]},
            ]}
    result = coverage_timeline(plan, buckets=8)
    assert len(result["steps"]) == 1
    assert result["steps"][0]["pct"] == 90
    assert result["steps"][0]["covered_km2"] == 0.006
    assert coverage_timeline({"sorties": [], "summary": {}})["steps"] == []


def test_sortie_selection_visibility_regression():
    if not shutil.which("node"):
        pytest.skip("Node.js is required for UI logic checks")
    subprocess.run(["node", "tests/ui_sortie_visibility.cjs"],
                   cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True)


def test_private_session_all_plan_routes_and_jobs(tmp_path, monkeypatch):
    api = importlib.import_module("api.app")
    monkeypatch.setattr(api, "PLANS", tmp_path)
    monkeypatch.setattr(api, "PLAN_SCENARIOS", tmp_path / "scenarios")
    (tmp_path / "scenarios").mkdir()
    monkeypatch.setattr(api, "JOBS", {})
    class FastPlanner:
        search_log = []
        def __init__(self, *args): pass
        def run(self):
            return {"id": "private", "status": "infeasible", "params": {}, "summary": {},
                    "validation": {}, "scenario_name": "Private area"}
    monkeypatch.setattr(api, "Planner", FastPlanner)
    with TestClient(api.app) as alice, TestClient(api.app) as bob:
        session = alice.get("/api/session")
        assert "HttpOnly" in session.headers["set-cookie"] and "SameSite=lax" in session.headers["set-cookie"]
        assert session.json()["id"] != bob.get("/api/session").json()["id"]
        payload = {"scenario": small_scenario(), "params": parameters().model_dump(mode="json")}
        job_id = alice.post("/api/plan", json=payload).json()["job_id"]
        for _ in range(100):
            if alice.get(f"/api/jobs/{job_id}").json()["status"] == "done":
                break
            time.sleep(.02)
        else:
            pytest.fail("job did not publish")
        assert alice.get("/api/plans").json()[0]["id"] == "private"
        assert bob.get("/api/plans").json() == []
        for suffix in ["", "/scenario", "/report", "/report/print", "/progress", "/brief", "/export/kml", "/export/zip", "/export/qgc?sortie=1"]:
            assert bob.get("/api/plans/private" + suffix).status_code == 404
        assert bob.delete("/api/plans/private").status_code == 404
        assert bob.get(f"/api/jobs/{job_id}").status_code == 404
        assert bob.post(f"/api/jobs/{job_id}/cancel").status_code == 404
        assert alice.get("/api/plans/private/report").status_code == 200
        assert alice.get("/api/plans/private/report/print").status_code == 200
        assert alice.get("/api/plans/private/progress").json()["steps"] == []
        assert alice.get("/api/plans/private").headers["cache-control"] == "no-store"
        assert "owner" not in alice.get("/api/plans/private").json()
        with TestClient(api.app) as returning:
            returning.cookies.set("gfp_session", alice.cookies.get("gfp_session"))
            assert returning.get("/api/plans/private").status_code == 200
        # Knowing the stored owner hash or plan id cannot recreate the cookie.
        bob.cookies.clear()
        bob.cookies.set("gfp_session", hashlib.sha256(alice.cookies.get("gfp_session").encode()).hexdigest())
        assert bob.get("/api/plans/private").status_code == 404
        (tmp_path / "legacy.json").write_text(json.dumps({"id": "legacy"}))
        assert alice.get("/api/plans/legacy").status_code == 404
        assert alice.post("/api/plan", json=payload, headers={"Origin": "https://unrelated.example"}).status_code == 403
        assert alice.delete("/api/plans/private").status_code == 200
        assert not (tmp_path / "private.owner").exists()


def test_upload_and_advice_jobs_also_have_owners(monkeypatch, tmp_path):
    api = importlib.import_module("api.app")
    monkeypatch.setattr(api, "JOBS", {})
    # Keep the submitted job queued; ownership must be assigned before its worker runs.
    monkeypatch.setattr(api, "threading", SimpleNamespace(Thread=lambda **kw: SimpleNamespace(start=lambda: None)))
    with TestClient(api.app) as alice, TestClient(api.app) as bob:
        alice.get("/api/session")
        p = parameters().model_dump(mode="json")
        advice = alice.post("/api/advise", json={"scenario": small_scenario(), "params": p})
        upload = alice.post("/api/plan/upload", files={"files": ("area.geojson", json.dumps(small_scenario()), "application/json")}, data={"params": json.dumps(p)})
        for response in [advice, upload]:
            assert response.status_code == 200
            job_id = response.json()["job_id"]
            assert alice.get(f"/api/jobs/{job_id}").status_code == 200
            assert bob.get(f"/api/jobs/{job_id}").status_code == 404
            assert bob.post(f"/api/jobs/{job_id}/cancel").status_code == 404
