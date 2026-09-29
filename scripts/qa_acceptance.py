r"""Reproducible acceptance scenarios, demo runs and seeded stress checks.

PowerShell: .\.venv\Scripts\python.exe scripts/qa_acceptance.py --fixtures
             .\.venv\Scripts\python.exe scripts/qa_acceptance.py --run --stress 20
Large organizer demo: add --moscow. Real DEM for demos: add --terrain.
Evidence and complete plans are written under out/qa (no production history).
"""
from __future__ import annotations

import argparse
import copy
import json
import platform
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shapely.geometry import Point, Polygon, box, mapping

from planner.geo import LocalFrame, parse_geojson
from planner.models import MissionParams
from planner.optimizer import plan
from planner.validate import validate_plan

FIXTURES = ROOT / "scenarios" / "qa"
START = "2026-09-25T09:00:00+03:00"
FLEET = [{"model": "geoscan_201", "count": 2},
         {"model": "geoscan_gemini", "count": 4},
         {"model": "geoscan_801", "count": 2}]


def parameters(**overrides):
    return MissionParams(**{
        "fleet": [{"model": "geoscan_gemini", "count": 2}],
        "start_time": START, "wind_speed_ms": 0, "use_terrain": False,
        "respect_daylight": True, "search_effort": "fast", "optimizer": "grid",
        **overrides,
    })


def feature(kind, geom, frame=None, **props):
    frame = frame or LocalFrame(38, 55)
    return {"type": "Feature", "properties": {"kind": kind, **props},
            "geometry": mapping(frame.geom_to_ll(geom))}


def small_scenario(name="QA: квадрат 1 км²", extra=(), sites=True, frame=None):
    frame = frame or LocalFrame(38, 55)
    feats = [feature("survey_area", box(-500, -500, 500, 500), frame, name="Участок")]
    if sites:
        feats.append(feature("site", Point(-650, 0), frame, name="S"))
    return {"type": "FeatureCollection", "name": name,
            "planning_hint": {"start_time": START}, "features": feats + list(extra)}


def fixture_scenarios():
    base = small_scenario()
    no_sites = small_scenario("QA: автоматические площадки", sites=False)
    hole = copy.deepcopy(base)
    hole["name"] = "QA: область с отверстием"
    hole["features"][0] = feature("survey_area", Polygon(
        box(-500, -500, 500, 500).exterior.coords,
        [box(-100, -100, 100, 100).exterior.coords]), name="Участок с отверстием")
    remote = small_scenario("QA: недостижимая площадка", sites=False)
    remote["features"].append(feature("site", Point(-40000, 0), name="Дальняя S"))
    empty_site = copy.deepcopy(base)
    empty_site["features"][1]["geometry"] = {"type": "Point", "coordinates": []}
    duplicate = small_scenario("QA: повтор имени площадки", extra=[
        feature("site", Point(650, 0), name="S")])
    return {
        "01_basic": base,
        "02_no_sites": no_sites,
        "03_permanent_zone": small_scenario("QA: постоянная зона", [
            feature("no_fly", Point(0, 0).buffer(120), name="Запрет")]),
        "04_temporary_zone": small_scenario("QA: временная зона", [
            feature("no_fly", box(-1000, -1000, 1000, 1000), name="Временная",
                    active_from="2026-09-25T09:00:00+03:00",
                    active_to="2026-09-25T11:00:00+03:00")]),
        "05_high_zone": small_scenario("QA: зона выше коридора", [
            feature("no_fly", box(-1000, -1000, 1000, 1000), name="Высокая",
                    alt_min_m=800, alt_max_m=2000)]),
        "06_obstacle": small_scenario("QA: высокая вышка", [
            feature("obstacle", Point(0, 0), name="Вышка", height_m=200, radius_m=15)]),
        "07_remote_site": remote,
        "08_airspace": small_scenario("QA: разрешенный коридор 180 м", [
            feature("airspace", box(-1000, -1000, 1000, 1000), ceiling_m=180)]),
        "09_polygon_hole": hole,
        "10_invalid_zone_geometry": small_scenario("QA-B01: ошибочная геометрия зоны", [
            feature("no_fly", Point(0, 0), name="Зона-точка")]),
        "11_empty_site": empty_site,
        "12_polar_terrain": small_scenario("QA: ЦМР вне покрытия", frame=LocalFrame(38, 89)),
        "13_separate_areas": small_scenario("QA: раздельные участки", [
            feature("survey_area", box(1000, -200, 1400, 200), name="Участок 2")]),
        "14_unknown_temporary": small_scenario("QA: TEMP без времени", [
            feature("no_fly", box(-1000, -1000, 1000, 1000), name="TEMP", category="TEMP")]),
        "15_duplicate_sites": duplicate,
    }


def write_fixtures():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, sc in fixture_scenarios().items():
        (FIXTURES / f"{name}.geojson").write_text(
            json.dumps(sc, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, kind, geom, props in [
        ("area", "survey_area", box(-500, -500, 500, 500), {}),
        ("zones", "no_fly", Point(0, 0).buffer(120), {}),
        ("obstacles", "obstacle", Point(250, 0), {"height_m": 80, "radius_m": 10}),
    ]:
        gj = feature(kind, geom, name=name, **props)
        g = gj["geometry"]
        coords = g["coordinates"][0] if g["type"] == "Polygon" else [g["coordinates"]]
        text = " ".join(f"{x},{y},0" for x, y in coords)
        geometry = (f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{text}</coordinates>"
                    "</LinearRing></outerBoundaryIs></Polygon>" if g["type"] == "Polygon" else
                    f"<Point><coordinates>{text}</coordinates></Point>")
        attrs = "".join(f'<Data name="{k}"><value>{v}</value></Data>'
                        for k, v in gj["properties"].items())
        kml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
               f"<Placemark><ExtendedData>{attrs}</ExtendedData>{geometry}</Placemark></Document></kml>")
        (FIXTURES / f"layer_{name}.kml").write_text(kml, encoding="utf-8")
    (FIXTURES / "invalid_json.geojson").write_text('{"type":', encoding="utf-8")
    (FIXTURES / "params_base.json").write_text(
        parameters().model_dump_json(indent=2), encoding="utf-8")
    print(f"Wrote acceptance inputs to {FIXTURES}", flush=True)


def run_case(case_id, source, params, output, expected="ok"):
    started = time.perf_counter()
    entry = {"id": case_id, "expected": expected, "params": params.model_dump(mode="json")}
    try:
        sc = parse_geojson(copy.deepcopy(source))
        result = plan(sc, params)
        independent = validate_plan(result, sc, params) if result.get("sorties") else None
        entry.update(status=result["status"], summary=result.get("summary"),
                     validation=result.get("validation"), independent_validation=independent,
                     error=result.get("error"), warnings=result.get("warnings"))
        entry["passed"] = (result["status"] == expected and
                           (expected != "ok" or bool(independent and independent["passed"])))
        (output / f"{case_id}_plan.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        entry.update(status="exception", passed=False,
                     error=f"{type(exc).__name__}: {exc}")
    entry["elapsed_s"] = round(time.perf_counter() - started, 3)
    summary = entry.get("summary") or {}
    print(f"{case_id}: {entry['status']}, {entry['elapsed_s']} s, "
          f"sorties={summary.get('sorties')}, checks="
          f"{(entry.get('independent_validation') or {}).get('passed')}", flush=True)
    return entry


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--terrain", action="store_true", help="Real DEM for demo cases")
    ap.add_argument("--moscow", action="store_true", help="Include large organizer demo")
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--only-moscow", action="store_true")
    scope.add_argument("--demos-only", action="store_true", help="Skip small and stress cases")
    scope.add_argument("--prepared-only", action="store_true", help="Run the prepared manual inputs only")
    scope.add_argument("--manual-only", action="store_true", help="Run manual parameter variants only")
    ap.add_argument("--stress", type=int, default=0)
    ap.add_argument("--seed", type=int, default=28)
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "qa")
    args = ap.parse_args()
    if args.fixtures:
        write_fixtures()
    if not args.run:
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    entries = []
    meta = {"started_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
            "platform": platform.platform(), "seed": args.seed,
            "terrain_for_demos": args.terrain, "cases": entries}

    def save():
        meta["finished_at"] = datetime.now(timezone.utc).isoformat()
        (args.out / "results.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.prepared_only:
        sources = fixture_scenarios()
        for name in ["01_basic", "02_no_sites", "03_permanent_zone", "04_temporary_zone", "05_high_zone",
                     "06_obstacle", "07_remote_site", "08_airspace", "09_polygon_hole", "13_separate_areas",
                     "14_unknown_temporary"]:
            entries.append(run_case(name, sources[name], parameters(), args.out,
                expected="infeasible" if name in {"07_remote_site", "14_unknown_temporary"} else "ok"))
            save()
        entries.append(run_case("14_unknown_temporary_ignored", sources["14_unknown_temporary"],
            parameters(temp_restrictions="ignore"), args.out))
        save()
        entries.append(run_case("12_polar_terrain", sources["12_polar_terrain"],
            parameters(use_terrain=True, respect_daylight=False), args.out, expected="infeasible"))
        save()
    if args.manual_only:
        for name, opts, expected in [
            ("gsd_3", {"gsd_cm": 3}, "ok"),
            ("gsd_4", {"gsd_cm": 4}, "ok"),
            ("overlap_80_60", {"gsd_cm": 3, "front_overlap": .8, "side_overlap": .6}, "ok"),
            ("plane_gsd_1", {"gsd_cm": 1, "fleet": [{"model": "geoscan_201", "count": 1}]}, "infeasible"),
            ("wind_15", {"wind_speed_ms": 15}, "infeasible"),
            ("cloud_200", {"cloud_base_m": 200}, "ok"),
            ("night_start", {"start_time": "2026-09-25T23:00:00+03:00"}, "ok"),
        ]:
            entries.append(run_case(name, small_scenario(), parameters(**opts), args.out, expected=expected))
            save()
    demos = ([] if args.prepared_only or args.manual_only else ["demo_moscow_task"] if args.only_moscow else
             ["demo_toksovo", "demo_pipeline", "demo_primorye"])
    if args.moscow and not (args.only_moscow or args.prepared_only or args.manual_only):
        demos.append("demo_moscow_task")
    for demo in demos:
        source = json.loads((ROOT / "scenarios" / f"{demo}.geojson").read_text(encoding="utf-8"))
        for objective in (("time",) if demo == "demo_moscow_task" else ("time", "flight")):
            entries.append(run_case(f"{demo}_{objective}", source,
                parameters(fleet=FLEET, wind_speed_ms=5, wind_dir_deg=250,
                           start_time=source.get("planning_hint", {}).get("start_time", START),
                           objective=objective, use_terrain=args.terrain), args.out))
            save()
    if not (args.only_moscow or args.demos_only or args.prepared_only or args.manual_only):
        sc = small_scenario()
        for survey, model in [("rgb", "geoscan_gemini"), ("multispectral", "geoscan_gemini"),
                              ("ir", "geoscan_801"), ("lidar", "geoscan_gemini"),
                              ("geophysics", "geoscan_gemini")]:
            entries.append(run_case(f"survey_{survey}", sc,
                parameters(survey_type=survey, fleet=[{"model": model, "count": 2}]), args.out))
            save()
        for name, opts in [("no_flight_plan", {"airspace_mode": "no_flight_plan"}),
                           ("calibrated", {"energy_model": "calibrated"}),
                           ("physical", {"wind_model": "physical", "wind_speed_ms": 4}),
                           ("bayes", {"optimizer": "bayes"})]:
            entries.append(run_case(name, sc, parameters(**opts), args.out))
            save()
        from scripts.stress import random_params, random_scenario
        rng = random.Random(args.seed)
        for i in range(args.stress):
            source = random_scenario(rng)
            p = random_params(rng).model_copy(update={"use_terrain": False})
            entry = run_case(f"stress_{i + 1:02d}", source, p, args.out, expected="ok-or-infeasible")
            entry["passed"] = (entry["status"] in ("ok", "infeasible") and
                               (entry["status"] != "ok" or
                                bool((entry.get("independent_validation") or {}).get("passed"))))
            entries.append(entry)
            save()
    counts = {"cases": len(entries), "passed": sum(e["passed"] for e in entries),
              "ok": sum(e["status"] == "ok" for e in entries),
              "infeasible": sum(e["status"] == "infeasible" for e in entries),
              "exceptions": sum(e["status"] == "exception" for e in entries)}
    meta["counts"] = counts
    save()
    print(json.dumps(counts), flush=True)
    return int(not all(e["passed"] for e in entries))


if __name__ == "__main__":
    raise SystemExit(main())
