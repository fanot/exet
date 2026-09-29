"""Геометрия съемки: формулы GSD → высота, шаг, базис; ветер."""
import math

import pytest

from planner.catalog import load_catalog
from planner.sensors import compute_survey_geom


def geom(model, st="rgb", gsd=3.0, wind=0.0, **kw):
    cat = load_catalog()
    return compute_survey_geom(cat, cat.drones[model], st, gsd, None, None, wind, 700, **kw)


def test_gsd_altitude_formula():
    g, _ = geom("geoscan_gemini")
    # PF1B: p = 23.5/6000 мм, f = 20 мм → H = 0.03·20/0.0039167 = 153.2 м
    assert g.altitude_agl == pytest.approx(0.03 * 20 / (23.5 / 6000), rel=1e-6)
    assert g.swath_m == pytest.approx(23.5 * g.altitude_agl / 20)
    assert g.spacing() == pytest.approx(g.swath_m * 0.5)          # поперечное 50%
    assert g.photo_base() == pytest.approx(g.along_m * 0.3)       # продольное 70%


def test_min_altitude_violation_gives_recommendation():
    g, reasons = geom("geoscan_201", gsd=1.0)  # нужна высота ~78 м < 100 м для 201
    assert g is None and "Увеличьте GSD" in reasons[0]


def test_wind_limit_excludes_model():
    g, reasons = geom("geoscan_gemini", wind=9.0)  # на 150 м ≈ 13 м/с > 10 м/с
    assert g is None and "ветер" in reasons[0]


def test_plane_crab_reduces_spacing():
    g, _ = geom("geoscan_201", wind=6.0)
    assert g.spacing(g.crab_rad(8.0)) < g.spacing(0.0)


def test_copter_speed_depends_on_wind_direction():
    """Физическая модель ветра: путевая скорость по треугольнику скоростей."""
    g, _ = geom("geoscan_gemini", wind=5.0, wind_model="physical")
    w = g.wind_ms
    along = g.survey_speed(w, 0.0)    # галсы по ветру: против ветра путевая ≤ 15 − w
    across = g.survey_speed(0.0, w)   # поперек: ≤ √(15² − w²)
    assert along == pytest.approx(min(12.0, 15 - w, g.v_cap))
    assert across >= along


def test_qa_wind_model_constant_ground_speed():
    """Методика QA: автопилот держит путевую скорость — ветер не меняет скорости съемки и перелета, только порог по ТТХ."""
    calm, _ = geom("geoscan_gemini", wind=0.0)
    g, _ = geom("geoscan_gemini", wind=5.0)
    w = g.wind_ms
    assert g.survey_speed(w, 0.0) == g.survey_speed(0.0, w) == pytest.approx(calm.speed_ms)
    assert g.transit_speed(-w, 0.0) == g.transit_speed(w, 0.0) == pytest.approx(calm.transit_speed_ms)
    g2, reasons = geom("geoscan_gemini", wind=9.0)
    assert g2 is None and "ветер" in reasons[0]  # порог по ТТХ действует и в этом режиме


def test_payload_compatibility():
    g, reasons = geom("geoscan_201", st="ir", gsd=15)
    assert g is None and "нет полезной нагрузки" in reasons[0]
    g, _ = geom("geoscan_801", st="ir", gsd=15)
    assert g is not None and g.kind == "camera"
