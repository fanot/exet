import json

import pytest

from fastapi.testclient import TestClient

from api.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_plan_storage(monkeypatch, tmp_path):
    import importlib
    api = importlib.import_module("api.app")
    monkeypatch.setattr(api, "PLANS", tmp_path)
    monkeypatch.setattr(api, "PLAN_SCENARIOS", tmp_path / "scenarios")
    (tmp_path / "scenarios").mkdir()


def test_catalog_and_scenarios():
    assert client.get("/api/health").json() == {"status": "ok"}
    cat = client.get("/api/catalog").json()
    assert {"geoscan_201", "geoscan_gemini", "geoscan_801"} <= set(cat["drones"])
    sc = client.get("/api/scenarios").json()
    assert any(s["id"] == "demo_toksovo" for s in sc)


def test_openapi_documents_units():
    spec = client.get("/openapi.json").json()
    props = spec["components"]["schemas"]["MissionParams"]["properties"]
    assert "м/с" in props["wind_speed_ms"]["description"]
    assert "см" in props["gsd_cm"]["description"]


def test_bad_scenario_422():
    r = client.post("/api/plan", json={"scenario": {"type": "FeatureCollection", "features": []},
                                       "params": {"fleet": [{"model": "geoscan_gemini", "count": 1}]}})
    assert r.status_code == 422


def _wait(job_id, timeout=240):
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/jobs/{job_id}").json()
        if st["status"] in ("done", "error", "cancelled"):
            return st
        time.sleep(0.2)
    raise AssertionError("расчет не завершился")


def _small_plan():
    sc = client.get("/api/scenarios/demo_toksovo").json()
    p = {"fleet": [{"model": "geoscan_201", "count": 1}, {"model": "geoscan_gemini", "count": 2}],
         "start_time": "2026-09-25T09:00:00+03:00", "search_effort": "fast", "use_terrain": False}
    return sc, p


def test_job_progress_export_and_brief():
    """Ход расчета (этап, сходимость), сценарий плана, задания по бортам, печатная форма, точки crash-теста."""
    sc, p = _small_plan()
    st = _wait(client.post("/api/plan", json={"scenario": sc, "params": p}).json()["job_id"])
    assert st["status"] == "done" and st["stage"] == "validate" and st["evaluations"] > 0
    assert st["trace"] and all(len(x) == 4 for x in st["trace"])
    pid = st["plan_id"]
    assert client.get(f"/api/plans/{pid}/scenario").json()["name"] == sc["name"]
    r = client.get(f"/api/plans/{pid}").json()
    sep = next(c for c in r["validation"]["checks"] if "сближ" in c["name"])
    assert all({"lon", "lat", "t", "label"} <= set(pt) for pt in sep.get("points", []))
    d = next(x for x in r["drones"] if x["sorties"])
    assert d["color"].startswith("#")
    kml = client.get(f"/api/plans/{pid}/export/kml", params={"drone": d["id"]})
    assert kml.status_code == 200 and d["id"] in kml.text
    assert client.get(f"/api/plans/{pid}/export/kml", params={"drone": "NOPE"}).status_code == 404
    qgc = client.get(f"/api/plans/{pid}/export/qgc", params={"sortie": d["sorties"][0]})
    assert qgc.status_code == 200 and qgc.json()["fileType"] == "Plan"
    brief = client.get(f"/api/plans/{pid}/brief", params={"drone": d["id"]})
    assert brief.status_code == 200 and "Полетное задание" in brief.text and "<svg" in brief.text
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(client.get(f"/api/plans/{pid}/export/zip").content)).namelist()
    assert f"drones/{d['id']}.kml" in names and "brief.html" in names and "report.html" in names


def test_job_cancel():
    sc, p = _small_plan()
    job = client.post("/api/plan", json={"scenario": sc, "params": {**p, "search_effort": "thorough"}}).json()
    client.post(f"/api/jobs/{job['job_id']}/cancel")
    assert _wait(job["job_id"])["status"] == "cancelled"
