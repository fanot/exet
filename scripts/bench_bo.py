"""Сравнение методов поиска: сетка vs байесовская оптимизация (TPE) — по ФАКТИЧЕСКОМУ результату
после полного построения (траектории, разведение, валидация), а не по быстрой оценке.

python scripts/bench_bo.py [число_случайных_сценариев]
"""
import copy
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from planner.geo import parse_geojson  # noqa: E402
from planner.models import MissionParams  # noqa: E402
from planner.optimizer import plan  # noqa: E402
from scripts.stress import random_scenario  # noqa: E402

FLEET = [{"model": "geoscan_201", "count": 2}, {"model": "geoscan_gemini", "count": 4}, {"model": "geoscan_801", "count": 2}]


def run(sc, obj, opt, start, wind, wdir):
    p = MissionParams(fleet=FLEET, start_time=start, wind_speed_ms=wind, wind_dir_deg=wdir, objective=obj, optimizer=opt, use_terrain=False)
    t = time.time()
    r = plan(parse_geojson(copy.deepcopy(sc)), p)
    s = r.get("summary", {})
    if "makespan_h" not in s:
        return None
    val = s["makespan_h"] if obj == "time" else s["flight_hours"]
    return dict(v=val, t=time.time() - t, ok=r["validation"]["passed"], n=len(r["search_log"]))


def main():
    n_rand = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    cases = []
    for name in ("demo_toksovo", "demo_pipeline"):
        cases.append((name, json.loads((ROOT / "scenarios" / f"{name}.geojson").read_text(encoding="utf-8")), "2026-09-25T09:00:00+03:00", 5, 250))
    rng = random.Random(123)
    for i in range(n_rand):
        cases.append((f"random_{i}", random_scenario(rng), "2026-06-10T08:00:00+03:00", rng.uniform(0, 6), rng.uniform(0, 359)))
    rows, wins, ties, losses, gains = [], 0, 0, 0, []
    for name, sc, start, wind, wdir in cases:
        for obj in ("time", "flight"):
            g = run(sc, obj, "grid", start, wind, wdir)
            b = run(sc, obj, "bayes", start, wind, wdir)
            if not g or not b:
                continue
            d = (b["v"] - g["v"]) / g["v"] * 100 if g["v"] else 0.0
            gains.append(d)
            wins += d < -0.5
            losses += d > 0.5
            ties += abs(d) <= 0.5
            rows.append(f"| {name} | {'время' if obj == 'time' else 'налет'} | {g['v']:.2f} | {b['v']:.2f} | {d:+.1f}% | {g['t']:.0f} / {b['t']:.0f} | {'✔' if g['ok'] and b['ok'] else '✖'} |")
            print(rows[-1], flush=True)
    import statistics
    summary = (f"\nИтого {len(gains)} задач: байесовская лучше в {wins}, равны в {ties}, хуже в {losses}; "
               f"среднее изменение критерия {statistics.mean(gains):+.1f}%, лучший выигрыш {min(gains):+.1f}%.")
    print(summary)
    md = ("# Сетка vs байесовская оптимизация\n\nПарк 2×201 + 4×Gemini + 2×801, режим «нормально», без ЦМР. Значение — фактический критерий "
          "после полного построения (время работ или налет, ч); меньше — лучше.\n\n"
          "| Сценарий | Критерий | Сетка | Байес (TPE) | Δ | Расчет, с (сетка / байес) | Crash-тест |\n|---|---|---|---|---|---|---|\n"
          + "\n".join(rows) + "\n" + summary + "\n")
    (ROOT / "docs" / "BENCHMARK_BO.md").write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
