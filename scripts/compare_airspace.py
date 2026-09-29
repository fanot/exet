"""Сравнение режимов воздушного пространства на демо «Токсово»: с планом полета (700 м) и без (ФП ИВП, 150 м)."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from planner.geo import parse_geojson  # noqa: E402
from planner.models import MissionParams  # noqa: E402
from planner.optimizer import plan  # noqa: E402

SC = json.loads((ROOT / "scenarios" / "demo_toksovo.geojson").read_text(encoding="utf-8"))
FLEET = [{"model": "geoscan_201", "count": 2}, {"model": "geoscan_gemini", "count": 4}, {"model": "geoscan_801", "count": 2}]


def run(**kw):
    p = MissionParams(fleet=FLEET, start_time="2026-09-25T09:00:00+03:00", wind_speed_ms=5, wind_dir_deg=250,
                      search_effort="fast", **kw)
    return plan(parse_geojson(copy.deepcopy(SC)), p)


def row(name, r, n_sites):
    s, v = r["summary"], r["validation"]
    ok = sum(c["passed"] for c in v["checks"])
    print(f"| {name} | {n_sites} | {s['makespan_h']:.1f} ч | {s['flight_hours']:.1f} ч | {s['sorties']} | "
          f"{s['coverage_allowed_pct']:.1f}% | {r['status']} | {ok}/{len(v['checks'])} |")


if __name__ == "__main__":
    base_sites = sum(f["properties"].get("kind") == "site" for f in SC["features"])
    print("| Режим | Площадок | Время работ | Налет | Вылетов | Покрытие | Статус | Crash-тест |\n|---|---|---|---|---|---|---|---|")
    row("С планом полета (700 м)", run(), base_sites)
    r = run(airspace_mode="no_flight_plan")
    row("Без плана полета, 2 км", r, base_sites)
    rec = next((x for x in r["recommendations"] if x.get("kind") == "vlos"), None)
    if rec:
        extra = [{"lon": s["lon"], "lat": s["lat"], "name": f"VLOS {i + 1}"} for i, s in enumerate(rec["sites"])]
        row(f"Без плана полета + {len(extra)} площадок", run(airspace_mode="no_flight_plan", extra_sites=extra), base_sites + len(extra))
