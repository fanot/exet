"""Audit every mission parameter on the complete Moscow organizer scenario.

Results are isolated under out/qa_moscow_params. Full plans are gzip compressed.
Run: .\\.venv\\Scripts\\python.exe -X utf8 scripts/qa_moscow_params.py --workers 3
Resume skips finished cases. --case CASE runs a single case for reproduction.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError

from planner.geo import parse_geojson
from planner.models import MissionParams
from planner.optimizer import Planner
from planner.validate import validate_plan

SOURCE = ROOT / "scenarios" / "demo_moscow_task.geojson"
OUT = ROOT / "out" / "qa_moscow_params"
FLEET = [{"model": "geoscan_201", "count": 2},
         {"model": "geoscan_gemini", "count": 4},
         {"model": "geoscan_801", "count": 2}]
BASE = dict(fleet=FLEET, start_time="2026-09-25T08:00:00+03:00",
            wind_speed_ms=5, wind_dir_deg=250, use_terrain=True,
            search_effort="fast", optimizer="grid")


def one(model, count=2, batteries=None):
    item = {"model": "geoscan_" + model, "count": count}
    if batteries is not None:
        item["batteries"] = batteries
    return [item]


def cases():
    definitions = [
        ("00_baseline", {}, "baseline"),
        ("01_flat", {"use_terrain": False}, "terrain"),
        ("02_min_flight", {"objective": "flight"}, "objective"),
        ("03_ignore_temporary", {"temp_restrictions": "ignore"}, "zones"),
        ("04_calibrated", {"energy_model": "calibrated"}, "energy"),
        ("05_physical_wind", {"wind_model": "physical"}, "wind"),
        ("06_calm_north", {"wind_speed_ms": 0, "wind_dir_deg": 0}, "wind"),
        ("07_wind_6_5_east", {"wind_speed_ms": 6.5, "wind_dir_deg": 90}, "wind"),
        ("08_wind_9_south", {"wind_speed_ms": 9, "wind_dir_deg": 180}, "wind"),
        ("09_wind_40_max_dir", {"wind_speed_ms": 40, "wind_dir_deg": 359.9}, "wind"),
        ("10_multispectral", {"survey_type": "multispectral"}, "survey"),
        ("11_ir", {"survey_type": "ir"}, "survey"),
        ("12_lidar", {"survey_type": "lidar"}, "survey"),
        ("13_geophysics", {"survey_type": "geophysics"}, "survey"),
        ("14_plane_one_battery", {"fleet": one("201", 1, 1)}, "fleet"),
        ("15_gemini_one_battery", {"fleet": one("gemini", 1, 1)}, "fleet"),
        ("16_801_two_batteries", {"fleet": one("801", 2, 2)}, "fleet"),
        ("17_zero_counts", {"fleet": [{"model": "geoscan_201", "count": 0},
                                          {"model": "geoscan_gemini", "count": 2},
                                          {"model": "geoscan_801", "count": 0}]}, "fleet"),
        ("18_fleet_30_battery_20", {"fleet": [{"model": f["model"], "count": 10, "batteries": 20} for f in FLEET]}, "fleet"),
        ("19_gsd_0_31", {"gsd_cm": 0.31, "wind_speed_ms": 0}, "gsd"),
        ("20_gsd_0_5", {"gsd_cm": 0.5, "wind_speed_ms": 0, "fleet": one("gemini")}, "gsd"),
        ("21_gsd_6", {"gsd_cm": 6, "wind_speed_ms": 4}, "gsd"),
        ("22_gsd_200", {"gsd_cm": 200, "wind_speed_ms": 0}, "gsd"),
        ("23_overlap_min", {"front_overlap": 0.7, "side_overlap": 0.5}, "overlap"),
        ("24_overlap_max", {"front_overlap": 0.95, "side_overlap": 0.9, "gsd_cm": 6,
                             "wind_speed_ms": 0, "fleet": one("gemini")}, "overlap"),
        ("25_cloud_51", {"cloud_base_m": 51, "wind_speed_ms": 0}, "cloud"),
        ("26_cloud_130", {"cloud_base_m": 130, "wind_speed_ms": 0}, "cloud"),
        ("27_cloud_5000", {"cloud_base_m": 5000, "wind_speed_ms": 0, "fleet": one("gemini")}, "cloud"),
        ("28_night_unrestricted", {"respect_daylight": False, "start_time": "2026-09-25T23:00:00+03:00"}, "daylight"),
        ("29_winter", {"start_time": "2026-12-21T09:00:00+03:00", "fleet": one("gemini")}, "daylight"),
        ("30_sun_60", {"min_sun_elevation_deg": 60, "start_time": "2026-06-21T12:00:00+03:00"}, "daylight"),
        ("31_sun_minus_5_utc", {"min_sun_elevation_deg": -5, "start_time": "2026-09-25T05:00:00Z",
                                  "fleet": one("gemini")}, "daylight"),
        ("32_reserve_60", {"reserve_pct": 60, "wind_speed_ms": 0, "fleet": one("gemini")}, "reserve"),
        ("33_deadline_0_01", {"objective": "flight", "deadline_h": 0.01, "fleet": one("gemini")}, "deadline"),
        ("34_deadline_240", {"objective": "flight", "deadline_h": 240, "respect_daylight": False,
                              "fleet": one("gemini")}, "deadline"),
        ("35_takeoff_weight_5", {"objective": "flight", "takeoff_weight_h": 5}, "takeoff"),
        ("36_no_plan_vlos_2", {"airspace_mode": "no_flight_plan"}, "airspace"),
        ("37_no_plan_vlos_0_3", {"airspace_mode": "no_flight_plan", "vlos_radius_km": 0.3,
                                 "survey_type": "lidar"}, "airspace"),
        ("38_no_plan_vlos_10", {"airspace_mode": "no_flight_plan", "vlos_radius_km": 10,
                                "survey_type": "lidar", "wind_speed_ms": 2,
                                "energy_model": "calibrated", "wind_model": "physical"}, "airspace"),
        ("39_extra_site_near", {"extra_sites": [{"lon": 38.8, "lat": 55.0, "name": "QA-center"}]}, "sites"),
        ("40_extra_site_remote", {"extra_sites": [{"lon": 30.0, "lat": 60.0, "name": "QA-remote"}]}, "sites"),
        ("41_extra_site_duplicate", {"extra_sites": [{"lon": 38.8, "lat": 55.0, "name": "QA-duplicate"},
                                                      {"lon": 38.9, "lat": 55.1, "name": "QA-duplicate"}]}, "sites"),
        ("42_normal_grid", {"search_effort": "normal", "survey_type": "lidar"}, "search"),
        ("43_thorough_grid", {"search_effort": "thorough", "survey_type": "lidar"}, "search"),
        ("44_fast_bayes", {"optimizer": "bayes"}, "search"),
        ("45_normal_bayes", {"optimizer": "bayes", "search_effort": "normal", "survey_type": "lidar"}, "search"),
        ("46_thorough_bayes", {"optimizer": "bayes", "search_effort": "thorough", "survey_type": "ir",
                               "gsd_cm": 200, "wind_speed_ms": 0}, "search"),
        ("47_api_defaults", {}, "defaults"),
        ("48_combined", {"survey_type": "lidar", "gsd_cm": 5, "front_overlap": 0.85, "side_overlap": 0.6,
                          "fleet": one("gemini", 4, 3), "objective": "flight", "deadline_h": 240,
                          "takeoff_weight_h": 0.5, "start_time": "2026-06-25T12:00:00+03:00",
                          "wind_speed_ms": 2, "wind_dir_deg": 270, "cloud_base_m": 500,
                          "respect_daylight": True, "min_sun_elevation_deg": 5, "use_terrain": True,
                          "energy_model": "calibrated", "wind_model": "physical", "temp_restrictions": "ignore",
                          "airspace_mode": "no_flight_plan", "vlos_radius_km": 5, "reserve_pct": 30,
                          "extra_sites": [{"lon": 38.8, "lat": 55.0, "name": "QA-combined"}],
                          "search_effort": "normal", "optimizer": "bayes"}, "combined"),
        ("49_combined_ir_no_plan", {"survey_type": "ir", "gsd_cm": 50, "fleet": one("801", 3, 2),
                                     "airspace_mode": "no_flight_plan", "vlos_radius_km": 5,
                                     "respect_daylight": False, "start_time": "2026-09-25T23:00:00+03:00",
                                     "cloud_base_m": 200, "reserve_pct": 40, "wind_speed_ms": 3,
                                     "energy_model": "calibrated", "optimizer": "bayes"}, "combined"),
        ("50_non_camera_overlap_min", {"survey_type": "lidar", "front_overlap": 0.5,
                                         "side_overlap": 0.0, "gsd_cm": 3}, "overlap"),
        ("51_fleet_20_gemini", {"fleet": one("gemini", 20, 20), "survey_type": "lidar"}, "fleet"),
    ]
    result = []
    for case_id, changes, group in definitions:
        raw = dict(fleet=FLEET) if case_id == "47_api_defaults" else copy.deepcopy(BASE)
        raw.update(changes)
        result.append(dict(id=case_id, group=group, overrides=changes, params=raw))
    return result


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def worker(case, force=False):
    folder = OUT / case["id"]
    record_path = folder / "result.json"
    if record_path.exists() and not force:
        return json.loads(record_path.read_text(encoding="utf-8"))
    folder.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    record = dict(id=case["id"], group=case["group"], started_at=datetime.now(timezone.utc).isoformat(),
                  overrides=case["overrides"], requested_params=case["params"])
    last_progress = -10.0
    planner = None

    def progress(fraction, message):
        nonlocal last_progress
        elapsed = time.monotonic() - started
        if elapsed - last_progress >= 3 or fraction >= 1:
            write_json(folder / "progress.json", dict(progress=round(fraction, 3), message=message,
                       elapsed_s=round(elapsed, 1), evaluations=len(planner.search_log) if planner else 0))
            last_progress = elapsed

    try:
        params = MissionParams.model_validate(case["params"])
        record["params"] = params.model_dump(mode="json")
        write_json(folder / "params.json", record["params"])
        raw = json.loads(SOURCE.read_text(encoding="utf-8"))
        planner = Planner(parse_geojson(copy.deepcopy(raw)), params, progress)
        result = planner.run()
        with gzip.open(folder / "plan.json.gz", "wt", encoding="utf-8") as file:
            json.dump(result, file, ensure_ascii=False, allow_nan=False)
        record.update(status=result.get("status"), error=result.get("error"), summary=result.get("summary"),
                      validation=result.get("validation"), recommendations=result.get("recommendations"),
                      warnings=result.get("warnings"), excluded=result.get("excluded"),
                      evaluations=len(planner.search_log), auto_sites=len(getattr(planner, "auto_site_names", ())),
                      terrain_enabled=planner.terrain.enabled if hasattr(planner, "terrain") else None)
        issues = []
        if result.get("sorties"):
            second_check = validate_plan(result, parse_geojson(copy.deepcopy(raw)), params)
            record["independent_validation"] = second_check
            if second_check["passed"] != result.get("validation", {}).get("passed"):
                issues.append("repeat_validation_disagrees")
        if result.get("status") == "ok":
            if not result.get("validation", {}).get("passed") or not result.get("sorties"):
                issues.append("ok_without_validated_routes")
            if result.get("summary", {}).get("coverage_allowed_pct", 0) < 95:
                issues.append("ok_with_less_than_95pct_coverage")
        if not result.get("sorties") and not result.get("error") and not result.get("recommendations"):
            issues.append("empty_result_without_diagnostic")
        if params.use_terrain and record["terrain_enabled"] is False:
            issues.append("terrain_requested_but_unavailable")
        record["suspicions"] = issues
        record["outcome"] = "completed"
    except Exception as error:
        record.update(outcome="exception", exception=type(error).__name__, error=str(error),
                      traceback=traceback.format_exc())
    record["elapsed_s"] = round(time.monotonic() - started, 2)
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(record_path, record)
    return record


def invalid_cases():
    bad = []
    numeric = {"gsd_cm": [0.3, 200.01], "front_overlap": [0.49, 0.69, 0.951],
               "side_overlap": [-0.01, 0.49, 0.901], "deadline_h": [0, 240.01],
               "takeoff_weight_h": [-0.01, 5.01], "wind_speed_ms": [-0.01, 40.01],
               "wind_dir_deg": [-0.01, 360], "cloud_base_m": [50, 5000.01],
               "min_sun_elevation_deg": [-5.01, 60.01], "vlos_radius_km": [0.299, 10.01],
               "reserve_pct": [19.99, 60.01]}
    for field, values in numeric.items():
        for value in values:
            bad.append((f"{field}_{value}", {field: value}))
        bad.append((field + "_nan", {field: float("nan")}))
        bad.append((field + "_inf", {field: float("inf")}))
    for field in ("survey_type", "objective", "energy_model", "wind_model", "temp_restrictions",
                  "airspace_mode", "search_effort", "optimizer"):
        bad.append((field + "_unknown", {field: "unexpected"}))
    bad.extend([
        ("empty_fleet", {"fleet": []}),
        ("all_zero_fleet", {"fleet": one("gemini", 0)}),
        ("negative_count", {"fleet": one("gemini", -1)}),
        ("count_21", {"fleet": one("gemini", 21)}),
        ("fleet_31", {"fleet": [{"model": "geoscan_201", "count": 11}, {"model": "geoscan_gemini", "count": 20}]}),
        ("battery_0", {"fleet": one("gemini", 1, 0)}),
        ("battery_21", {"fleet": one("gemini", 1, 21)}),
        ("duplicate_model", {"fleet": one("gemini") + one("gemini")}),
        ("unknown_model", {"fleet": [{"model": "unknown", "count": 1}]}),
        ("invalid_start", {"start_time": "invalid"}),
        ("invalid_longitude", {"extra_sites": [{"lon": 180.01, "lat": 55, "name": "bad"}]}),
        ("invalid_latitude", {"extra_sites": [{"lon": 38, "lat": 90.01, "name": "bad"}]}),
    ])
    results = []
    for case_id, changes in bad:
        params = copy.deepcopy(BASE)
        params.update(changes)
        try:
            MissionParams.model_validate(params)
            results.append(dict(id=case_id, rejected=False))
        except ValidationError as error:
            results.append(dict(id=case_id, rejected=True,
                           errors=[dict(location=list(item["loc"]), type=item["type"]) for item in error.errors()]))
    write_json(OUT / "invalid_inputs.json", results)
    return results


def manifest():
    defined = cases()
    raw = SOURCE.read_bytes()
    params_values = {name: [] for name in MissionParams.model_fields}
    for case in defined:
        normalized = MissionParams.model_validate(case["params"]).model_dump(mode="json")
        for name, value in normalized.items():
            if value not in params_values[name]:
                params_values[name].append(value)
    result = dict(scenario=str(SOURCE.relative_to(ROOT)), scenario_sha256=hashlib.sha256(raw).hexdigest(),
                  revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  python=platform.python_version(), platform=platform.platform(),
                  packages={name: importlib.metadata.version(name) for name in ("numpy", "shapely", "pydantic", "optuna")},
                  created_at=datetime.now(timezone.utc).isoformat(), cases=defined, parameter_values=params_values,
                  scope="Complete organizer geometry. Every categorical level; representative numeric values and boundaries. Not exhaustive Cartesian product.")
    write_json(OUT / "manifest.json", result)
    return result


def run_child(case, timeout, force):
    destination = OUT / case["id"]
    destination.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--case", case["id"], "--out", str(OUT)]
    if force:
        command.append("--force")
    started = time.monotonic()
    environment = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    with (destination / "worker.log").open("w", encoding="utf-8") as log:
        try:
            process = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            record = dict(id=case["id"], group=case["group"], outcome="timeout", elapsed_s=round(time.monotonic()-started, 2),
                          requested_params=case["params"], timeout_s=timeout)
            write_json(destination / "result.json", record)
            return record
    path = destination / "result.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return dict(id=case["id"], outcome="worker_failed", exit_code=process.returncode)


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", help="Comma-separated case ids")
    parser.add_argument("--out", type=Path, help="Separate directory for verification runs; preserves the original audit")
    args = parser.parse_args()
    if args.out:
        OUT = args.out.resolve()
    if args.case:
        chosen = next(case for case in cases() if case["id"] == args.case)
        record = worker(chosen, args.force)
        print(json.dumps({key:record.get(key) for key in ("id", "outcome", "status", "elapsed_s", "error", "suspicions")}, ensure_ascii=False), flush=True)
        return
    manifest()
    rejected = invalid_cases()
    print(f"Invalid input rejection: {sum(case['rejected'] for case in rejected)}/{len(rejected)}", flush=True)
    selected = set(args.only.split(",")) if args.only else None
    todo = [case for case in cases() if (selected is None or case["id"] in selected)
            and (args.force or not (OUT/case["id"]/"result.json").exists())]
    print(f"Running {len(todo)} full-scenario cases, workers={args.workers}, timeout={args.timeout}s", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(run_child, case, args.timeout, args.force): case for case in todo}
        for future in as_completed(pending):
            record = future.result()
            summary = record.get("summary") or {}
            print(json.dumps(dict(id=record['id'], outcome=record.get('outcome'), status=record.get('status'),
                  elapsed_s=record.get('elapsed_s'), coverage=summary.get('coverage_allowed_pct'), sorties=summary.get('sorties'),
                  error=record.get('error'), suspicions=record.get('suspicions')), ensure_ascii=False), flush=True)
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(OUT.glob("*/result.json"))]
    write_json(OUT / "results.json", dict(cases=records, invalid_inputs=rejected,
               finished_at=datetime.now(timezone.utc).isoformat()))
    print(f"Saved {len(records)} case results under {OUT}", flush=True)


if __name__ == "__main__":
    main()
