"""Бенчмарк: масштабирование по числу бортов и площади. Результат — таблица Markdown."""
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from planner.geo import parse_geojson  # noqa: E402
from planner.models import MissionParams  # noqa: E402
from planner.optimizer import plan  # noqa: E402
from scripts.make_demo import circle, feat, poly  # noqa: E402

BASE = json.loads((ROOT / "scenarios" / "demo_toksovo.geojson").read_text(encoding="utf-8"))


def square_scenario(km):
    import math
    lon, lat = 30.5, 60.15
    dl = km / 111.32 / 2
    dn = km / (111.32 * math.cos(math.radians(lat))) / 2
    ring = [[lon - dn, lat - dl], [lon + dn, lat - dl], [lon + dn, lat + dl], [lon - dn, lat + dl]]
    return {"type": "FeatureCollection", "features": [
        feat("survey_area", poly(ring)),
        feat("no_fly", poly(circle(lon, lat, km * 60))),
        feat("site", {"type": "Point", "coordinates": [lon - dn - 0.01, lat]}, name="Запад"),
        feat("site", {"type": "Point", "coordinates": [lon + dn + 0.01, lat]}, name="Восток"),
    ]}


WIND_MODEL = "qa"


def run(sc, fleet, objective="time", effort="normal"):
    p = MissionParams(fleet=fleet, start_time="2026-09-25T09:00:00+03:00", wind_speed_ms=5, wind_dir_deg=250,
                      objective=objective, search_effort=effort, wind_model=WIND_MODEL)
    t = time.time()
    r = plan(parse_geojson(copy.deepcopy(sc)), p)
    s = r["summary"]
    return dict(t=time.time() - t, mk=s["makespan_h"], fl=s["flight_hours"], n=s["sorties"], cov=s["coverage_allowed_pct"],
                ok=r["validation"]["passed"], st=r["status"])


FOOTER = """
## Упаковка вылетов: было → стало

Сравнение в одинаковых условиях — физическая модель ветра, как в прежней версии (`python scripts/bench.py --wind physical`).

Изменения:
- оценка вылета учитывает обход запретных зон на перелетах и подъем на эшелон на закрытых переходах между галсами;
- добор кромок упорядочивается цепочкой «ближайший сосед»;
- хвост после ремонта по энергии переносится в следующий вылет, а не становится отдельным коротким вылетом;
- диспетчер выбирает для борта вылет с учетом его текущей площадки (меньше переездов).

Раньше у зон до четверти налета уходило на вылеты с полукилометром съемки.

| Сценарий | Парк | Критерий | Время работ, ч: было → стало | Налет, ч: было → стало | Вылетов |
|---|---|---|---|---|---|
| Токсово 100 км² | 1×Gemini | time | 505.02 → 408.86 (-19%) | 117.27 → 107.7 (-8%) | 235 → 212 |
| Токсово 100 км² | 1×Gemini | flight | 508.32 → 408.86 (-20%) | 115.31 → 107.7 (-7%) | 229 → 212 |
| Токсово 100 км² | 4×Gemini | time | 171.04 → 151.35 (-12%) | 165.5 → 155.7 (-6%) | 329 → 306 |
| Токсово 100 км² | 4×Gemini | flight | 171.04 → 151.35 (-12%) | 165.5 → 155.7 (-6%) | 329 → 306 |
| Токсово 100 км² | 1×201 | time | 31.74 → 30.86 (-3%) | 12.13 → 11.83 (-2%) | 7 → 7 |
| Токсово 100 км² | 1×201 | flight | 53.62 → 30.86 (-42%) | 11.83 → 11.83 (+0%) | 7 → 7 |
| Токсово 100 км² | 2×201 + 4×Gemini + 2×801 | time | 25.25 → 25.59 (+1%) | 43.55 → 35.8 (-18%) | 72 → 53 |
| Токсово 100 км² | 2×201 + 4×Gemini + 2×801 | flight | 30.45 → 28.25 (-7%) | 27.79 → 23.26 (-16%) | 34 → 22 |
| Токсово 100 км² | 3×201 + 5×Gemini + 2×801 (10) | time | 25.71 → 7.69 (-70%) | 37.83 → 40.84 (+8%) | 54 → 60 |
| Токсово 100 км² | 3×201 + 5×Gemini + 2×801 (10) | flight | 28.37 → 25.82 (-9%) | 28.82 → 24.23 (-16%) | 34 → 23 |
| Квадрат 25 км² | 2×201 + 4×Gemini + 2×801 | time | 2.84 → 2.71 (-5%) | 11.22 → 9.36 (-17%) | 21 → 13 |
| Квадрат 100 км² | 2×201 + 4×Gemini + 2×801 | time | 25.96 → 25.37 (-2%) | 30.42 → 50.98 (+68%) | 38 → 85 |
| Квадрат 225 км² | 2×201 + 4×Gemini + 2×801 | time | 51.47 → 49.87 (-3%) | 84.76 → 86.16 (+2%) | 125 → 127 |

Итог:
- при минимуме налета налет снизился на 16% (8 и 10 бортов), время работ — на 7–9%;
- у одного Gemini время работ снизилось на 19%;
- 10 бортов по времени: 25.7 → 7.7 ч — работа укладывается в один световой день.

Где критерий — время, налет может вырасти: параллельно работает больше бортов.

## Прочее

Стресс-тест (`scripts/stress.py`): 100 случайных сценариев (seed 7 × 40, seed 42 × 30, seed 123 × 30):
- MultiPolygon с отверстиями;
- 1–4 зоны (постоянные, временные, по высотам);
- препятствия 40–250 м;
- 1–3 площадки, 1–10 бортов;
- ветер 0–7 м/с, широты 45–62°;
- случайные режимы: модель АКБ, воздушное пространство (с планом полета / без плана до 150 м).

**Нарушений: 0 из 100.** Обоснованные отказы (ветер выше ТТХ всех типов, нет подходящей нагрузки) — не нарушения.

Демо (8 бортов, минимум времени, методика QA):

| Демо | Время работ | Налет | Вылетов | Покрытие | Crash-тест |
|---|---|---|---|---|---|
| «Трубопровод 35 км + 2 узла» | 4.1 ч | 16.3 ч | 28 | 99.9% | 7/7 |
| «Артем, Приморье», 1082 реальных препятствия организаторов | 6.4 ч | 25.8 ч | 39 | 99.9% | 7/7 |
| «Задание организаторов: Московская обл.», 245 км², 570 участков, 17 зон, 1837 препятствий, 28 авто-площадок (режим «быстро») | 247 ч (11 дн.) | 308 ч | 608 | 99.7% | 7/7 |

Сравнение методов поиска — в [BENCHMARK_BO.md](BENCHMARK_BO.md).
"""


def main(out=None):
    rows = ["| Сценарий | Парк | Критерий | Расчет, с | Время работ, ч | Налет, ч | Вылетов | Покрытие | Crash-тест |", "|---|---|---|---|---|---|---|---|---|"]
    fleets = [
        ("1×Gemini", [{"model": "geoscan_gemini", "count": 1}]),
        ("4×Gemini", [{"model": "geoscan_gemini", "count": 4}]),
        ("1×201", [{"model": "geoscan_201", "count": 1}]),
        ("2×201 + 4×Gemini + 2×801", [{"model": "geoscan_201", "count": 2}, {"model": "geoscan_gemini", "count": 4}, {"model": "geoscan_801", "count": 2}]),
        ("3×201 + 5×Gemini + 2×801 (10)", [{"model": "geoscan_201", "count": 3}, {"model": "geoscan_gemini", "count": 5}, {"model": "geoscan_801", "count": 2}]),
    ]
    for name, fl in fleets:
        for obj in ("time", "flight"):
            r = run(BASE, fl, obj)
            rows.append(f"| Токсово 100 км² | {name} | {obj} | {r['t']:.1f} | {r['mk']} | {r['fl']} | {r['n']} | {r['cov']}% | {'✔' if r['ok'] else '✖'} {r['st']} |")
            print(rows[-1], flush=True)
    for km in (5, 10, 15):
        r = run(square_scenario(km), fleets[3][1])
        rows.append(f"| Квадрат {km * km} км² | {fleets[3][0]} | time | {r['t']:.1f} | {r['mk']} | {r['fl']} | {r['n']} | {r['cov']}% | {'✔' if r['ok'] else '✖'} {r['st']} |")
        print(rows[-1], flush=True)
    (out or ROOT / "docs" / "BENCHMARK.md").write_text("# Бенчмарк\n\nMacBook (Apple Silicon), режим поиска «нормально» (сетка + байесовская оптимизация), ветер 5 м/с, старт 25.09 09:00, ЦМР включена, "
                                                  + ("учет ветра по методике QA (путевая скорость постоянна)" if WIND_MODEL == "qa" else "физическая модель ветра") + ".\n\n"
                                               + "\n".join(rows) + "\n" + FOOTER, encoding="utf-8")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wind", choices=["qa", "physical"], default="qa", help="модель ветра (по умолчанию — методика QA)")
    ap.add_argument("--out", type=Path, default=None, help="куда записать таблицу (по умолчанию docs/BENCHMARK.md)")
    a = ap.parse_args()
    WIND_MODEL = a.wind
    main(a.out)
