"""Стресс-тест «как у экспертов»: случайные сценарии → план → независимая проверка.

python scripts/stress.py [число_сценариев] [seed]
"""
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from planner.geo import parse_geojson  # noqa: E402
from planner.models import MissionParams  # noqa: E402
from planner.optimizer import PlanningError, plan  # noqa: E402
from scripts.make_demo import circle, feat, poly  # noqa: E402


def blob(rng, lon, lat, r_m, n=9, jitter=0.35):
    k = 111320.0
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = r_m * (1 + rng.uniform(-jitter, jitter))
        pts.append([lon + rr * math.cos(a) / (k * math.cos(math.radians(lat))), lat + rr * math.sin(a) / k])
    return pts


def random_scenario(rng):
    lat = rng.uniform(45, 62)
    lon = rng.uniform(30, 60)
    r = rng.uniform(2500, 6000)
    area = blob(rng, lon, lat, r)
    holes = [blob(rng, lon + rng.uniform(-0.3, 0.3) * r / 55000, lat + rng.uniform(-0.3, 0.3) * r / 111000, r * 0.12, 7, 0.2)] if rng.random() < 0.5 else []
    feats = [feat("survey_area", poly(area, holes))]
    k = 111320.0
    def off(d, ang):
        return [lon + d * math.cos(ang) / (k * math.cos(math.radians(lat))), lat + d * math.sin(ang) / k]
    for _ in range(rng.randint(1, 4)):
        c = off(rng.uniform(0, r), rng.uniform(0, 2 * math.pi))
        kind = rng.random()
        props = {"name": "zone"}
        if kind < 0.25:
            props.update(active_from="2026-06-10T11:00:00+03:00", active_to="2026-06-10T14:00:00+03:00")
        elif kind < 0.4:
            props.update(alt_min_m=rng.choice([350, 450, 550]), alt_max_m=3000)
        feats.append(feat("no_fly", poly(circle(c[0], c[1], rng.uniform(300, 1200), 24)), **props))
    for _ in range(rng.randint(0, 4)):
        c = off(rng.uniform(0, r), rng.uniform(0, 2 * math.pi))
        feats.append(feat("obstacle", {"type": "Point", "coordinates": c}, name="obs", height_m=rng.choice([40, 90, 150, 250]), radius_m=15))
    for i in range(rng.randint(1, 3)):
        feats.append(feat("site", {"type": "Point", "coordinates": off(r * rng.uniform(1.05, 1.3), rng.uniform(0, 2 * math.pi))}, name=f"S{i}"))
    feats.append(feat("reserve_site", {"type": "Point", "coordinates": off(r * 1.2, rng.uniform(0, 2 * math.pi))}, name="R"))
    return {"type": "FeatureCollection", "features": feats}


def random_params(rng):
    fleet = [{"model": "geoscan_201", "count": rng.randint(0, 3)}, {"model": "geoscan_gemini", "count": rng.randint(0, 5)},
             {"model": "geoscan_801", "count": rng.randint(0, 2)}]
    if sum(f["count"] for f in fleet) == 0:
        fleet[1]["count"] = 2
    return MissionParams(fleet=fleet, survey_type=rng.choice(["rgb", "rgb", "multispectral"]), objective=rng.choice(["time", "flight"]),
                         start_time="2026-06-10T08:00:00+03:00", wind_speed_ms=rng.uniform(0, 7), wind_dir_deg=rng.uniform(0, 359),
                         search_effort="fast", use_terrain=rng.random() < 0.3,
                         energy_model=rng.choice(["linear", "calibrated"]),
                         airspace_mode=rng.choice(["flight_plan", "flight_plan", "no_flight_plan"]))


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    rng = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    fails = 0
    valid, refused = 0, 0
    for i in range(n):
        sc, p = random_scenario(rng), random_params(rng)
        if "--flat" in sys.argv:
            p.use_terrain = False
        t = time.time()
        try:
            r = plan(parse_geojson(sc), p)
        except PlanningError as e:  # обоснованный отказ (ветер выше ТТХ всех типов и т.п.) — не ошибка
            print(f"#{i} refused    {e}")
            refused += 1
            continue
        except Exception as e:  # noqa: BLE001
            print(f"#{i} EXCEPTION {type(e).__name__}: {e}")
            (ROOT / "data").mkdir(exist_ok=True)
            (ROOT / "data" / f"stress_fail_{i}.json").write_text(json.dumps({"scenario": sc, "params": p.model_dump(mode="json")}, ensure_ascii=False))
            fails += 1
            continue
        if not r.get("sorties") and r["status"] == "infeasible" and r.get("recommendations"):
            print(f"#{i} infeasible, 0 вылетов — рекомендации: {len(r['recommendations'])}")  # обоснованный отказ
            refused += 1
            continue
        v = r.get("validation", {"passed": r["status"] == "infeasible", "checks": []})
        bad = [c["name"] + ": " + "; ".join(c["details"][:2]) for c in v.get("checks", []) if not c["passed"]]
        print(f"#{i} {r['status']:10s} {time.time() - t:5.1f}s sorties={r.get('summary', {}).get('sorties')} {'OK' if not bad else 'FAIL ' + ' | '.join(bad)}", flush=True)
        if bad:
            fails += 1
            (ROOT / "data").mkdir(exist_ok=True)
            (ROOT / "data" / f"stress_fail_{i}.json").write_text(json.dumps({"scenario": sc, "params": p.model_dump(mode="json")}, ensure_ascii=False))
        elif r["status"] == "ok":
            valid += 1
        else:
            refused += 1
    print(f"valid plans: {valid}; infeasible/refused: {refused}; failures: {fails}/{n}")
    sys.exit(1 if fails else 0)
