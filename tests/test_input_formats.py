"""Форматы входных данных организаторов: eTOD-препятствия в KML, слои отдельными файлами."""
import copy

from planner.geo import merge_scenarios, parse_geojson, parse_kml, parse_scenario_bytes
from planner.models import MissionParams
from planner.validate import validate_plan
from tests.conftest import square

ETOD_KML = """<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark id="1"><name>UHHH100910 OTHER:COMMUNICATION_TOWER</name><Polygon><extrude>1</extrude><altitudeMode>relativeToGround</altitudeMode>
<outerBoundaryIs><LinearRing><coordinates>30.500,60.150,69.5 30.5003,60.150,69.5 30.5003,60.1502,69.5 30.500,60.1502,69.5 30.500,60.150,69.5</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark id="2"><name>UHWW0001 BUILDING</name><Polygon><altitudeMode>absolute</altitudeMode>
<outerBoundaryIs><LinearRing><coordinates>30.51,60.16,145.2 30.5103,60.16,145.2 30.5103,60.1602,145.2 30.51,60.1602,145.2 30.51,60.16,145.2</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark id="3"><name>   </name><LineString><altitudeMode>absolute</altitudeMode><coordinates>30.49,60.14,120 30.52,60.15,125</coordinates></LineString></Placemark>
<Placemark id="4"><name>far away TREE</name><Polygon><altitudeMode>absolute</altitudeMode>
<outerBoundaryIs><LinearRing><coordinates>40,50,10 40.001,50,10 40.001,50.001,10 40,50,10</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>"""


def test_etod_obstacles_heights_and_refs():
    sc = parse_kml(ETOD_KML, name="высотные препятствия", partial=True)
    assert len(sc.obstacles) == 4
    tower, building, line = sc.obstacles[0], sc.obstacles[1], sc.obstacles[2]
    assert tower.height_m == 69.5 and tower.alt_ref == "AGL"
    assert building.height_m == 145.2 and building.alt_ref == "AMSL"
    assert line.geom.geom_type == "LineString" and line.height_m == 125 and line.radius_m > 0
    assert line.name == "3"  # пустое имя → id


def test_etod_names_without_file_hint():
    sc = parse_kml(ETOD_KML.replace("far away TREE", "far away TREE"), name="layer", partial=True)
    # без подсказки по имени файла: тип по кодам eTOD (TOWER, BUILDING, TREE), безымянный объект с высотой в Z —
    # препятствие по большинству объектов документа
    assert len(sc.obstacles) == 4


def test_layers_merge_and_crop():
    area = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "survey_area"}, "geometry": {"type": "Polygon", "coordinates": [square(30.5, 60.15, 3)]}},
        {"type": "Feature", "properties": {"kind": "site"}, "geometry": {"type": "Point", "coordinates": [30.47, 60.15]}}]}
    import json
    a = parse_scenario_bytes(json.dumps(area).encode(), "область съемки.geojson", partial=True)
    o = parse_scenario_bytes(ETOD_KML.encode(), "высотные препятствия.kml", partial=True)
    sc = merge_scenarios([a, o], name="merged")
    assert len(sc.areas) == 1 and len(sc.sites) == 1
    assert len(sc.obstacles) == 3  # дальнее препятствие (40°E) отрезано по району работ


def test_validator_catches_flight_below_amsl_obstacle(toksovo):
    sc = parse_geojson(copy.deepcopy(toksovo))
    from planner.geo import Obstacle
    from shapely.geometry import Point
    sc.obstacles = [Obstacle(name="мачта", geom=Point(30.50, 60.15).buffer(0.002), height_m=400.0, alt_ref="AMSL")]
    p = MissionParams(fleet=[{"model": "geoscan_gemini", "count": 1}], use_terrain=False, respect_daylight=False)
    t0 = 1.79e9
    wp = [[30.49, 60.15, 0, 0, t0, "ground"], [30.49, 60.15, 150, 150, t0 + 30, "takeoff"],
          [30.51, 60.15, 150, 150, t0 + 130, "survey"], [30.51, 60.15, 0, 0, t0 + 160, "landing"]]
    plan = {"sorties": [{"id": 1, "drone_id": "D1", "model": "geoscan_gemini", "t_takeoff": t0, "t_landing": t0 + 160,
                         "distance_km": 1.2, "waypoints": wp}]}
    v = validate_plan(plan, sc, p)
    ob = next(c for c in v["checks"] if c["name"] == "Высотные препятствия")
    assert not ob["passed"]  # борт на 150 м AMSL ниже отметки верха 400 м AMSL
