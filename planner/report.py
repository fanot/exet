"""Mission-wide analytics derived only from the saved calculation, never telemetry."""
from html import escape

OBJECTIVES = {"time": "Минимум времени", "flight": "Минимум налёта", "balanced": "Баланс"}
NOTICE = "Отчёт по расчётному плану. Фактический полёт и телеметрия не анализировались."


def number(value, unit="", digits=1):
    if value is None:
        return "—"
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",") + (f" {unit}" if unit else "")


def mission_report(plan):
    p, s, v = plan.get("params") or {}, plan.get("summary") or {}, plan.get("validation") or {}
    checks = v.get("checks") or []
    sorties = plan.get("sorties") or []
    ready = plan.get("status") == "ok" and v.get("passed") and bool(sorties)
    title = "План готов к проверке оператором" if ready else "План требует доработки"
    issues = []
    if plan.get("error"):
        issues.append(plan["error"])
    issues.extend(c["name"] + (": " + "; ".join(c.get("details") or []) if c.get("details") else "")
                  for c in checks if not c.get("passed"))
    for reasons in (plan.get("excluded") or {}).values():
        issues.extend(reasons if isinstance(reasons, list) else [str(reasons)])
    if p.get("deadline_h") and s.get("makespan_h", 0) > p["deadline_h"]:
        issues.append(f"Срок {number(p['deadline_h'], 'ч')} превышен на {number(s['makespan_h'] - p['deadline_h'], 'ч')}.")
    if s.get("coverage_allowed_pct") is not None and s["coverage_allowed_pct"] < 99:
        issues.append(f"Покрыто {number(s['coverage_allowed_pct'], '%')} разрешённой площади.")
    metrics = [
        ["Время работ", number(s.get("makespan_h"), "ч")],
        ["Суммарный налёт", number(s.get("flight_hours"), "ч")],
        ["Вылеты / борта", f"{s.get('sorties', '—')} / {s.get('drones_used', '—')}"],
        ["Покрытие разрешённой площади", number(s.get("coverage_allowed_pct"), "%")],
    ]
    remaining = [100 - x["battery_used_pct"] for x in sorties if x.get("battery_used_pct") is not None]
    layers = s.get("transit_layers_agl_m") or []
    conditions = [
        ["Ветер у земли (10 м)", number(p.get("wind_speed_ms"), "м/с")],
        ["Направление, откуда дует", number(p.get("wind_dir_deg"), "°", 0)],
        ["Учёт ветра", "Физическая модель" if p.get("wind_model") == "physical" else "Методика QA: пределы и курс галсов"],
        ["Эшелоны перелёта, AGL", ", ".join(number(x, "м", 0) for x in layers) or "—"],
        ["Потолок, AGL", number(s.get("ceiling_agl_m"), "м", 0)],
        ["Минимальный расчётный остаток АКБ", number(min(remaining) if remaining else None, "%")],
        ["Заданный резерв АКБ", number(p.get("reserve_pct"), "%")],
        ["Рельеф", s.get("terrain") or "Нет данных в результате"],
        ["Начало / окончание", f"{s.get('mission_start_iso', '—')} / {s.get('mission_end_iso', '—')}"],
    ]
    resolved = s.get("conflicts_resolved_by_delay")
    decisions = ([f"Разведение задержкой старта: {resolved} срабатываний при построении расписания."]
                 if resolved is not None else [])
    if p.get("wind_model", "qa") == "qa":
        decisions.append("Ветер ограничивает допустимые типы БВС и влияет на курс галсов; в методике QA расход АКБ от ветра не меняется.")
    if s.get("balance_reference"):
        b = s["balance_reference"]
        if b.get("method") == "augmented_chebyshev":
            decisions.append(
                "Баланс: критерий Чебышёва с равными весами уменьшает худшее нормированное отставание по времени и налёту. "
                f"Диапазоны оценок поиска: {number(b['time_s'] / 3600, 'ч')}–{number(b['time_max_s'] / 3600, 'ч')} работ "
                f"и {number(b['flight_s'] / 3600, 'ч')}–{number(b['flight_max_s'] / 3600, 'ч')} налёта. "
                "Штрафы за срок и износ учитываются дополнительно. Это оценки кандидатов, а не отдельные проверенные планы.")
        else:
            decisions.append(f"Баланс: равные веса времени и налёта. Опорные оценки поиска: {number(b['time_s'] / 3600, 'ч')} работ и {number(b['flight_s'] / 3600, 'ч')} налёта. Это оценки кандидатов, а не отдельные проверенные планы.")
    return {"plan_id": plan["id"], "scenario": plan.get("scenario_name", "Миссия"), "title": title,
            "ready": bool(ready), "notice": NOTICE, "objective": OBJECTIVES.get(p.get("objective"), "—"),
            "metrics": metrics, "conditions": conditions, "issues": list(dict.fromkeys(issues)),
            "warnings": list(dict.fromkeys(plan.get("warnings") or [])), "decisions": decisions,
            "recommendations": [r.get("text") or r.get("reason") for r in plan.get("recommendations") or [] if r.get("text") or r.get("reason")],
            "checks": checks, "checks_passed": sum(bool(c.get("passed")) for c in checks),
            "drones": [{"id": d["id"], "flight_h": d.get("flight_h"), "sorties": len(d.get("sorties") or []),
                        "layer_m": d.get("transit_alt_agl_m")} for d in plan.get("drones") or [] if d.get("sorties")]}


def report_analytics(plan):
    """Aggregate saved sorties; missing phase/energy data stays explicitly unknown."""
    sorties = plan.get("sorties") or []
    groups = {}
    phase_seconds = {"survey": 0, "transit": 0, "maneuvers": 0, "unknown": 0}
    for sortie in sorties:
        drone_id = sortie["drone_id"]
        group = groups.setdefault(drone_id, {"id": drone_id, "sorties": 0, "flight_h": 0,
                                             "remaining_pct": None, "energy_samples": 0})
        group["sorties"] += 1
        start, end = sortie.get("t_takeoff"), sortie.get("t_landing")
        duration = max(0, end - start) if start is not None and end is not None else max(0, (sortie.get("duration_min") or 0) * 60)
        group["flight_h"] += duration / 3600
        used = sortie.get("battery_used_pct")
        if used is not None:
            remaining = 100 - used
            group["energy_samples"] += 1
            group["remaining_pct"] = remaining if group["remaining_pct"] is None else min(group["remaining_pct"], remaining)
        accounted = 0
        for phase in sortie.get("phases") or []:
            kind = phase.get("phase")
            seconds = max(0, phase["t_end"] - phase["t_start"])
            accounted += seconds
            if kind == "ground":
                continue
            key = "survey" if kind == "survey" else "transit" if kind in {"transit_out", "transit_back"} else "maneuvers" if kind in {"takeoff", "landing", "climb", "descent"} else "unknown"
            phase_seconds[key] += seconds
        phase_seconds["unknown"] += max(0, duration - accounted)
    total = sum(phase_seconds.values())
    labels = {"survey": "Съёмка", "transit": "Перелёты", "maneuvers": "Взлёт, посадка и смена высоты", "unknown": "Этап не указан"}
    return {"drones": sorted(groups.values(), key=lambda d: (-d["flight_h"], d["id"])),
            "phases": [{"key": key, "label": labels[key], "hours": seconds / 3600,
                        "pct": 100 * seconds / total if total else 0}
                       for key, seconds in phase_seconds.items() if seconds > 0],
            "reserve_pct": (plan.get("params") or {}).get("reserve_pct")}


def report_html(plan):
    r, a = mission_report(plan), report_analytics(plan)
    summary = plan.get("summary") or {}
    def e(value):
        return escape(str(value))
    def section(title, items, empty=None):
        if not items and empty is None:
            return ""
        return f"<section><h2>{e(title)}</h2>" + ("<ul>" + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>" if items else f"<p class=muted>{e(empty)}</p>") + "</section>"
    def bar(value, maximum=100):
        width = max(0, min(100, value / maximum * 100)) if maximum else 0
        return f'<svg class="bar" viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden="true"><rect width="100" height="12" rx="2" fill="#e8edf3"/><rect width="{width:.3f}" height="12" rx="2" fill="#2563eb"/></svg>'
    coverage = ""
    for label, key, area_key in [("Разрешённая площадь", "coverage_allowed_pct", "area_allowed_km2"),
                                 ("Вся запрошенная площадь", "coverage_pct", "area_requested_km2")]:
        value = summary.get(key)
        coverage += f'<div class="coverage-row"><div><b>{label}</b><span class="muted">{number(summary.get(area_key), "км²", 2)}</span></div><div>{bar(value) if value is not None else ""}</div><strong>{number(value, "%")}</strong></div>'
    allowed, covered = summary.get("area_allowed_km2"), summary.get("coverage_allowed_pct")
    coverage_note = "Проценты рассчитаны от разных площадей; ограничения могут исключать часть исходного участка."
    if allowed is not None and covered is not None:
        coverage_note += f" В разрешённой области осталось покрыть около {number(max(0, allowed * (1 - covered / 100)), 'км²', 2)}."
    phase_colors = {"survey": "#2563eb", "transit": "#64748b", "maneuvers": "#d97706", "unknown": "#cbd5e1"}
    phase_svg, offset = "", 0
    for phase in a["phases"]:
        phase_svg += f'<rect x="{offset:.4f}" y="0" width="{phase["pct"]:.4f}" height="12" fill="{phase_colors[phase["key"]]}"/>'
        offset += phase["pct"]
    phases = (f'<svg class="phase-chart" viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden="true">{phase_svg}</svg><div class="phase-legend">' +
              "".join(f'<div><i style="background:{phase_colors[p["key"]]}"></i><span>{p["label"]}</span><b>{number(p["hours"], "ч")} · {number(p["pct"], "%", 0)}</b></div>' for p in a["phases"]) + '</div>') if a["phases"] else '<p class="muted">Нет данных об этапах полёта.</p>'
    max_flight = max((d["flight_h"] for d in a["drones"]), default=1) or 1
    layers = {d["id"]: d.get("transit_alt_agl_m") for d in plan.get("drones") or []}
    reserve = a["reserve_pct"]
    fleet_rows = ""
    for d in a["drones"]:
        remaining = d["remaining_pct"]
        bad = remaining is not None and reserve is not None and remaining < reserve
        incomplete = d["energy_samples"] != d["sorties"]
        reserve_label = number(remaining, "%") + (" *" if incomplete else "")
        fleet_rows += f'<tr><th scope="row">{e(d["id"])}<span class="drone-layer">AGL {number(layers.get(d["id"]), "м", 0)}</span></th><td>{d["sorties"]}</td><td><div class="bar-value">{bar(d["flight_h"], max_flight)}<span>{number(d["flight_h"], "ч", 2)}</span></div></td><td class="{"bad" if bad else ""}">{reserve_label}{" · ниже резерва" if bad else ""}</td></tr>'
    fleet = f'<table class="fleet"><thead><tr><th>Борт</th><th>Вылеты</th><th>Налёт, ч</th><th>Мин. остаток АКБ</th></tr></thead><tbody>{fleet_rows}</tbody></table>' if fleet_rows else '<p class="muted">Вылетов нет. Распределение по бортам не рассчитано.</p>'
    partial_note = " * Данные АКБ есть не для всех вылетов борта." if any(d["energy_samples"] != d["sorties"] for d in a["drones"]) else ""
    checks = [f"{'Пройдена' if c.get('passed') else 'НЕ ПРОЙДЕНА'}: {c['name']}" for c in r["checks"]]
    metrics = "".join(f'<div class="metric"><strong>{e(value)}</strong><span>{e(label)}</span></div>' for label, value in r["metrics"])
    conditions = "".join(f'<div><dt>{e(k)}</dt><dd>{e(v)}</dd></div>' for k, v in r["conditions"])
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Отчёт по миссии · {e(r['scenario'])}</title><style>
*{{box-sizing:border-box}}body{{font:14px/1.45 system-ui,sans-serif;color:#172338;margin:32px auto;max-width:920px;padding:0 24px}}
h1{{font-size:30px;letter-spacing:-.7px;margin:4px 0 8px}}h2{{font-size:18px;margin:22px 0 10px;break-after:avoid}}p{{margin:8px 0}}.eyebrow{{color:#2563eb;font-size:11px;font-weight:700;letter-spacing:2px}}.muted{{color:#536377;font-size:12px}}.status{{padding:12px 14px;background:#eef3fb;border-left:4px solid #2563eb;margin:16px 0}}.status.bad{{border-color:#b42318;background:#fff1ef}}.bad{{color:#b42318}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}.metric{{padding:12px;border:1px solid #dce3ed;border-radius:8px}}.metric strong{{display:block;font-size:24px}}.metric span{{display:block;font-size:11px;color:#536377;margin-top:4px}}
.chart{{border:1px solid #dce3ed;border-radius:8px;padding:14px 16px;margin:12px 0;break-inside:avoid}}.chart h2{{margin:0 0 12px}}.coverage-row{{display:grid;grid-template-columns:220px 1fr 75px;gap:16px;align-items:center;margin:10px 0}}.coverage-row span{{display:block}}.coverage-row strong{{text-align:right}}.bar{{width:100%;height:10px;display:block}}.phase-chart{{display:block;width:100%;height:18px;border-radius:3px;overflow:hidden}}.phase-legend{{display:grid;grid-template-columns:1fr 1fr;gap:8px 24px;margin:12px 0 0;font-size:12px}}.phase-legend div{{display:flex;align-items:center;gap:6px}}.phase-legend b{{margin-left:auto;white-space:nowrap}}.phase-legend i{{width:8px;height:8px;flex-shrink:0;border-radius:2px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:9px 8px;text-align:left;border-bottom:1px solid #dce3ed;overflow-wrap:anywhere}}thead{{display:table-header-group}}th{{font-weight:600}}.fleet th:first-child{{width:20%}}.fleet th:nth-child(2){{width:10%}}.fleet th:nth-child(3){{width:40%}}.fleet th:nth-child(4){{width:30%}}.drone-layer{{display:block;font-size:10px;font-weight:400;color:#536377}}.bar-value{{display:flex;align-items:center;gap:12px}}.bar-value .bar{{flex:1;min-width:30px}}.bar-value span{{min-width:64px;text-align:right}}.facts{{display:grid;grid-template-columns:1fr 1fr;gap:0 24px;margin:0;font-size:12px}}.facts>div{{padding:8px 0;border-bottom:1px solid #dce3ed;break-inside:avoid}}dt{{color:#536377;font-size:11px}}dd{{margin:3px 0 0;overflow-wrap:anywhere}}li{{margin:5px 0;overflow-wrap:anywhere}}ul{{padding-left:20px;margin:8px 0}}.checks ul{{columns:2;column-gap:30px;font-size:12px}}.checks li{{break-inside:avoid}}button{{padding:10px 18px;cursor:pointer}}footer{{margin-top:24px;padding-top:10px;border-top:1px solid #dce3ed}}
@media screen and (max-width:600px){{.metrics{{grid-template-columns:1fr 1fr}}.coverage-row{{grid-template-columns:1fr 1fr 65px;gap:8px}}.phase-legend,.facts{{grid-template-columns:1fr}}}}
@media print{{button{{display:none}}body{{margin:0;padding:0;font-size:10pt;max-width:none}}h1{{font-size:25pt}}h2{{font-size:13pt;margin-top:18px}}.metric strong{{font-size:19pt}}.details{{break-before:page}}tr,.status,.metrics{{break-inside:avoid}}.muted{{font-size:9pt}}.chart{{padding:12px 14px}}*{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}}}@page{{size:A4;margin:14mm 16mm}}
</style></head><body><button onclick="window.print()">Печать / сохранить PDF</button>
<header><p class="eyebrow">GEOSCAN · FLEET PLANNER</p><h1>Обзор миссии</h1><p>{e(r['scenario'])} · {e(r['objective'])}</p><p class="muted">План {e(r['plan_id'])} · {e(r['notice'])}</p></header>
<div class="status {'' if r['ready'] else 'bad'}"><b>{e(r['title'])}</b> · Проверки: {r['checks_passed']}/{len(r['checks'])}</div>
<div class="metrics">{metrics}</div>
<div class="chart"><h2>Покрытие территории</h2>{coverage}<p class="muted">{coverage_note}</p></div>
<div class="chart"><h2>На что уходит полётное время</h2>{phases}<p class="muted">Сумма времени всех бортов в воздухе. Наземные паузы и зарядка не включены.</p></div>
{section('Что требует внимания', r['issues'], 'Нарушения в результате расчёта не указаны. Условия и проверки приведены далее.')}
{section('Рекомендуемые действия', r['recommendations'])}
<div class="details"><p class="eyebrow">РЕСУРСЫ И ПРОВЕРКИ</p><h2>Нагрузка и запас АКБ по бортам</h2>{fleet}
<p class="muted">Длина полосы показывает налёт относительно самого загруженного борта. Остаток АКБ - минимум по его вылетам; заданный резерв: {number(reserve, '%')}.{partial_note}</p>
<h2>Условия и высоты</h2><dl class="facts">{conditions}</dl>
<div class="checks">{section('Независимая проверка', checks, 'Проверки не выполнены.')}</div>
{section('Решения планировщика', r['decisions'])}
{section('Замечания расчёта', r['warnings'])}</div>
<footer class="muted">{e(r['notice'])}</footer></body></html>'''
