"""Консольный интерфейс.

Пример:
    python -m planner.cli scenarios/demo_toksovo.geojson --fleet geoscan_201=2,geoscan_gemini=4 \
        --objective time --start 2026-09-25T09:00:00+03:00 --wind 5 --wind-dir 250 --out out/
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import export
from .catalog import load_catalog
from .geo import ScenarioError, parse_scenario_bytes
from .models import MissionParams
from .optimizer import PlanningError, plan


def main(argv=None):
    ap = argparse.ArgumentParser(description="Планирование полетов группы БВС (Геоскан)")
    ap.add_argument("scenario", help="Сценарий: .geojson или .kml")
    ap.add_argument("--fleet", default="geoscan_201=2,geoscan_gemini=4,geoscan_801=2", help="модель=кол-во через запятую")
    ap.add_argument("--survey", default="rgb", choices=["rgb", "multispectral", "ir", "lidar", "geophysics"])
    ap.add_argument("--gsd", type=float, default=None, help="GSD, см/пикс")
    ap.add_argument("--objective", default="time", choices=["time", "flight"])
    ap.add_argument("--deadline", type=float, default=None, help="Срок, ч")
    ap.add_argument("--start", default=None, help="Начало работ, ISO 8601")
    ap.add_argument("--wind", type=float, default=4.0, help="Ветер у земли, м/с")
    ap.add_argument("--wind-dir", type=float, default=270.0, help="Откуда дует, градусы")
    ap.add_argument("--no-terrain", action="store_true")
    ap.add_argument("--no-daylight", action="store_true")
    ap.add_argument("--effort", default="normal", choices=["fast", "normal", "thorough"])
    ap.add_argument("--params", default=None, help="JSON-файл с MissionParams (перекрывает флаги)")
    ap.add_argument("--out", default="out", help="Каталог для результатов")
    a = ap.parse_args(argv)

    if a.params:
        params = MissionParams.model_validate_json(Path(a.params).read_text(encoding="utf-8"))
    else:
        fleet = []
        for tok in a.fleet.split(","):
            m, n = tok.split("=")
            fleet.append({"model": m.strip(), "count": int(n)})
        kw = dict(survey_type=a.survey, gsd_cm=a.gsd, objective=a.objective, deadline_h=a.deadline, fleet=fleet,
                  wind_speed_ms=a.wind, wind_dir_deg=a.wind_dir, use_terrain=not a.no_terrain,
                  respect_daylight=not a.no_daylight, search_effort=a.effort)
        if a.start:
            kw["start_time"] = a.start
        params = MissionParams(**kw)
    p = Path(a.scenario)
    try:
        sc = parse_scenario_bytes(p.read_bytes(), p.name)
    except ScenarioError as e:
        print(f"Ошибка сценария: {e}", file=sys.stderr)
        return 2
    t = time.time()

    def prog(f, msg):
        print(f"\r[{f * 100:5.1f}%] {msg:<60}", end="", file=sys.stderr, flush=True)

    try:
        r = plan(sc, params, prog)
    except PlanningError as e:
        print(f"\nПлан невозможен: {e}", file=sys.stderr)
        return 3
    print(file=sys.stderr)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan_full.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    if r.get("sorties"):
        (out / "plan.geojson").write_text(json.dumps(export.to_geojson(r), ensure_ascii=False), encoding="utf-8")
        (out / "plan.kml").write_text(export.to_kml(r), encoding="utf-8")
        (out / "schedule.csv").write_text("﻿" + export.to_csv(r), encoding="utf-8")
        (out / "plan.zip").write_bytes(export.to_zip(r, load_catalog()))
    s = r.get("summary", {})
    print(json.dumps({"status": r["status"], **{k: s.get(k) for k in ("makespan_h", "flight_hours", "sorties", "drones_used",
                                                                     "coverage_allowed_pct", "compute_s")},
                      "validation_passed": r.get("validation", {}).get("passed")}, ensure_ascii=False, indent=1))
    for w in r.get("warnings", []):
        print("•", w)
    for x in r.get("recommendations", []):
        print("→", x.get("text") or x.get("reason"))
    print(f"Результаты: {out.resolve()} ({time.time() - t:.1f} с)")
    return 0 if r["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
