"""Reproduce audit metrics, including parameters and verification status.

Run from the repository root: python scripts/audit_benchmark.py [--terrain].
The default run uses a flat surface explicitly; it does not validate real terrain.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from planner.geo import parse_geojson
from planner.models import MissionParams
from planner.optimizer import plan


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--terrain", action="store_true")
    ap.add_argument("--effort", choices=("fast", "normal", "thorough"), default="fast")
    ap.add_argument("--optimizer", choices=("grid", "bayes"), default="grid")
    ap.add_argument("--scenario", default="scenarios/demo_toksovo.geojson")
    ap.add_argument("--out", default="data/audit_benchmark.json")
    args = ap.parse_args()
    source = json.loads((ROOT / args.scenario).read_text(encoding="utf-8"))
    runs = []
    for objective in ("time", "flight"):
        p = MissionParams(
            fleet=[{"model": "geoscan_201", "count": 2}, {"model": "geoscan_gemini", "count": 4},
                   {"model": "geoscan_801", "count": 2}],
            start_time="2026-09-25T09:00:00+03:00", wind_speed_ms=5, wind_dir_deg=250,
            search_effort=args.effort, optimizer=args.optimizer, objective=objective, use_terrain=args.terrain)
        t = time.perf_counter()
        r = plan(parse_geojson(source), p)
        entry = {"objective": objective, "elapsed_s": round(time.perf_counter() - t, 3),
                 "status": r["status"], "params": p.model_dump(mode="json"),
                 "summary": r.get("summary"), "validation": r.get("validation"),
                 "error": r.get("error"), "warnings": r.get("warnings")}
        runs.append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    output = ROOT / args.out
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"python": platform.python_version(), "platform": platform.platform(),
                                  "scenario": args.scenario, "runs": runs}, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(any(r["status"] != "ok" or not r.get("validation", {}).get("passed") for r in runs))


if __name__ == "__main__":
    raise SystemExit(main())
