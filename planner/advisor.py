"""Подбор парка к сроку (ответ организаторов: «пользователь задает ограничения — число бортов или срок — и получает
подсказки: выполнимо/невыполнимо, сколько бортов потребуется в срок, сколько времени займет работа заданным парком»).

Маржинальный анализ: из доступного парка (params.fleet — максимум по типам) борта добавляются по одному; на каждом
шаге выбирается тип, сильнее всего сокращающий время работ. Каждая точка — быстрая оценка планировщика
(галсы → упаковка вылетов с учетом обхода зон → расписание), которая совпадает с точным построением с точностью
в несколько процентов. Итог — кривая «время работ от числа бортов» и минимальный парк, укладывающийся в срок.
Рекомендованный парк затем считается полностью (траектории, разведение, crash-тест) обычным расчетом.
"""
from __future__ import annotations

import copy
import math
from typing import Callable, Dict, List, Optional

from .models import MissionParams


EXTRA_MAX_PER_TYPE = 12


def _estimate(scenario, params: MissionParams, fleet: Dict[str, int]) -> Optional[dict]:
    from .optimizer import Planner, PlanningError

    d = params.model_dump(mode="json")
    d.update(fleet=[{"model": k, "count": v, "batteries": next((f.batteries for f in params.fleet if f.model == k), None)}
                    for k, v in fleet.items() if v > 0], objective="time", search_effort="fast", optimizer="grid")
    p = MissionParams.model_validate(d)
    pl = Planner(copy.deepcopy(scenario), p)
    try:
        pl.prepare()
        pl.survey_geoms()
        if not pl.models:
            return None
        pl.build_contexts()
        best = pl.search()
    except (PlanningError, ValueError):
        return None
    return {"makespan_h": round(best["makespan"] / 3600, 2), "flight_h": round(best["flight"] / 3600, 2),
            "sorties": best["n_sorties"], "feasible": best["bad_m"] < 1.0}


def advise(scenario, params: MissionParams, progress: Optional[Callable] = None) -> dict:
    prog = progress or (lambda f, m: None)
    pool = {f.model: f.count for f in params.fleet if f.count > 0}
    total = sum(pool.values())
    if not total:
        raise ValueError("Задайте доступный парк: сколько бортов каждого типа можно привлечь")
    deadline = params.deadline_h
    # для каждого размера парка n — лучший из кандидатов: жадный шаг от лучшего состава n−1 (каждый тип) и
    # пропорциональный доступному парку состав. Время работ — ступенчатая функция (световой день), поэтому одного
    # жадного шага мало: он не видит, что «еще 3 коптера» укладывают работу в один день
    cache: Dict[tuple, Optional[dict]] = {}
    evals = [0]
    budget = total * (len(pool) + 1)

    def est(fl):
        key = tuple(sorted(fl.items()))
        if key not in cache:
            evals[0] += 1
            prog(min(0.95, evals[0] / max(budget, 1)), f"Оценка парка из {sum(fl.values())} бортов ({_fleet_str(fl)})")
            cache[key] = _estimate(scenario, params, fl)
        return cache[key]

    def proportional(n):
        raw = {k: pool[k] * n / total for k in pool}
        fl = {k: int(math.floor(v)) for k, v in raw.items()}
        for k in sorted(pool, key=lambda k: raw[k] - fl[k], reverse=True):
            if sum(fl.values()) >= n:
                break
            if fl[k] < pool[k]:
                fl[k] += 1
        return fl

    points: List[dict] = []
    prev = {k: 0 for k in pool}
    cap = dict(pool)
    n_max = total
    n = 0
    while n < n_max:
        n += 1
        if n > total:
            # срок недостижим доступным парком — считаем, сколько бортов тех же типов потребуется (до 12 каждого)
            cap = {k: max(pool[k], EXTRA_MAX_PER_TYPE) for k in pool}
        cands = []
        for k in pool:
            if prev[k] < cap[k]:
                c = dict(prev)
                c[k] += 1
                cands.append(c)
        if n <= total:
            cands.append(proportional(n))
        best = None
        for c in cands:
            if sum(c.values()) != n:
                continue
            e = est(c)
            if e is None:
                continue
            key = (not e["feasible"], e["makespan_h"], e["flight_h"])
            if best is None or key < best[0]:
                best = (key, c, e)
        if best is None:
            k = next((k for k in pool if prev[k] < cap[k]), None)
            if k is None:
                break
            prev = dict(prev)
            prev[k] += 1
            continue
        _, prev, e = best
        pt = {"fleet": dict(prev), "n": n, **e}
        # огибающая: при n доступных бортах лишние можно не поднимать — время не хуже, чем у меньшего парка
        if points and points[-1]["feasible"] and (not e["feasible"] or points[-1]["makespan_h"] < e["makespan_h"]):
            pt = {**points[-1], "n": n}
        pt["used"] = sum(pt["fleet"].values())
        pt["beyond_pool"] = n > total
        points.append(pt)
        if n == total and deadline and not any(x["feasible"] and x["makespan_h"] <= deadline for x in points):
            n_max = min(30, total + 12, sum(max(v, EXTRA_MAX_PER_TYPE) for v in pool.values()))
        if n > total and pt["feasible"] and pt["makespan_h"] <= deadline:
            break
    rec = None
    if deadline:
        ok = [pt for pt in points if pt["feasible"] and pt["makespan_h"] <= deadline and not pt.get("beyond_pool")]
        rec = ok[0] if ok else None  # наименьший парк, укладывающийся в срок
    in_pool = [pt for pt in points if not pt.get("beyond_pool")]
    fastest = min((pt for pt in in_pool if pt["feasible"]), key=lambda pt: pt["makespan_h"], default=None)
    need = next((pt for pt in points if pt.get("beyond_pool") and pt["feasible"] and pt["makespan_h"] <= (deadline or 0)), None)
    # «колено»: наименьший парк, чье время работ в пределах 5% от времени всего доступного парка
    knee = None
    if fastest:
        knee = next((pt for pt in in_pool if pt["feasible"] and pt["makespan_h"] <= fastest["makespan_h"] * 1.05), None)
        fastest = next(pt for pt in in_pool if pt["fleet"] == fastest["fleet"])
    if rec is None and need is not None:
        rec = need
    text = _explain(points, deadline, rec, fastest, knee, total)
    prog(1.0, "Готово")
    return {"points": points, "deadline_h": deadline, "recommended": rec, "fastest": fastest, "knee": knee,
            "pool": pool, "text": text}


def _fleet_str(fl: Dict[str, int]) -> str:
    names = {"geoscan_201": "201", "geoscan_gemini": "Gemini", "geoscan_801": "801"}
    return " + ".join(f"{v}×{names.get(k, k)}" for k, v in fl.items() if v)


def _h(v) -> str:
    return f"{v:.1f}".replace(".", ",")


def _explain(points, deadline, rec, fastest, knee, total) -> str:
    if not points:
        return "Ни один вариант парка не дает плана — проверьте тип съемки, ветер и площадки"
    parts = []
    if deadline:
        if rec and not rec.get("beyond_pool"):
            parts.append(f"Срок {deadline:g} ч выдерживается при парке из {rec['used']} бортов ({_fleet_str(rec['fleet'])}): "
                         f"≈ {_h(rec['makespan_h'])} ч работ, налет {_h(rec['flight_h'])} ч")
        elif rec:
            best_text = f"лучшее ≈ {_h(fastest['makespan_h'])} ч" if fastest else "нет выполнимого варианта"
            parts.append(f"Доступным парком ({total} бортов) срок {deadline:g} ч невыполним — {best_text}. "
                         f"Потребуется {rec['used']} бортов ({_fleet_str(rec['fleet'])}): ≈ {_h(rec['makespan_h'])} ч работ")
        elif fastest:
            parts.append(f"Срок {deadline:g} ч невыполним даже при {points[-1]['n']} бортах: минимум ≈ {_h(min(p['makespan_h'] for p in points if p['feasible']))} ч. "
                         "Ограничение не в числе бортов — добавьте площадки ближе к участкам или сдвиньте срок")
    elif fastest:
        parts.append(f"Лучшее время доступным парком ≈ {_h(fastest['makespan_h'])} ч ({_fleet_str(fastest['fleet'])})")
    if knee and fastest and knee["used"] < fastest["used"] and (not rec or knee["used"] != rec["used"]):
        parts.append(f"{knee['used']} бортов ({_fleet_str(knee['fleet'])}) дают почти то же время (≈ {_h(knee['makespan_h'])} ч) — "
                     "остальные почти не ускоряют работу")
    return ". ".join(parts)
