"""Генерация демонстрационных сценариев (иллюстративные зоны, не реальные ограничения)."""
import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "scenarios"


def circle(lon, lat, r_m, n=48):
    k = 111320.0
    return [[lon + r_m * math.cos(2 * math.pi * i / n) / (k * math.cos(math.radians(lat))),
             lat + r_m * math.sin(2 * math.pi * i / n) / k] for i in range(n)] + [[lon + r_m / (k * math.cos(math.radians(lat))), lat]]


def feat(kind, geom, **props):
    return {"type": "Feature", "properties": {"kind": kind, **props}, "geometry": geom}


def poly(coords, holes=()):
    ring = coords + [coords[0]] if coords[0] != coords[-1] else coords
    return {"type": "Polygon", "coordinates": [ring, *[h + [h[0]] if h[0] != h[-1] else h for h in holes]]}


def demo_toksovo():
    area = [[30.400, 60.100], [30.580, 60.104], [30.602, 60.148], [30.572, 60.190], [30.452, 60.196], [30.392, 60.160]]
    lake = circle(30.505, 60.163, 650, 24)
    feats = [
        feat("survey_area", poly(area, [lake]), name="Участок аэрофотосъемки «Токсово»"),
        feat("no_fly", poly(circle(30.468, 60.122, 1100)), name="Населенный пункт (постоянная)"),
        feat("no_fly", poly([[30.575, 60.120], [30.600, 60.121], [30.605, 60.140], [30.580, 60.139]]),
             name="Охраняемый объект"),
        feat("no_fly", poly(circle(30.548, 60.176, 700)), name="Временная зона (массовое мероприятие)",
             active_from="2026-09-25T12:00:00+03:00", active_to="2026-09-25T16:00:00+03:00"),
        feat("no_fly", poly([[30.38, 60.145], [30.62, 60.145], [30.62, 60.152], [30.38, 60.152]]),
             name="Воздушная трасса выше 450 м", alt_min_m=450, alt_max_m=3000, alt_ref="AGL"),
        feat("obstacle", {"type": "Point", "coordinates": [30.522, 60.138]}, name="Вышка связи", height_m=120, radius_m=15),
        feat("obstacle", {"type": "Point", "coordinates": [30.432, 60.180]}, name="Дымовая труба", height_m=180, radius_m=10),
        feat("obstacle", {"type": "LineString", "coordinates": [[30.395, 60.132], [30.60, 60.170]]}, name="ЛЭП 110 кВ", height_m=35, radius_m=20),
        feat("site", {"type": "Point", "coordinates": [30.386, 60.128]}, name="Площадка А (запад)"),
        feat("site", {"type": "Point", "coordinates": [30.607, 60.172]}, name="Площадка Б (восток)"),
        feat("reserve_site", {"type": "Point", "coordinates": [30.500, 60.200]}, name="Резервная площадка (север)"),
    ]
    return {"type": "FeatureCollection", "name": "Токсово, 100 км² (площадь + зоны 3D/4D)",
            "planning_hint": {"start_time": "2026-09-25T09:00:00+03:00"}, "features": feats}


def demo_pipeline():
    # коридор вдоль трассы трубопровода ~35 км с двумя площадными участками (узлы)
    pts = [[30.10, 59.80], [30.25, 59.84], [30.40, 59.83], [30.55, 59.88], [30.70, 59.90]]
    k = 111320.0
    half = 250 / k
    left, right = [], []
    for i, (lo, la) in enumerate(pts):
        a = pts[max(i - 1, 0)]
        b = pts[min(i + 1, len(pts) - 1)]
        dx, dy = (b[0] - a[0]) * math.cos(math.radians(la)), b[1] - a[1]
        n = math.hypot(dx, dy)
        nx, ny = -dy / n, dx / n
        left.append([lo + nx * half / math.cos(math.radians(la)), la + ny * half])
        right.append([lo - nx * half / math.cos(math.radians(la)), la - ny * half])
    corridor = left + right[::-1]
    node1 = [[30.23, 59.825], [30.28, 59.825], [30.28, 59.855], [30.23, 59.855]]
    node2 = [[30.53, 59.865], [30.58, 59.865], [30.58, 59.895], [30.53, 59.895]]
    feats = [
        feat("survey_area", {"type": "MultiPolygon", "coordinates": [[corridor + [corridor[0]]]]}, name="Коридор трубопровода"),
        feat("survey_area", poly(node1), name="Узел 1 (КС)"),
        feat("survey_area", poly(node2), name="Узел 2 (НПС)"),
        feat("no_fly", poly(circle(30.40, 59.845, 1500)), name="Аэродром (постоянная)"),
        feat("obstacle", {"type": "Point", "coordinates": [30.26, 59.84]}, name="Факельная установка", height_m=90, radius_m=30),
        feat("site", {"type": "Point", "coordinates": [30.12, 59.79]}, name="База 1"),
        feat("site", {"type": "Point", "coordinates": [30.47, 59.87]}, name="База 2"),
        feat("reserve_site", {"type": "Point", "coordinates": [30.68, 59.91]}, name="Резерв"),
    ]
    return {"type": "FeatureCollection", "name": "Трубопровод 35 км + 2 узла (линейный объект)",
            "planning_hint": {"start_time": "2026-09-25T09:00:00+03:00"}, "features": feats}


def demo_primorye(obstacles_kml: str):
    """Артем (Приморский край): реальные высотные препятствия из примера организаторов (eTOD, абсолютные отметки),
    зона аэродрома Кневичи — иллюстративная (круг 4 км), не реальные границы."""
    import shapely
    from shapely.geometry import mapping
    from planner.geo import parse_scenario_bytes
    area = [[132.125, 43.300], [132.245, 43.302], [132.250, 43.345], [132.238, 43.388], [132.150, 43.392], [132.122, 43.350]]
    obs_sc = parse_scenario_bytes(open(obstacles_kml, "rb").read(), "высотные препятствия.kml", partial=True)
    bb = shapely.box(132.09, 43.27, 132.29, 43.42)
    feats = [feat("survey_area", poly(area), name="Участок аэрофотосъемки «Артем»"),
             feat("no_fly", poly(circle(132.148, 43.399, 4000)), name="Аэродром Кневичи (иллюстративно, R=4 км)")]
    n = 0
    for o in obs_sc.obstacles:
        if o.geom.intersects(bb):
            feats.append(feat("obstacle", mapping(o.geom), name=o.name, height_m=round(o.height_m, 1), alt_ref=o.alt_ref,
                              radius_m=o.radius_m or None))
            n += 1
    feats += [feat("site", {"type": "Point", "coordinates": [132.112, 43.318]}, name="Площадка А (запад)"),
              feat("site", {"type": "Point", "coordinates": [132.262, 43.340]}, name="Площадка Б (восток)"),
              feat("reserve_site", {"type": "Point", "coordinates": [132.190, 43.288]}, name="Резервная площадка (юг)")]
    return {"type": "FeatureCollection", "name": f"Артем, Приморье — {n} реальных препятствий (данные организаторов)",
            "planning_hint": {"start_time": "2026-09-25T09:00:00+10:00"}, "features": feats}


def demo_moscow_task(folder: str) -> dict:
    """Второй пакет организаторов: задание на съемку «Границы полетов.kml» (без площадок — расставляются
    автоматически), зоны «Московская зона.kml» (высоты текстом, временные ограничения без времени действия),
    3D-препятствия «obstacles_Московская область.kml». Зоны и препятствия обрезаны до района работ."""
    from api.app import scenario_to_geojson
    from planner.geo import merge_scenarios, parse_scenario_bytes

    D = Path(folder)
    parts = [parse_scenario_bytes((D / f).read_bytes(), f, partial=True)
             for f in ("Границы полетов.kml", "Московская зона.kml", "obstacles_Московская область.kml")]
    sc = merge_scenarios(parts, name="x", partial=True)
    sc.crop_to_work()
    gj = scenario_to_geojson(sc)
    gj.pop("notes", None)
    gj["name"] = "Задание организаторов: Московская обл., 245 км² (570 участков, зоны, 1837 препятствий) — расчет 5–10 мин"
    gj["planning_hint"] = {"start_time": "2026-09-25T08:00:00+03:00"}
    return gj


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    (OUT / "demo_toksovo.geojson").write_text(json.dumps(demo_toksovo(), ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "demo_pipeline.geojson").write_text(json.dumps(demo_pipeline(), ensure_ascii=False, indent=1), encoding="utf-8")
    import os
    import sys
    sys.path.insert(0, str(OUT.parent))
    src = os.environ.get("PRIMORYE_OBSTACLES_KML", str(Path.home() / "Downloads" / "хакатон" / "высотные препятствия Приморский край.kml"))
    if Path(src).exists():
        (OUT / "demo_primorye.geojson").write_text(json.dumps(demo_primorye(src), ensure_ascii=False), encoding="utf-8")
    src2 = os.environ.get("ORGANIZER_FOLDER_2", str(Path.home() / "Downloads" / "хакатон-2"))
    if (Path(src2) / "Границы полетов.kml").exists():
        (OUT / "demo_moscow_task.geojson").write_text(json.dumps(demo_moscow_task(src2), ensure_ascii=False, separators=(",", ":")),
                                                      encoding="utf-8")
    print("ok")
