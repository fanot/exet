"""Форматы второго пакета организаторов: задание на съемку без площадок, зоны с текстовыми высотами, 3D-препятствия.

Реальные файлы («Границы полетов.kml», «Московская зона.kml», «obstacles_Московская область.kml») большие и в репозиторий
не входят — здесь их структура воспроизведена в миниатюре.
"""
import pytest

from planner.advisor import advise
from planner.geo import merge_scenarios, parse_altitudes, parse_kml
from planner.models import MissionParams
from planner.optimizer import plan

LON, LAT = 38.76, 54.87

AREA_KML = f"""<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Schema id="500" name="500"><SimpleField name="FID" type="float"/></Schema><Folder><name>500</name>
<Placemark><ExtendedData><SchemaData schemaUrl="#500"><SimpleData name="FID">1</SimpleData></SchemaData></ExtendedData>
<Polygon><altitudeMode>clampToGround</altitudeMode><outerBoundaryIs><LinearRing><coordinates>
{LON},{LAT} {LON + 0.03},{LAT} {LON + 0.03},{LAT + 0.02} {LON},{LAT + 0.02} {LON},{LAT}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>
{LON + 0.05},{LAT} {LON + 0.07},{LAT} {LON + 0.07},{LAT + 0.015} {LON + 0.05},{LAT + 0.015} {LON + 0.05},{LAT}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>{LON},{LAT} {LON},{LAT} {LON},{LAT}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Folder></Document></kml>"""

ZONES_KML = f"""<?xml version='1.0' encoding='utf-8'?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>UUP1</name><ExtendedData><Data name="Name"><value>UUP1</value></Data>
<Data name="Altitudes"><value>От земли до 900 м (3000 фут) AMSL</value></Data><Data name="Type"><value>запретная_зона</value></Data></ExtendedData>
<MultiGeometry><Polygon><outerBoundaryIs><LinearRing><coordinates>{LON + 0.01},{LAT + 0.005} {LON + 0.015},{LAT + 0.005} {LON + 0.015},{LAT + 0.01} {LON + 0.01},{LAT + 0.01} {LON + 0.01},{LAT + 0.005}</coordinates></LinearRing></outerBoundaryIs></Polygon></MultiGeometry></Placemark>
<Placemark><name>UUR2</name><ExtendedData><Data name="Altitudes"><value>От FL150 до FL980</value></Data><Data name="Type"><value>врем_ограничение</value></Data></ExtendedData>
<MultiGeometry><Polygon><outerBoundaryIs><LinearRing><coordinates>{LON - 0.1},{LAT - 0.1} {LON + 0.2},{LAT - 0.1} {LON + 0.2},{LAT + 0.2} {LON - 0.1},{LAT + 0.2} {LON - 0.1},{LAT - 0.1}</coordinates></LinearRing></outerBoundaryIs></Polygon></MultiGeometry></Placemark>
<Placemark><name>FAR</name><ExtendedData><Data name="Altitudes"><value>На всех высотах</value></Data><Data name="Type"><value>пост_ограничение</value></Data></ExtendedData>
<MultiGeometry><Polygon><outerBoundaryIs><LinearRing><coordinates>45,50 45.1,50 45.1,50.1 45,50.1 45,50</coordinates></LinearRing></outerBoundaryIs></Polygon></MultiGeometry></Placemark>
</Document></kml>"""

OBST_KML = f"""<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark id="1"><name>UUWV100069 OTHER:COMMUNICATION_TOWER</name><Polygon><extrude>1</extrude><altitudeMode>relativeToGround</altitudeMode>
<outerBoundaryIs><LinearRing><coordinates>{LON + 0.02},{LAT + 0.012},77.0 {LON + 0.0203},{LAT + 0.012},77.0 {LON + 0.0203},{LAT + 0.0122},77.0 {LON + 0.02},{LAT + 0.012},77.0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark id="2"><name>                   -Line-0010</name><LineString><extrude>1</extrude><altitudeMode>relativeToGround</altitudeMode>
<coordinates>0.0, 0.0, 0.0</coordinates></LineString></Placemark>
</Document></kml>"""


@pytest.mark.parametrize("text,expected", [
    ("От земли до 900 м (3000 фут) AMSL", (None, 900.0, "AMSL")),
    ("От 800\xa0м (2700 фут) AMSL до FL90", (800.0, 2743.2, "AMSL")),
    ("От земли до 500 м (1700 фут) AGL", (None, 500.0, "AGL")),
    ("Oт FL050 до FL100", (1524.0, 3048.0, "AMSL")),
    ("На всех высотах", (None, None, "AGL")),
    ("По описанию военных", (None, None, "AGL")),
    ("Некорректная геометрия! От земли до 1400\xa0м (4600 фут) AMSL", (None, 1400.0, "AMSL")),
])
def test_zone_altitude_text(text, expected):
    lo, hi, ref = parse_altitudes(text)
    assert ref == expected[2]
    assert (lo is None) == (expected[0] is None) and (lo is None or lo == pytest.approx(expected[0], abs=0.5))
    assert (hi is None) == (expected[1] is None) and (hi is None or hi == pytest.approx(expected[1], abs=0.5))


def _scenario():
    parts = [parse_kml(AREA_KML, name="Границы полетов", partial=True), parse_kml(ZONES_KML, name="Московская зона", partial=True),
             parse_kml(OBST_KML, name="obstacles_Московская область", partial=True)]
    return parts, merge_scenarios(parts, name="Организаторы-2")


def test_organizer_layers_parse():
    parts, sc = _scenario()
    area, zones, obst = parts
    assert len(area.areas) == 2 and any("пропущено" in n for n in area.notes)  # вырожденный полигон пропущен
    assert {z.category for z in zones.nofly} == {"P", "TEMP", "R"}
    assert next(z for z in zones.nofly if z.name == "UUR2").time_unknown
    assert len(obst.obstacles) == 1 and obst.obstacles[0].height_m == 77.0  # пустая линия «0.0, 0.0, 0.0» пропущена
    assert all(z.name != "FAR" for z in sc.nofly)  # далекая зона отрезана по району работ
    assert not sc.sites


def test_plan_without_sites_and_high_temp_zone():
    """Площадок нет — расставляются автоматически; зона «от FL150» не блокирует съемку; запретная — вырезается."""
    _, sc = _scenario()
    p = MissionParams(fleet=[{"model": "geoscan_gemini", "count": 2}], start_time="2026-09-25T09:00:00+03:00",
                      search_effort="fast", use_terrain=False)
    r = plan(sc, p)
    assert r["sites"] and all(s["auto"] for s in r["sites"])
    assert any("выше коридора" in w and "UUR2" in w for w in r["warnings"])
    assert r["summary"]["coverage_allowed_pct"] > 95
    assert r["validation"]["passed"], [c for c in r["validation"]["checks"] if not c["passed"]]


def test_fleet_advisor_curve():
    _, sc = _scenario()
    p = MissionParams(fleet=[{"model": "geoscan_gemini", "count": 3}], start_time="2026-09-25T09:00:00+03:00",
                      search_effort="fast", use_terrain=False, deadline_h=100)
    a = advise(sc, p)
    assert [pt["n"] for pt in a["points"]] == [1, 2, 3]
    ms = [pt["makespan_h"] for pt in a["points"]]
    assert all(b <= a_ + 1e-9 for a_, b in zip(ms, ms[1:]))  # огибающая: больше доступных бортов — не дольше
    assert a["recommended"] is not None and a["text"]
