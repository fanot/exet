"""Геометрия: локальная метрическая проекция и чтение сценария из GeoJSON/KML.

Все входные и выходные координаты — WGS-84 (EPSG:4326). Внутри расчета используется
азимутальная равнопромежуточная проекция с центром в сценарии: на 100 км² искажение
расстояний < 0.01%, пересчет обратно в WGS-84 выполняется при экспорте.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, shape
from shapely.validation import make_valid


class ScenarioError(ValueError):
    """Ошибка входных данных, понятная пользователю."""


class LocalFrame:
    def __init__(self, lon0: float, lat0: float):
        self.lon0, self.lat0 = lon0, lat0
        crs = CRS.from_proj4(f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs")
        self._fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        self._inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    def to_xy(self, lon, lat):
        return self._fwd.transform(lon, lat)

    def to_ll(self, x, y):
        return self._inv.transform(x, y)

    def geom_to_xy(self, geom):
        return shapely.transform(geom, lambda c: np.column_stack(self._fwd.transform(c[:, 0], c[:, 1])))

    def geom_to_ll(self, geom):
        return shapely.transform(geom, lambda c: np.column_stack(self._inv.transform(c[:, 0], c[:, 1])))


@dataclass
class NoFlyZone:
    name: str
    geom: object  # WGS-84
    alt_min_m: Optional[float] = None
    alt_max_m: Optional[float] = None
    alt_ref: str = "AGL"
    active_from: Optional[datetime] = None
    active_to: Optional[datetime] = None
    category: str = "P"           # P — запретная, R — ограничение (постоянное), TEMP — временное ограничение, SVO
    note: str = ""                # исходный текст высот/исключений из файла зон
    time_unknown: bool = False    # временное ограничение без времени действия в данных

    def active_during(self, t0: datetime, t1: datetime) -> bool:
        if self.active_from and self.active_from >= t1:
            return False
        if self.active_to and self.active_to <= t0:
            return False
        return True


@dataclass
class Obstacle:
    name: str
    geom: object  # WGS-84, уже с учетом радиуса для точек
    height_m: float  # высота верха: над землей (alt_ref=AGL) или над уровнем моря (alt_ref=AMSL)
    radius_m: float = 0.0  # для точечных/линейных объектов (опора, ЛЭП)
    alt_ref: str = "AGL"   # AGL — высота объекта; AMSL — абсолютная отметка верха (KML altitudeMode=absolute, eTOD)


@dataclass
class Site:
    name: str
    lon: float
    lat: float
    reserve: bool = False


@dataclass
class Scenario:
    name: str = "scenario"
    areas: List[tuple] = field(default_factory=list)  # (name, geom)
    nofly: List[NoFlyZone] = field(default_factory=list)
    obstacles: List[Obstacle] = field(default_factory=list)
    airspace: Optional[object] = None
    airspace_ceiling_m: Optional[float] = None
    sites: List[Site] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # что сделано при разборе (пропущенные объекты, допущения)

    @property
    def main_sites(self):
        return [s for s in self.sites if not s.reserve]

    @property
    def reserve_sites(self):
        return [s for s in self.sites if s.reserve]

    def work_bounds(self):
        """Район работ без зон и препятствий: области, площадки, разрешенное пространство."""
        geoms = [g for _, g in self.areas] + [Point(s.lon, s.lat) for s in self.sites]
        if self.airspace is not None:
            geoms.append(self.airspace)
        return shapely.GeometryCollection(geoms).bounds if geoms else None

    def crop_to_work(self, margin_deg: float = 0.3):
        """Файлы зон и препятствий организаторов покрывают регионы целиком (Московская зона — несколько областей,
        препятствия — весь Приморский край): оставляем то, что рядом с районом работ, зоны обрезаем по рамке."""
        wb = self.work_bounds()
        if wb is None or not self.areas:
            return
        minx, miny, maxx, maxy = wb
        bb = shapely.box(minx - margin_deg, miny - margin_deg, maxx + margin_deg, maxy + margin_deg)
        n0, kept = len(self.nofly), []
        for z in self.nofly:
            if not z.geom.intersects(bb):
                continue
            if not bb.contains(z.geom):
                g = z.geom.intersection(bb)
                if g.is_empty or g.area <= 0:
                    continue
                z.geom = _clean_polygonal(g, f"Зона «{z.name}»")
            kept.append(z)
        self.nofly = kept
        if n0 > len(kept):
            self.notes.append(f"Зоны: в районе работ {len(kept)} из {n0} (остальные далеко — не влияют)")
        self.crop_obstacles(margin_deg * 0.85)

    def bounds(self):
        """Район работ: области, зоны, площадки. Препятствия не расширяют район — файл препятствий
        может покрывать целый регион (пример организаторов — весь Приморский край)."""
        geoms = [g for _, g in self.areas] + [z.geom for z in self.nofly]
        geoms += [Point(s.lon, s.lat) for s in self.sites]
        if self.airspace is not None:
            geoms.append(self.airspace)
        return shapely.GeometryCollection(geoms).bounds

    def crop_obstacles(self, margin_deg: float = 0.25):
        """Оставить препятствия в районе работ (+запас ~20–25 км на перелеты)."""
        if not self.areas and not self.sites:
            return
        minx, miny, maxx, maxy = self.bounds()
        bb = shapely.box(minx - margin_deg, miny - margin_deg, maxx + margin_deg, maxy + margin_deg)
        self.obstacles = [o for o in self.obstacles if o.geom.intersects(bb)]

    def centroid(self):
        u = shapely.union_all([g for _, g in self.areas])
        c = u.centroid
        return c.x, c.y


KIND_ALIASES = {
    "survey_area": "survey_area", "area": "survey_area", "survey": "survey_area", "съемка": "survey_area",
    "no_fly": "no_fly", "nofly": "no_fly", "restricted": "no_fly", "запрет": "no_fly",
    # типы из файла зон организаторов (Московская зона.kml, атрибут Type)
    "запретная_зона": "no_fly", "пост_ограничение": "no_fly", "врем_ограничение": "no_fly", "svo": "no_fly",
    "prohibited": "no_fly", "restriction": "no_fly", "temporary": "no_fly",
    "obstacle": "obstacle", "препятствие": "obstacle",
    "airspace": "airspace", "allowed_airspace": "airspace",
    "site": "site", "base": "site", "takeoff": "site", "площадка": "site",
    "reserve_site": "reserve_site", "reserve": "reserve_site", "резерв": "reserve_site",
}

KML_KEYWORDS = [
    ("reserve_site", ("резерв", "reserve")),
    ("no_fly", ("запрет", "бесполет", "no_fly", "nofly", "no-fly", "restricted")),
    ("obstacle", ("препят", "obstacle", "высотн")),
    ("airspace", ("airspace", "воздушн", "разрешен")),
    ("site", ("площад", "site", "base", "старт", "взлет", "takeoff")),
    ("survey_area", ("съем", "survey", "area", "полигон", "участ")),
]


def _parse_dt(v) -> Optional[datetime]:
    if v in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone(timedelta(hours=3)))
    except ValueError:
        raise ScenarioError(f"Некорректная дата/время: {v!r} (ожидается ISO 8601)")


def _num(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        out = float(v)
        if not math.isfinite(out):
            raise ValueError("non-finite number")
        return out
    except (TypeError, ValueError):
        raise ScenarioError(f"Ожидалось число, получено {v!r}")


ZONE_CATEGORY = {"запретная_зона": "P", "prohibited": "P", "пост_ограничение": "R", "restriction": "R",
                 "врем_ограничение": "TEMP", "temporary": "TEMP", "svo": "SVO"}
FT = 0.3048


def _alt_value(tok: str):
    """«земли»/GND → (None, 'AGL'); «FL90» → (метры, 'AMSL'); «900 м (3000 фут) AMSL» → (900, 'AMSL'); UNL → (None, None)."""
    t = tok.strip().lower()
    if not t or "земл" in t or t.startswith("gnd") or t.startswith("поверхн"):
        return None, "AGL"
    if "unl" in t or "без огранич" in t:
        return None, None
    m = re.search(r"fl\s*(\d+)", t)
    if m:
        return float(m.group(1)) * 100 * FT, "AMSL"   # эшелон — давление 1013 гПа ≈ высота над уровнем моря
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(м|m)(?![a-zа-я])", t) or re.search(r"(\d+(?:[.,]\d+)?)", t)
    if not m:
        return None, None
    v = float(m.group(1).replace(",", "."))
    if re.search(r"(фут|ft|feet)\b", t) and not re.search(r"\d\s*(м|m)(?![a-zа-я])", t):
        v *= FT
    ref = "AGL" if re.search(r"\bagl\b|над земл|\bafl\b", t) else "AMSL"
    return v, ref


def parse_altitudes(text) -> tuple:
    """Диапазон высот зоны из текста (формат аэронавигационных сборников, пример — Московская зона.kml):
    «От земли до 900 м (3000 фут) AMSL», «От 800 м (2700 фут) AMSL до FL90», «От FL280 до FL400», «На всех высотах».
    Возвращает (alt_min_m | None — от земли, alt_max_m | None — без верхней границы, alt_ref)."""
    t = str(text or "").replace("\xa0", " ").replace("фyт", "фут")
    t = re.sub(r"(?i)некорректная геометрия!?", "", t).split("\n")[0].strip()
    low = t.lower().replace("oт", "от")  # латинская «o» в исходных данных
    if not low or "всех высот" in low or "gnd-unl" in low:
        return None, None, "AGL"
    m = re.search(r"от\s+(.*?)\s+до\s+(.*)$", low)
    if not m:
        if low.startswith("до "):
            hi, ref = _alt_value(low[3:])
            return None, hi, ref or "AGL"
        return None, None, "AGL"   # «SVO», «По описанию военных» — считаем все высоты (консервативно)
    lo, lo_ref = _alt_value(m.group(1))
    hi, hi_ref = _alt_value(m.group(2))
    if lo is None:      # от земли: одна система отсчета — у верхней границы
        return None, hi, hi_ref or "AGL"
    if hi_ref and lo_ref != hi_ref:
        # смешанные отсчеты (редко): приводим к AMSL у нижней границы нельзя без ЦМР — берем консервативно от земли
        return None, hi, hi_ref
    return lo, hi, lo_ref or "AMSL"


def _alt_ref(props) -> str:
    v = str(props.get("alt_ref") or props.get("altitudeMode") or "AGL").strip()
    if v.upper() in ("AMSL", "ABSOLUTE", "MSL"):
        return "AMSL"
    if v.upper() in ("AGL", "RELATIVETOGROUND", "CLAMPTOGROUND"):
        return "AGL"
    raise ScenarioError(f"Неизвестная система отсчета высоты: {v!r}; ожидается AGL или AMSL")


def _clean_polygonal(geom, what: str):
    if geom.is_empty:
        return None
    if not geom.is_valid:
        geom = make_valid(geom)
    polys = [g for g in getattr(geom, "geoms", [geom]) if isinstance(g, (Polygon, MultiPolygon))]
    if isinstance(geom, (Polygon, MultiPolygon)):
        polys = [geom]
    polys = [g for g in polys if g.area > 0]
    if not polys:
        if geom.geom_type in ("LineString", "MultiLineString", "Point", "MultiPoint", "GeometryCollection", "LinearRing"):
            return None  # вырожденный контур нулевой площади (после make_valid) — пропускается
        raise ScenarioError(f"{what}: ожидается Polygon/MultiPolygon")
    return shapely.union_all(polys)


def _add_feature(sc: Scenario, kind: str, geom, props: dict):
    if geom.is_empty:
        raise ScenarioError(f"{kind}: геометрия пуста")
    if kind in ("no_fly", "airspace") and geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ScenarioError(f"{kind}: ожидается Polygon/MultiPolygon")
    coords = shapely.get_coordinates(geom)
    if len(coords) and (not np.isfinite(coords).all() or np.any(np.abs(coords[:, 0]) > 180)
                        or np.any(np.abs(coords[:, 1]) > 90)):
        raise ScenarioError("Координаты вне диапазона WGS-84 — проверьте порядок [lon, lat]")
    name = str(props.get("name") or props.get("title") or kind)
    if kind == "survey_area":
        g = _clean_polygonal(geom, f"Область съемки «{name}»")
        if g is not None:
            sc.areas.append((name, g))
    elif kind == "no_fly":
        g = _clean_polygonal(geom, f"Бесполетная зона «{name}»")
        if g is None:
            raise ScenarioError(f"Бесполетная зона «{name}»: контур имеет нулевую площадь")
        if g is not None:
            lo, hi, ref = _num(props.get("alt_min_m")), _num(props.get("alt_max_m")), _alt_ref(props)
            alt_text = props.get("Altitudes") or props.get("altitudes") or props.get("Высоты") or props.get("высоты")
            if alt_text and lo is None and hi is None and props.get("alt_ref") is None:
                lo, hi, ref = parse_altitudes(alt_text)
            ztype = str(props.get("Type") or props.get("zone_type") or props.get("type") or "").strip().lower()
            cat = str(props.get("category") or ZONE_CATEGORY.get(ztype, "P")).upper()
            a_from, a_to = _parse_dt(props.get("active_from")), _parse_dt(props.get("active_to"))
            if lo is not None and hi is not None and lo > hi:
                raise ScenarioError(f"Зона «{name}»: alt_min_m больше alt_max_m")
            if a_from is not None and a_to is not None and a_from >= a_to:
                raise ScenarioError(f"Зона «{name}»: active_from должен предшествовать active_to")
            sc.nofly.append(NoFlyZone(
                name=name, geom=g, alt_min_m=lo, alt_max_m=hi, alt_ref=ref, active_from=a_from, active_to=a_to,
                category=cat, note=str(alt_text or props.get("note") or "").replace("\xa0", " ").strip(),
                time_unknown=(cat == "TEMP" and a_from is None and a_to is None),
            ))
    elif kind == "obstacle":
        h = _num(props.get("height_m", props.get("height")))
        if h is None:
            raise ScenarioError(f"Высотное препятствие «{name}»: не задана height_m")
        radius = _num(props.get("radius_m")) or 10.0
        if radius < 0 or (h < 0 and _alt_ref(props) == "AGL"):
            raise ScenarioError(f"Препятствие «{name}»: высота AGL и радиус не могут быть отрицательными")
        if geom.geom_type in ("Point", "LineString", "MultiLineString", "MultiPoint"):
            # буфер в градусах приблизительный — точный буфер строится в метрах в LocalFrame
            sc.obstacles.append(Obstacle(name=name, geom=geom, height_m=h, radius_m=radius, alt_ref=_alt_ref(props)))
        else:
            g = _clean_polygonal(geom, f"Препятствие «{name}»")
            if g is not None:
                sc.obstacles.append(Obstacle(name=name, geom=g, height_m=h, alt_ref=_alt_ref(props)))
    elif kind == "airspace":
        g = _clean_polygonal(geom, "Разрешенное воздушное пространство")
        if g is None:
            raise ScenarioError("Разрешенное воздушное пространство: контур имеет нулевую площадь")
        sc.airspace = g if sc.airspace is None else sc.airspace.union(g)
        c = _num(props.get("ceiling_m"))
        if c is not None:
            if c <= 0:
                raise ScenarioError("Потолок разрешенного пространства должен быть положительным")
            sc.airspace_ceiling_m = min(sc.airspace_ceiling_m, c) if sc.airspace_ceiling_m is not None else c
    elif kind in ("site", "reserve_site"):
        if geom.geom_type != "Point":
            geom = geom.centroid
        if any(s.name == name for s in sc.sites):
            raise ScenarioError(f"Имя площадки «{name}» повторяется — задайте уникальные имена")
        sc.sites.append(Site(name=name, lon=geom.x, lat=geom.y, reserve=(kind == "reserve_site")))


def _validate(sc: Scenario):
    if not sc.areas:
        raise ScenarioError("В сценарии нет области съемки (kind=survey_area)")
    sc.crop_to_work()
    minx, miny, maxx, maxy = sc.work_bounds()
    if not (-180 <= minx <= 180 and -90 <= miny <= 90 and -180 <= maxx <= 180 and -90 <= maxy <= 90):
        raise ScenarioError("Координаты вне диапазона WGS-84 — проверьте порядок [lon, lat]")
    if maxx - minx > 3 or maxy - miny > 3:
        raise ScenarioError("Сценарий шире 3° — слишком большая территория для одного плана")
    if len(sc.nofly) > 3000 or len(sc.obstacles) > 60000:
        raise ScenarioError("Слишком много объектов в сценарии")


def parse_geojson(data: dict, name: str = "scenario", partial: bool = False) -> Scenario:
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise ScenarioError("GeoJSON должен быть FeatureCollection")
    if not isinstance(data.get("features"), list):
        raise ScenarioError("GeoJSON features должен быть массивом")
    sc = Scenario(name=data.get("name") or name)
    for i, f in enumerate(data.get("features", [])):
        if not isinstance(f, dict) or not isinstance(f.get("properties") or {}, dict):
            raise ScenarioError(f"Объект #{i}: ожидается Feature с объектом properties")
        props = f.get("properties") or {}
        raw_kind = str(props.get("kind") or props.get("layer") or props.get("type") or "").strip().lower()
        kind = KIND_ALIASES.get(raw_kind)
        if kind is None:
            raise ScenarioError(f"Объект #{i}: неизвестный kind={raw_kind!r}. Допустимо: survey_area, no_fly, obstacle, airspace, site, reserve_site")
        try:
            geom = shape(f["geometry"])
        except Exception as e:  # noqa: BLE001
            raise ScenarioError(f"Объект #{i}: некорректная геометрия ({e})")
        geom = shapely.force_2d(geom)
        _add_feature(sc, kind, geom, props)
    if not partial:
        _validate(sc)
    return sc


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _kml_coords(text: str, with_z: bool = False):
    """Координаты KML «lon,lat[,alt] …». Терпимо к пробелам после запятых («0.0, 0.0, 0.0» в примере организаторов)
    и к мусору: нечисловые кортежи пропускаются."""
    pts = []
    for tok in re.sub(r"\s*,\s*", ",", (text or "").strip()).split():
        parts = tok.split(",")
        try:
            x, y = float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            continue
        try:
            z = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
        except ValueError:
            z = 0.0
        pts.append((x, y, z) if with_z else (x, y))
    return pts


def _kml_max_z(pm) -> Optional[float]:
    zs = [p[2] for c in pm.iter("coordinates") for p in _kml_coords(c.text, with_z=True)]
    return max(zs) if zs and max(zs) > 0 else None


# коды типов препятствий eTOD / ICAO (пример организаторов: «UHHH100910 OTHER:COMMUNICATION_TOWER»)
ETOD_OBSTACLE_WORDS = ("building", "tower", "pylon", "chimney", "antenna", "mast", "vegetation", "tree", "fence",
                       "navaid", "crane", "light_support", "lightning", "pole", "windmill", "wind_turbine", "stack",
                       "other:", "sign", "structure", "cooling", "tank", "bridge", "catenary", "cable")


def _file_hint(name: str) -> Optional[str]:
    n = (name or "").lower()
    # «Границы полетов.kml» у организаторов — задание на съемку (описание файлов); «Московская зона.kml» — зоны ограничений
    for k, words in (("obstacle", ("препят", "obstacle", "etod")),
                     ("survey_area", ("границы полет", "задани", "съем", "survey", "участ")),
                     ("no_fly", ("запрет", "бесполет", "nofly", "no-fly", "restricted", "зона", "зоны", "ограничен")),
                     ("reserve_site", ("резерв",)), ("site", ("площадк", "sites")), ("airspace", ("airspace", "пространств")),
                     ("survey_area", ("област",))):
        if any(w in n for w in words):
            return k
    return None


def _real(pts) -> bool:
    """Отсекает заглушки «0,0» (в примере организаторов 120 пустых линий «0.0, 0.0, 0.0»)."""
    return any(abs(x) > 1e-9 or abs(y) > 1e-9 for x, y in pts)


def _kml_geoms(pm):
    out = []
    for poly in pm.iter("Polygon"):
        outer = poly.find("outerBoundaryIs/LinearRing/coordinates")
        ring = _kml_coords(outer.text if outer is not None else "")
        if len(set(ring)) < 3 or not _real(ring):
            continue
        holes = [h for h in (_kml_coords(x.text) for x in poly.findall("innerBoundaryIs/LinearRing/coordinates")) if len(set(h)) >= 3]
        out.append(Polygon(ring, holes))
    for ls in pm.iter("LineString"):
        c = _kml_coords(ls.findtext("coordinates") or "")
        if len(set(c)) >= 2 and _real(c):
            out.append(LineString(c))
    for p in pm.iter("Point"):
        c = _kml_coords(p.findtext("coordinates") or "")
        if c and _real(c[:1]):
            out.append(Point(c[0]))
    return out


def parse_kml(text: str, name: str = "scenario", partial: bool = False) -> Scenario:
    try:
        root = _strip_ns(ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text))
    except ET.ParseError as e:
        raise ScenarioError(f"Некорректный KML: {e}")
    sc = Scenario(name=name)
    hint = _file_hint(name)

    items = []  # (placemark, props, kind | None)

    def walk(node, folder_names):
        for child in node:
            if child.tag in ("Folder", "Document"):
                nm = child.findtext("name") or ""
                walk(child, folder_names + [nm])
            elif child.tag == "Placemark":
                props = {"name": (child.findtext("name") or "").strip() or child.get("id") or "объект"}
                for d in child.iter("Data"):
                    props[d.get("name")] = d.findtext("value")
                for d in child.iter("SimpleData"):
                    props[d.get("name")] = d.text
                kind = KIND_ALIASES.get(str(props.get("kind", "")).lower())
                if kind is None and (str(props.get("Type", "")).strip().lower() in ZONE_CATEGORY or props.get("Altitudes")):
                    kind = "no_fly"  # атрибуты зоны ограничений: Type, Altitudes
                kind = kind or hint
                if kind is None and any(w in (props["name"] or "").lower() for w in ETOD_OBSTACLE_WORDS):
                    kind = "obstacle"
                if kind is None:
                    hay = " ".join([*(folder_names), props.get("name") or "", child.findtext("description") or ""]).lower()
                    for k, words in KML_KEYWORDS:
                        if any(w in hay for w in words):
                            kind = k
                            break
                items.append((child, props, kind))

    walk(root, [])
    # вывод по документу: в файле, где большинство объектов — препятствия, безымянный объект с высотой в Z
    # тоже препятствие (в примере организаторов 71 препятствие без имени)
    resolved = [k for _, _, k in items if k]
    obstacle_doc = bool(resolved) and resolved.count("obstacle") >= 0.5 * len(resolved)
    # файл из одних безымянных полигонов без признаков слоя — задание на съемку (так выглядит пример организаторов)
    area_doc = not resolved and items and all(any(True for _ in c.iter("Polygon")) for c, _, _ in items)
    if area_doc:
        sc.notes.append(f"«{name}»: тип слоя не указан — {len(items)} полигонов приняты как области съемки")
    skipped = 0
    for child, props, kind in items:
        if kind is None and obstacle_doc and _kml_max_z(child) is not None:
            kind = "obstacle"
        if kind is None and area_doc:
            kind = "survey_area"
        if not _kml_geoms(child):
            skipped += 1  # пустая/вырожденная геометрия — пропускаем, а не отвергаем файл
            continue
        if kind is None:
            raise ScenarioError(f"KML: не удалось определить тип объекта «{props.get('name')}». Добавьте ExtendedData kind "
                                f"или назовите файл/папку по слою («область съемки», «запретные зоны», «высотные препятствия», «площадки»)")
        geoms = _kml_geoms(child)
        if kind in ("no_fly", "airspace") and any(g.geom_type not in ("Polygon", "MultiPolygon") for g in geoms):
            raise ScenarioError(f"KML: «{props['name']}»: ожидается Polygon/MultiPolygon")
        if kind == "obstacle":
            if props.get("height_m") in (None, "") and props.get("height") in (None, ""):
                z = _kml_max_z(child)
                if z is None:
                    raise ScenarioError(f"Препятствие «{props['name']}»: нет высоты (ExtendedData height_m или Z в координатах)")
                props["height_m"] = z
            props.setdefault("altitudeMode", child.findtext(".//altitudeMode") or "relativeToGround")
        if kind in ("survey_area", "no_fly", "airspace") and len(geoms) > 1:
            geoms = [shapely.union_all([make_valid(g) for g in geoms if g.geom_type == "Polygon"])]
        for g in geoms:
            _add_feature(sc, kind, g, props)
    if skipped:
        sc.notes.append(f"«{name}»: пропущено объектов без геометрии или с вырожденными координатами — {skipped}")

    if not partial:
        _validate(sc)
    return sc


def merge_scenarios(parts: List["Scenario"], name: str = "scenario", partial: bool = False) -> Scenario:
    """Сценарий из нескольких слоев (область, зоны, препятствия, площадки — отдельными файлами)."""
    sc = Scenario(name=name)
    for p in parts:
        sc.notes += p.notes
        sc.areas += p.areas
        sc.nofly += p.nofly
        sc.obstacles += p.obstacles
        sc.sites += p.sites
        if p.airspace is not None:
            sc.airspace = p.airspace if sc.airspace is None else sc.airspace.union(p.airspace)
            ceilings = [c for c in (p.airspace_ceiling_m, sc.airspace_ceiling_m) if c is not None]
            sc.airspace_ceiling_m = min(ceilings) if ceilings else None
    names = [s.name for s in sc.sites]
    if len(names) != len(set(names)):
        raise ScenarioError("Имена площадок в загружаемых слоях повторяются — задайте уникальные имена")
    if not partial:
        _validate(sc)
    return sc


def parse_scenario_bytes(content: bytes, filename: str, partial: bool = False) -> Scenario:
    import json

    fn = filename.lower()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ScenarioError("Файл должен быть текстом в кодировке UTF-8")
    if fn.endswith(".kml"):
        return parse_kml(text, name=filename.rsplit(".", 1)[0], partial=partial)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ScenarioError(f"Файл не является GeoJSON/KML: {e}")
    return parse_geojson(data, name=filename.rsplit(".", 1)[0], partial=partial)
