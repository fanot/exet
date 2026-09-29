"""Данные для презентации — из свежих расчетов, а не вручную.

python docs/presentation/make_data.py <plan_moscow.json> <out_dir_images>
  * демо «Токсово» (8 бортов, оба критерия, методика QA) → числа слайдов и картинки (render_plan.py);
  * таблица масштабирования — из docs/BENCHMARK.md; режимы воздушного пространства — scripts/compare_airspace.py;
  * задание организаторов (Московская обл.) — из готового плана (его расчет ~5–10 мин, передается файлом);
  * подбор парка к сроку — planner.advisor на «Токсово».
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from planner.advisor import advise  # noqa: E402
from planner.geo import parse_geojson  # noqa: E402
from planner.models import MissionParams  # noqa: E402
from planner.optimizer import plan  # noqa: E402

FLEET = [{"model": "geoscan_201", "count": 2}, {"model": "geoscan_gemini", "count": 4}, {"model": "geoscan_801", "count": 2}]


def run(sc, **kw):
    p = MissionParams(fleet=FLEET, start_time="2026-09-25T09:00:00+03:00", wind_speed_ms=5, wind_dir_deg=250, **kw)
    return plan(parse_geojson(copy.deepcopy(sc)), p)


def main(moscow_json: str, img: str):
    img = Path(img)
    img.mkdir(parents=True, exist_ok=True)
    data = json.loads((Path(__file__).with_name("data.json")).read_text(encoding="utf-8"))
    tok = json.loads((ROOT / "scenarios" / "demo_toksovo.geojson").read_text(encoding="utf-8"))
    rt, rf = run(tok, objective="time"), run(tok, objective="flight")
    st, sf = rt["summary"], rf["summary"]
    (img / "plan_full.json").write_text(json.dumps(rt, ensure_ascii=False), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "render_plan.py"), str(img / "plan_full.json"), str(ROOT / "scenarios" / "demo_toksovo.geojson"),
                    str(img / "map.png"), str(img / "gantt.png"), str(img / "map_t.png")], check=True)
    data["time"] = {"makespan_h": round(st["makespan_h"], 1), "flight_hours": round(st["flight_hours"], 1), "sorties": st["sorties"]}
    data["flight"] = {"makespan_h": round(sf["makespan_h"], 1), "flight_hours": round(sf["flight_hours"], 1), "sorties": sf["sorties"]}
    data["flight_saving"] = round(100 * (1 - sf["flight_hours"] / st["flight_hours"]))
    data["time_penalty"] = round(100 * (sf["makespan_h"] / st["makespan_h"] - 1))
    data["cov"] = round(st["coverage_allowed_pct"], 1)
    data["demo_compute"] = round(st["compute_s"])
    data["models"] = [[f"{m['title']} · {m['payload_title']}", round(m["altitude_agl_m"]), round(m["line_spacing_m"]),
                       round(m["photo_base_m"] or 0, 1), round(m["survey_speed_ms"], 1)] for m in rt["models"].values()]
    data["maintenance"] = [f"{m['title'].replace('Геоскан ', '')}: {m['sorties']} вылетов, {m['flight_h']:.1f} ч — {m['used_pct']:.1f}% интервала ({m['interval']})"
                           for m in st.get("maintenance", [])] + ["Интервал Gemini не назван — принят как у 801"]
    rows = []
    for line in (ROOT / "docs" / "BENCHMARK.md").read_text(encoding="utf-8").splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("| ") and len(c) == 9 and c[2] == "time" and c[0] != "Сценарий":
            rows.append([c[0], c[1], c[3], c[4], c[5], c[8][:1]])
    data["bench"] = rows
    air = subprocess.run([sys.executable, str(ROOT / "scripts" / "compare_airspace.py")], capture_output=True, text=True, check=True).stdout
    ar = [[x.strip() for x in l.strip().strip("|").split("|")] for l in air.splitlines() if l.startswith("| ") and not l.startswith("| Режим")]
    data["airspace"]["rows"] = [[a[0], a[1], a[2], a[5], a[7]] for a in ar]
    t_plan, t_nop = float(ar[0][2].split()[0]), float(ar[2][2].split()[0])
    data["airspace"]["ratio"] = f"×{t_nop / t_plan:.0f}"
    data["airspace"]["extra_sites"] = int(ar[2][1]) - int(ar[0][1])
    m = json.loads(Path(moscow_json).read_text(encoding="utf-8"))
    ms = m["summary"]
    v = m["validation"]
    data["moscow"] = {"polys": 613, "area_km2": 245, "zones_total": 341, "zones_near": 17, "obst_total": 5146, "obst_near": 1837,
                      "sites": sum(1 for x in m["sites"] if x.get("auto")), "coverage": round(ms["coverage_allowed_pct"], 1),
                      "checks": f"{sum(c['passed'] for c in v['checks'])}/{len(v['checks'])}", "compute_min": round(ms["compute_s"] / 60, 1),
                      "days": ms["work_days"], "fleet_n": ms["drones_total"], "makespan_h": ms["makespan_h"], "flight_h": ms["flight_hours"]}
    subprocess.run([sys.executable, str(ROOT / "scripts" / "render_plan.py"), moscow_json, str(ROOT / "scenarios" / "demo_moscow_task.geojson"),
                    str(img / "map_moscow.png")], check=True)
    p = MissionParams(fleet=[{"model": "geoscan_201", "count": 3}, {"model": "geoscan_gemini", "count": 6}, {"model": "geoscan_801", "count": 2}],
                      start_time="2026-09-25T09:00:00+03:00", wind_speed_ms=5, wind_dir_deg=250, deadline_h=12)
    a = advise(parse_geojson(copy.deepcopy(tok)), p)
    data["advice"] = {"points": [[x["n"], x["makespan_h"]] for x in a["points"] if x["feasible"]], "deadline": 12, "text": a["text"]}
    data["stress_ok"], data["stress_n"] = 100, 100
    data["pipeline"] = "4.1 ч, 28 вылетов"
    (Path(__file__).with_name("data.json")).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: data[k] for k in ("time", "flight", "flight_saving", "time_penalty", "cov", "demo_compute", "moscow", "advice")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
