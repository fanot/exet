"""Экспорт полетных заданий: GeoJSON, KML, CSV-расписание, QGroundControl .plan, ZIP.

GeoJSON (RFC 7946, WGS-84, координаты [lon, lat, alt_amsl_m]):
  * feature_type=route    — полный маршрут вылета (LineString 3D) + coord_times (ISO 8601 на каждую вершину);
  * feature_type=phase    — этап полета (takeoff / transit_out / descent / survey / climb / transit_back / landing);
  * feature_type=keypoint — ключевые точки (взлет, начало/конец съемки, посадка) с ETA;
  * feature_type=site / reserve_site — площадки; feature_type=uncovered — непокрытые участки.
  Сводка и расписание — во внешнем члене "mission" (разрешено RFC 7946 §6.1).
KML: папка на борт → папка на вылет: LineString (altitudeMode=absolute), gx:Track с <when> для
  воспроизведения во времени (Google Earth), точки этапов, ExtendedData с атрибутами.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Dict, List
from xml.sax.saxutils import escape

# цвета бортов — общие для интерфейса и файлов экспорта (различимы на карте и в печати)
PALETTE = ["#2563eb", "#e11d48", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#c026d3", "#65a30d", "#b45309", "#4f46e5",
           "#0d9488", "#be123c", "#475569", "#a16207", "#7c3aed"]

PHASE_RU = {"ground": "На земле", "takeoff": "Взлет и набор", "transit_out": "Перелет к участку", "descent": "Снижение на рабочую высоту",
            "survey": "Съемка", "climb": "Набор эшелона", "transit_back": "Возврат", "landing": "Посадка"}


def _iso(ts: float, tz_offset_iso: str) -> str:
    tz = datetime.fromisoformat(tz_offset_iso).tzinfo
    return datetime.fromtimestamp(ts, tz).isoformat(timespec="seconds")


def drone_colors(result) -> Dict[str, str]:
    return {d["id"]: d.get("color") or PALETTE[i % len(PALETTE)] for i, d in enumerate(result.get("drones", []))}


def subset(result: dict, drone_id: str) -> dict:
    """План одного борта — индивидуальное полетное задание (те же форматы, только его вылеты)."""
    drones = [d for d in result.get("drones", []) if d["id"] == drone_id]
    if not drones:
        raise KeyError(drone_id)
    return {**result, "id": f"{result['id']}_{drone_id}", "drones": drones,
            "sorties": [s for s in result["sorties"] if s["drone_id"] == drone_id]}


def to_geojson(result: dict) -> dict:
    tzref = result["summary"]["mission_start_iso"]
    colors = drone_colors(result)
    feats: List[dict] = []
    for s in result["sorties"]:
        W = s["waypoints"]
        coords = [[w[0], w[1], w[2]] for w in W]
        base = {"sortie_id": s["id"], "drone_id": s["drone_id"], "model": s["model"], "model_title": s["model_title"],
                "color": colors.get(s["drone_id"])}
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {
            **base, "feature_type": "route",
            "takeoff_site": s["site"], "landing_site": s["landing_site"],
            "takeoff_time": s["takeoff_iso"], "landing_time": s["landing_iso"],
            "duration_min": s["duration_min"], "distance_km": s["distance_km"], "survey_km": s["survey_km"],
            "battery_used_pct": s["battery_used_pct"], "photos": s["photos"],
            "survey_alt_agl_m": s["survey_alt_agl_m"], "transit_alt_agl_m": s["transit_alt_agl_m"],
            "survey_speed_ms": s["survey_speed_ms"], "transit_speed_ms": s["transit_speed_ms"],
            "launch_heading_deg": s.get("launch_heading_deg"),
            "alt_agl_m": [w[3] for w in W],
            "coord_times": [_iso(w[4], tzref) for w in W],
            "units": {"coordinates": "lon, lat [deg WGS-84], alt [m AMSL]", "speed": "m/s", "duration": "min", "distance": "km"},
        }})
        # этапы
        i = 0
        while i < len(W) - 1:
            ph = W[i + 1][5]
            j = i + 1
            while j + 1 < len(W) and W[j + 1][5] == ph:
                j += 1
            seg = [[w[0], w[1], w[2]] for w in W[i:j + 1]]
            if len(seg) >= 2:
                feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": seg}, "properties": {
                    **base, "feature_type": "phase", "phase": ph, "phase_ru": PHASE_RU.get(ph, ph),
                    "t_start": _iso(W[i][4], tzref), "t_end": _iso(W[j][4], tzref)}})
            i = j
        for kp in s.get("keypoints", []):
            w = W[kp["i"]]
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [w[0], w[1], w[2]]}, "properties": {
                **base, "feature_type": "keypoint", "label": kp["label"], "eta": _iso(w[4], tzref), "alt_agl_m": w[3]}})
    for st in result.get("sites", []):
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [st["lon"], st["lat"]]},
                      "properties": {"feature_type": "reserve_site" if st["reserve"] else "site", "name": st["name"]}})
    unc = result.get("geo", {}).get("uncovered")
    if unc:
        feats.append({"type": "Feature", "geometry": unc, "properties": {"feature_type": "uncovered"}})
    mission = {k: result[k] for k in ("id", "status", "scenario_name", "summary", "drones", "models", "warnings") if k in result}
    mission["schedule"] = [{k: s[k] for k in ("id", "drone_id", "model", "site", "takeoff_iso", "landing_iso", "duration_min",
                                              "distance_km", "battery_used_pct")} for s in result["sorties"]]
    return {"type": "FeatureCollection", "name": f"plan_{result['id']}", "mission": mission, "features": feats}


def _kml_color(hexcol: str, alpha="ff") -> str:
    h = hexcol.lstrip("#")
    return f"{alpha}{h[4:6]}{h[2:4]}{h[0:2]}"


def to_kml(result: dict) -> str:
    tzref = result["summary"]["mission_start_iso"]
    colors = drone_colors(result)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">',
           f'<Document><name>{escape("План " + result["id"] + " — " + result.get("scenario_name", ""))}</name>',
           f'<description>{escape(json.dumps(result["summary"], ensure_ascii=False))}</description>']
    for did, col in colors.items():
        out.append(f'<Style id="d_{escape(did)}"><LineStyle><color>{_kml_color(col)}</color><width>2.5</width></LineStyle>'
                   f'<IconStyle><color>{_kml_color(col)}</color></IconStyle></Style>')
    out.append('<Style id="site"><IconStyle><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/airports.png</href></Icon></IconStyle></Style>')
    out.append("<Folder><name>Площадки</name>")
    for st in result.get("sites", []):
        out.append(f'<Placemark><name>{escape(st["name"])}{" (резерв)" if st["reserve"] else ""}</name><styleUrl>#site</styleUrl>'
                   f'<ExtendedData><Data name="kind"><value>{"reserve_site" if st["reserve"] else "site"}</value></Data></ExtendedData>'
                   f'<Point><coordinates>{st["lon"]},{st["lat"]},0</coordinates></Point></Placemark>')
    out.append("</Folder>")
    for d in result.get("drones", []):
        out.append(f'<Folder><name>{escape(d["id"])} — {escape(d["title"])}</name>')
        for s in [x for x in result["sorties"] if x["drone_id"] == d["id"]]:
            W = s["waypoints"]
            ext = {"sortie_id": s["id"], "drone_id": s["drone_id"], "model": s["model"], "takeoff_site": s["site"],
                   "landing_site": s["landing_site"], "takeoff_time": s["takeoff_iso"], "landing_time": s["landing_iso"],
                   "duration_min": s["duration_min"], "distance_km": s["distance_km"], "battery_used_pct": s["battery_used_pct"],
                   "survey_alt_agl_m": s["survey_alt_agl_m"], "transit_alt_agl_m": s["transit_alt_agl_m"],
                   "survey_speed_ms": s["survey_speed_ms"], "transit_speed_ms": s["transit_speed_ms"], "photos": s["photos"]}
            ext["phases"] = json.dumps(s.get("phases", []), ensure_ascii=False)
            ed = "".join(f'<Data name="{k}"><value>{escape(str(v))}</value></Data>' for k, v in ext.items())
            coords = " ".join(f"{w[0]},{w[1]},{w[2]}" for w in W)
            ts, te = _iso(W[0][4], tzref), _iso(W[-1][4], tzref)
            out.append(f'<Folder><name>Вылет {s["id"]}: {s["takeoff_iso"][11:16]}–{s["landing_iso"][11:16]}</name>')
            out.append(f'<Placemark><name>Маршрут вылета {s["id"]}</name><TimeSpan><begin>{ts}</begin><end>{te}</end></TimeSpan>'
                       f'<styleUrl>#d_{escape(d["id"])}</styleUrl><ExtendedData>{ed}</ExtendedData>'
                       f'<LineString><altitudeMode>absolute</altitudeMode><coordinates>{coords}</coordinates></LineString></Placemark>')
            whens = "".join(f"<when>{_iso(w[4], tzref)}</when>" for w in W)
            gxc = "".join(f"<gx:coord>{w[0]} {w[1]} {w[2]}</gx:coord>" for w in W)
            out.append(f'<Placemark><name>Трек {s["id"]} (4D)</name><styleUrl>#d_{escape(d["id"])}</styleUrl>'
                       f'<gx:Track><altitudeMode>absolute</altitudeMode>{whens}{gxc}</gx:Track></Placemark>')
            for kp in s.get("keypoints", []):
                w = W[kp["i"]]
                out.append(f'<Placemark><name>{escape(kp["label"])}</name><TimeStamp><when>{_iso(w[4], tzref)}</when></TimeStamp>'
                           f'<styleUrl>#d_{escape(d["id"])}</styleUrl><ExtendedData><Data name="eta"><value>{_iso(w[4], tzref)}</value></Data>'
                           f'<Data name="alt_agl_m"><value>{w[3]}</value></Data></ExtendedData>'
                           f'<Point><altitudeMode>absolute</altitudeMode><coordinates>{w[0]},{w[1]},{w[2]}</coordinates></Point></Placemark>')
            out.append("</Folder>")
        out.append("</Folder>")
    out.append("</Document></kml>")
    return "\n".join(out)


def to_csv(result: dict) -> str:
    buf = io.StringIO()
    cols = ["id", "drone_id", "model_title", "site", "takeoff_iso", "landing_iso", "duration_min", "distance_km", "survey_km",
            "lines", "photos", "battery_used_pct", "survey_alt_agl_m", "transit_alt_agl_m", "survey_speed_ms", "ground_hold_s"]
    w = csv.writer(buf, delimiter=";")
    w.writerow(cols)
    for s in result["sorties"]:
        w.writerow([s.get(c) for c in cols])
    return buf.getvalue()


def to_qgc_plan(result: dict, sortie: dict, catalog) -> dict:
    """Полетное задание одного вылета в формате QGroundControl .plan (высоты AMSL)."""
    m = catalog.drones[sortie["model"]]
    geom = result["models"][sortie["model"]]
    W = sortie["waypoints"]
    items = []
    jid = 1

    def add(cmd, params, frame=0, alt=None):
        nonlocal jid
        items.append({"type": "SimpleItem", "autoContinue": True, "command": cmd, "doJumpId": jid, "frame": frame,
                      "params": params, "Altitude": alt, "AltitudeMode": 2, "AMSLAltAboveTerrain": None})
        jid += 1

    home = W[0]
    first_index = next((i for i, w in enumerate(W[1:], 1) if w[5] == "takeoff"), 1)
    first_climb = W[first_index]
    add(22, [15 if m.is_plane else 0, 0, 0, None, first_climb[1], first_climb[0], first_climb[2]], alt=first_climb[2])
    in_survey = False
    speed = None
    base = sortie.get("photo_base_m", geom.get("photo_base_m")) or 0
    for w in W[first_index + 1:-1]:
        target_speed = sortie["survey_speed_ms"] if w[5] == "survey" else sortie["transit_speed_ms"]
        if target_speed != speed:
            add(178, [1, target_speed, -1, 0, 0, 0, 0], frame=2)
            speed = target_speed
        if w[5] == "survey" and not in_survey:
            add(206, [base, 0, 1, 0, 0, 0, 0], frame=2)
            in_survey = True
        if w[5] != "survey" and in_survey:
            add(206, [0, 0, 0, 0, 0, 0, 0], frame=2)
            in_survey = False
        add(16, [0, 0, 0, None, w[1], w[0], w[2]], alt=w[2])
    last = W[-1]
    if in_survey:
        add(206, [0, 0, 0, 0, 0, 0, 0], frame=2)
    add(21, [0, 0, 0, None, last[1], last[0], last[2]], alt=last[2])
    return {"fileType": "Plan", "version": 1, "groundStation": "QGroundControl",
            "geoFence": {"circles": [], "polygons": [], "version": 2}, "rallyPoints": {"points": [], "version": 2},
            "mission": {"cruiseSpeed": sortie["transit_speed_ms"], "hoverSpeed": sortie["survey_speed_ms"],
                        "firmwareType": 12, "vehicleType": 1 if m.is_plane else 2, "globalPlanAltitudeMode": 2,
                        "plannedHomePosition": [home[1], home[0], home[2]], "version": 2, "items": items}}


def to_zip(result: dict, catalog) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("plan.geojson", json.dumps(to_geojson(result), ensure_ascii=False))
        z.writestr("plan.kml", to_kml(result))
        z.writestr("schedule.csv", "﻿" + to_csv(result))
        z.writestr("summary.json", json.dumps({k: result.get(k) for k in ("id", "status", "summary", "models", "drones",
                                                                         "validation", "warnings", "recommendations")},
                                              ensure_ascii=False, indent=1))
        for s in result["sorties"]:
            z.writestr(f"qgc/{s['drone_id']}_sortie{s['id']:03d}.plan", json.dumps(to_qgc_plan(result, s, catalog), ensure_ascii=False))
        from .brief import to_brief_html
        for d in result.get("drones", []):
            if not d.get("sorties"):
                continue
            one = subset(result, d["id"])
            z.writestr(f"drones/{d['id']}.kml", to_kml(one))
            z.writestr(f"drones/{d['id']}.geojson", json.dumps(to_geojson(one), ensure_ascii=False))
            z.writestr(f"drones/{d['id']}_brief.html", to_brief_html(result, d["id"]))
        z.writestr("brief.html", to_brief_html(result))
        from .report import report_html
        z.writestr("report.html", report_html(result))
        z.writestr("README.txt", "Состав архива:\n plan.geojson — все вылеты (WGS-84, высоты AMSL, coord_times)\n"
                                 " plan.kml — то же для Google Earth/Geoscan Planner (gx:Track — воспроизведение во времени)\n"
                                 " schedule.csv — расписание вылетов\n qgc/*.plan — полетные задания QGroundControl по вылетам\n"
                                 " drones/<борт>.kml|.geojson — индивидуальное полетное задание каждого БВС\n"
                                 " drones/<борт>_brief.html, brief.html — полетные задания для печати (маршрут, этапы, ключевые точки, профиль высот)\n"
                                 " summary.json — сводка, проверка ограничений, рекомендации\n"
                                 " report.html — общий отчёт по расчётной миссии: условия, высоты, проблемы и рекомендации; печать / PDF из браузера\n")
    return buf.getvalue()
