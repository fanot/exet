"""Полетное задание для печати (HTML, A4): по борту — вылеты с маршрутом, этапами, ключевыми точками и профилем высот.

ТЗ: «индивидуальное полетное задание должно состоять из маршрута и ключевых точек, высоты, скорости, места старта
и посадки, последовательности этапов полета». Машиночитаемые форматы — KML/GeoJSON/.plan; этот документ —
то же задание в виде, пригодном для расчета на площадке (печать или PDF из браузера).
"""
from __future__ import annotations

import math
from datetime import datetime
from html import escape
from typing import List, Optional

from .export import PHASE_RU, drone_colors

PHASE_COLOR = {"ground": "#94a3b8", "takeoff": "#64748b", "transit_out": "#64748b", "descent": "#64748b",
               "survey": None, "climb": "#64748b", "transit_back": "#64748b", "landing": "#64748b"}


def _tz(result):
    return datetime.fromisoformat(result["summary"]["mission_start_iso"]).tzinfo


def _hm(ts, tz, sec=False):
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S" if sec else "%H:%M")


def _num(v, d=0):
    if v is None:
        return "—"
    return f"{v:,.{d}f}".replace(",", " ").replace(".", ",")


def _route_svg(s, sites, color, w=330, h=250) -> str:
    """Схема маршрута вылета в равнопромежуточной проекции (север вверху) с масштабной линейкой."""
    W = s["waypoints"]
    lat0 = sum(p[1] for p in W) / len(W)
    kx = math.cos(math.radians(lat0)) * 111320.0
    ky = 110540.0
    xs = [p[0] * kx for p in W]
    ys = [p[1] * ky for p in W]
    near = [st for st in sites if min(abs(st["lon"] * kx - x) + abs(st["lat"] * ky - y) for x, y in zip(xs[::10] or xs, ys[::10] or ys)) < 4000]
    ax = xs + [st["lon"] * kx for st in near]
    ay = ys + [st["lat"] * ky for st in near]
    x0, x1, y0, y1 = min(ax), max(ax), min(ay), max(ay)
    span = max(x1 - x0, y1 - y0, 200.0)
    pad = 16
    sc = min((w - 2 * pad) / max(x1 - x0, 1), (h - 2 * pad - 14) / max(y1 - y0, 1), (w - 2 * pad) / span * 2)
    ox = pad + ((w - 2 * pad) - (x1 - x0) * sc) / 2
    oy = pad + ((h - 2 * pad - 14) - (y1 - y0) * sc) / 2

    def P(x, y):
        return ox + (x - x0) * sc, h - 14 - (oy + (y - y0) * sc)

    parts = [f'<svg class="map" viewBox="0 0 {w} {h}" width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">',
             f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="6" fill="#f8fafc" stroke="#e2e8f0"/>']
    seg, cur = [], None
    for i in range(1, len(W)):
        surv = W[i][5] == "survey"
        if cur is None or cur[0] != surv:
            cur = (surv, [P(xs[i - 1], ys[i - 1])])
            seg.append(cur)
        cur[1].append(P(xs[i], ys[i]))
    for surv, pts in seg:
        d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        if surv:
            parts.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linejoin="round"/>')
        else:
            parts.append(f'<polyline points="{d}" fill="none" stroke="#64748b" stroke-width="1.2" stroke-dasharray="4 3"/>')
    for st in near:
        x, y = P(st["lon"] * kx, st["lat"] * ky)
        lab = "R" if st["reserve"] else "H"
        parts.append(f'<rect x="{x - 7:.1f}" y="{y - 7:.1f}" width="14" height="14" rx="3" fill="{"#64748b" if st["reserve"] else "#0f172a"}"/>'
                     f'<text x="{x:.1f}" y="{y + 3.5:.1f}" font-size="9" font-weight="700" fill="#fff" text-anchor="middle">{lab}</text>')
    for kp in s.get("keypoints", []):
        x, y = P(xs[kp["i"]], ys[kp["i"]])
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="#fff" stroke="#0f172a" stroke-width="1.2"/>')
    # масштабная линейка
    target = span / 4
    mag = 10 ** math.floor(math.log10(max(target, 1)))
    bar = min((1, 2, 5, 10), key=lambda k: abs(k * mag - target)) * mag
    bx, by = pad, h - 8
    parts.append(f'<line x1="{bx}" y1="{by}" x2="{bx + bar * sc:.1f}" y2="{by}" stroke="#0f172a" stroke-width="2"/>'
                 f'<text x="{bx + bar * sc + 4:.1f}" y="{by + 3}" font-size="9" fill="#475569">{_num(bar / 1000, 1 if bar < 1000 else 0)} км</text>'
                 f'<text x="{w - pad}" y="{pad + 4}" font-size="10" font-weight="700" fill="#475569" text-anchor="end">С ↑</text></svg>')
    return "".join(parts)


def _profile_svg(s, color, ceiling: Optional[float], w=430, h=250) -> str:
    """Профиль вылета: рельеф под трассой (заливка), высота борта AMSL, эшелон и потолок AGL."""
    W = s["waypoints"]
    t0 = W[0][4]
    T = [(p[4] - t0) / 60 for p in W]
    Z = [p[2] for p in W]
    G = [p[2] - p[3] for p in W]
    tmax = max(T[-1], 1e-6)
    zmin = min(G) - 10
    zmax = max(Z) + 25
    if ceiling:
        cz = [g + ceiling for g in G]
        if min(cz) < zmax + 60:
            zmax = max(zmax, max(cz) + 10)
    else:
        cz = None
    l, r, t, b = 44, 10, 12, 30

    def X(v):
        return l + (w - l - r) * v / tmax

    def Y(v):
        return t + (h - t - b) * (1 - (v - zmin) / max(zmax - zmin, 1))

    parts = [f'<svg class="prof" viewBox="0 0 {w} {h}" width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">']
    step = next((k for k in (10, 20, 25, 50, 100, 200, 250, 500, 1000) if (zmax - zmin) / k <= 6), 2000)
    v = math.ceil(zmin / step) * step
    while v <= zmax:
        parts.append(f'<line x1="{l}" x2="{w - r}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#eef2f7"/>'
                     f'<text x="{l - 5}" y="{Y(v) + 3:.1f}" font-size="9" fill="#64748b" text-anchor="end">{v:.0f}</text>')
        v += step
    tstep = next((k for k in (1, 2, 5, 10, 15, 20, 30, 60) if tmax / k <= 8), 120)
    v = 0.0
    while v <= tmax + 1e-6:
        parts.append(f'<text x="{X(v):.1f}" y="{h - b + 13}" font-size="9" fill="#64748b" text-anchor="middle">{v:.0f}</text>')
        v += tstep
    parts.append(f'<text x="{(l + w - r) / 2}" y="{h - 4}" font-size="9" fill="#64748b" text-anchor="middle">минуты от взлета</text>'
                 f'<text x="10" y="{t + (h - t - b) / 2}" font-size="9" fill="#64748b" text-anchor="middle" '
                 f'transform="rotate(-90 10 {t + (h - t - b) / 2})">высота, м н.у.м.</text>')
    ground = " ".join(f"{X(a):.1f},{Y(g):.1f}" for a, g in zip(T, G))
    parts.append(f'<polygon points="{X(0):.1f},{Y(zmin):.1f} {ground} {X(tmax):.1f},{Y(zmin):.1f}" fill="#e7e1d6" stroke="#a8a29e" stroke-width="0.8"/>')
    if cz and min(cz) < zmax:
        parts.append(f'<polyline points="{" ".join(f"{X(a):.1f},{Y(c):.1f}" for a, c in zip(T, cz))}" fill="none" stroke="#dc2626" '
                     f'stroke-width="1" stroke-dasharray="5 3"/><text x="{w - r - 2}" y="{Y(cz[-1]) - 4:.1f}" font-size="9" fill="#dc2626" '
                     f'text-anchor="end">потолок {ceiling:.0f} м AGL</text>')
    seg, cur = [], None
    for i in range(1, len(W)):
        surv = W[i][5] == "survey"
        if cur is None or cur[0] != surv:
            cur = (surv, [(T[i - 1], Z[i - 1])])
            seg.append(cur)
        cur[1].append((T[i], Z[i]))
    for surv, pts in seg:
        d = " ".join(f"{X(a):.1f},{Y(z):.1f}" for a, z in pts)
        dash = "" if surv else 'stroke-dasharray="4 2"'
        parts.append(f'<polyline points="{d}" fill="none" stroke="{color if surv else "#475569"}" stroke-width="{1.8 if surv else 1.3}" '
                     f'{dash} stroke-linejoin="round"/>')
    parts.append("</svg>")
    return "".join(parts)


def _sortie_html(result, s, color, tz, ceiling) -> str:
    W = s["waypoints"]
    rows = []
    for ph in s.get("phases", []):
        if ph["phase"] == "ground":
            continue
        ws = [p for p in W if ph["t_start"] - 0.5 <= p[4] <= ph["t_end"] + 0.5] or W[:1]
        a0, a1 = min(p[3] for p in ws), max(p[3] for p in ws)
        alt = f"{_num(a0)}" if abs(a1 - a0) < 3 else f"{_num(a0)}–{_num(a1)}"
        rows.append(f"<tr><td>{escape(PHASE_RU.get(ph['phase'], ph['phase']))}</td><td>{_hm(ph['t_start'], tz, True)}</td>"
                    f"<td>{_hm(ph['t_end'], tz, True)}</td><td class=n>{_num((ph['t_end'] - ph['t_start']) / 60, 1)}</td><td class=n>{alt}</td></tr>")
    kps = []
    for kp in s.get("keypoints", []):
        p = W[kp["i"]]
        kps.append(f"<tr><td>{escape(kp['label'])}</td><td>{_hm(p[4], tz, True)}</td><td class=n>{p[1]:.6f}</td><td class=n>{p[0]:.6f}</td>"
                   f"<td class=n>{_num(p[2])}</td><td class=n>{_num(p[3])}</td></tr>")
    m = result.get("models", {}).get(s["model"], {})
    facts = [("Взлет", f"{escape(s['site'])}, {s['takeoff_iso'][11:16]}"), ("Посадка", f"{escape(s['landing_site'])}, {s['landing_iso'][11:16]}"),
             ("Длительность", f"{_num(s['duration_min'])} мин"), ("Расход АКБ", f"{_num(s['battery_used_pct'])} % (резерв ≥ 20 %)"),
             ("Путь", f"{_num(s['distance_km'], 1)} км, съемка {_num(s['survey_km'], 1)} км"),
             ("Съемка", f"{_num(s['survey_alt_agl_m'])} м AGL · {_num(s['survey_speed_ms'], 1)} м/с"),
             ("Перелет", f"эшелон {_num(s['transit_alt_agl_m'])} м AGL · {_num(s['transit_speed_ms'], 1)} м/с"),
             ("Галсов / снимков", f"{s['lines']} / {_num(s['photos'])}")]
    if m.get("photo_base_m"):
        facts.append(("Базис фотографирования", f"{_num(m['photo_base_m'], 1)} м · шаг галсов {_num(m.get('line_spacing_m'), 0)} м"))
    if s.get("ground_hold_s"):
        facts.append(("Удержание на земле", f"{s['ground_hold_s']} с (разведение)"))
    if s.get("launch_heading_deg") is not None:
        facts.append(("Курс запуска (катапульта)", f"{s['launch_heading_deg']}° — против ветра"))
    fx = "".join(f"<div class=f><span>{a}</span><b>{b}</b></div>" for a, b in facts)
    return (f'<section class="sortie"><h3><i style="background:{color}"></i>Вылет {s["id"]} · {s["takeoff_iso"][11:16]}–{s["landing_iso"][11:16]}'
            f'<small>{s["takeoff_iso"][:10]}</small></h3>'
            f'<div class="grid"><div>{_route_svg(s, result.get("sites", []), color)}</div><div>{_profile_svg(s, color, ceiling)}</div></div>'
            f'<div class="facts">{fx}</div>'
            f'<div class="grid2"><table><tr><th>Этап</th><th>Начало</th><th>Конец</th><th class=n>мин</th><th class=n>H AGL, м</th></tr>{"".join(rows)}</table>'
            f'<table><tr><th>Ключевая точка</th><th>Время</th><th class=n>Широта</th><th class=n>Долгота</th><th class=n>H, м н.у.м.</th>'
            f'<th class=n>H AGL</th></tr>{"".join(kps)}</table></div></section>')


CSS = """
*{box-sizing:border-box}body{margin:0;font:12px/1.45 Inter,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:#0f172a;background:#eef1f5}
.page{max-width:900px;margin:24px auto;background:#fff;padding:28px 32px;border-radius:12px;box-shadow:0 8px 30px rgba(15,23,42,.08)}
h1{font-size:22px;margin:0 0 2px;letter-spacing:-.01em}h2{font-size:16px;margin:28px 0 8px;display:flex;align-items:center;gap:8px;
border-top:2px solid #0f172a;padding-top:12px}h2 small,h3 small{font-weight:500;color:#64748b;margin-left:auto;font-size:12px}
h3{font-size:13px;margin:0 0 8px;display:flex;align-items:center;gap:8px}h2 i,h3 i{width:12px;height:12px;border-radius:3px;display:inline-block}
.sub{color:#64748b}.kv{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:14px 0}.kv div{border:1px solid #e2e8f0;border-radius:8px;padding:8px 10px}
.kv b{display:block;font-size:17px}.kv span{color:#64748b;font-size:11px}
.sortie{border:1px solid #e2e8f0;border-radius:10px;padding:12px 14px;margin:10px 0;break-inside:avoid}
.grid{display:grid;grid-template-columns:330px 1fr;gap:10px}.grid2{display:grid;grid-template-columns:1fr 1.25fr;gap:10px;margin-top:8px}
svg.prof{width:100%;height:auto}.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:4px 14px;margin-top:8px}
.f{display:flex;justify-content:space-between;gap:8px;border-bottom:1px dotted #e2e8f0;padding:2px 0}.f span{color:#64748b}.f b{font-weight:600;text-align:right}
table{width:100%;border-collapse:collapse;font-size:11px}th{text-align:left;color:#64748b;font-weight:500;border-bottom:1px solid #cbd5e1;padding:3px 4px}
td{border-bottom:1px solid #f1f5f9;padding:3px 4px}.n{text-align:right;font-variant-numeric:tabular-nums}
.sched td,.sched th{padding:4px 6px}.ok{color:#15803d}.bad{color:#b91c1c}
.bar{position:sticky;top:0;background:#0f172a;color:#fff;padding:8px 16px;display:flex;gap:12px;align-items:center;z-index:2}
.bar button{font:inherit;background:#2563eb;color:#fff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer}
.bar a{color:#cbd5e1}
@media print{body{background:#fff}.page{box-shadow:none;margin:0;max-width:none;padding:0}.bar{display:none}
.drone{break-before:page}.drone:first-of-type{break-before:auto}@page{size:A4;margin:12mm}}
"""


def to_brief_html(result: dict, drone_id: Optional[str] = None) -> str:
    tz = _tz(result)
    colors = drone_colors(result)
    s_all = result.get("sorties", [])
    drones = [d for d in result.get("drones", []) if d.get("sorties") and (drone_id is None or d["id"] == drone_id)]
    sm = result["summary"]
    ceiling = sm.get("ceiling_agl_m")
    v = result.get("validation") or {}
    checks = v.get("checks", [])
    passed = sum(1 for c in checks if c.get("passed"))
    title = f"Полетное задание{' — ' + drone_id if drone_id else ''}"
    head = (f'<h1>{escape(title)}</h1><div class="sub">План {escape(result["id"])} · {escape(result.get("scenario_name") or "")} · '
            f'{"минимум времени" if sm.get("objective") == "time" else "минимум налета"} · '
            f'время местное ({datetime.fromisoformat(sm["mission_start_iso"]).strftime("UTC%z")})</div>')
    kv = (f'<div class="kv"><div><b>{_num(sm.get("makespan_h"), 1)} ч</b><span>время работ всей группы</span></div>'
          f'<div><b>{_num(sm.get("flight_hours"), 1)} ч</b><span>суммарный налет</span></div>'
          f'<div><b>{sm.get("sorties")}</b><span>вылетов · {sm.get("drones_used")} бортов</span></div>'
          f'<div><b class="{"ok" if v.get("passed") else "bad"}">{passed}/{len(checks)}</b><span>crash-тест (независимая проверка)</span></div></div>')
    sched = ['<table class="sched"><tr><th>Борт</th><th>Тип</th><th class=n>Вылетов</th><th class=n>Налет, ч</th><th class=n>Эшелон, м</th>'
             '<th>Первый взлет</th><th>Последняя посадка</th></tr>']
    for d in drones:
        ss = [s for s in s_all if s["drone_id"] == d["id"]]
        sched.append(f'<tr><td><i style="display:inline-block;width:9px;height:9px;border-radius:2px;background:{colors[d["id"]]}"></i> '
                     f'<b>{escape(d["id"])}</b></td><td>{escape(d["title"])}</td><td class=n>{len(ss)}</td><td class=n>{_num(d["flight_h"], 2)}</td>'
                     f'<td class=n>{_num(d["transit_alt_agl_m"])}</td><td>{ss[0]["takeoff_iso"][:16].replace("T", " ")}</td>'
                     f'<td>{ss[-1]["landing_iso"][:16].replace("T", " ")}</td></tr>')
    sched.append("</table>")
    body: List[str] = []
    for d in drones:
        ss = [s for s in s_all if s["drone_id"] == d["id"]]
        body.append(f'<div class="drone"><h2><i style="background:{colors[d["id"]]}"></i>{escape(d["id"])} — {escape(d["title"])}'
                    f'<small>{len(ss)} вылетов · налет {_num(d["flight_h"], 2)} ч · эшелон перелета {_num(d["transit_alt_agl_m"])} м AGL</small></h2>')
        body.extend(_sortie_html(result, s, colors[d["id"]], tz, ceiling) for s in ss)
        body.append("</div>")
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{escape(title)} · {escape(result["id"])}</title>'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><style>{CSS}</style></head><body>'
            f'<div class="bar"><b>Geoscan Fleet Planner</b><span>{escape(title)}</span><span style="flex:1"></span>'
            f'<button onclick="print()">Печать / PDF</button></div>'
            f'<div class="page">{head}{kv}{"".join(sched)}{"".join(body)}'
            f'<p class="sub" style="margin-top:20px">Координаты — WGS-84; высоты: AGL — над землей (ЦМР), н.у.м. — над уровнем моря. '
            f'Полные траектории с метками времени — в KML/GeoJSON и QGroundControl .plan того же плана.</p></div></body></html>')
