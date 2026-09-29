"""Модель расхода АКБ: таблица, физическая правдоподобность, интеграция с планом."""
import copy

import numpy as np

from planner.energy import energy_equiv_s, load_table


def test_table_loaded_and_validated():
    t = load_table()
    assert t is not None
    v = t.meta["validation"]
    # калиброванная модель на отложенных полетах точнее линейной
    assert v["mape_calibrated_pct"] < v["mape_linear_pct"]


def test_physics_monotonic():
    t = load_table()
    assert t(0, 3, 0)[0] > t(0, 0, 0)[0] > t(0, -3, 0)[0]   # набор дороже висения, снижение дешевле
    assert t(3, 0, 3)[0] >= t(3, 0, 0)[0]                  # разгон не дешевле равномерного полета
    assert abs(t(15, 0, 0)[0] - t(6.5, 0, 0)[0]) < 1e-9    # за границей данных — без экстраполяции


def test_linear_equals_duration_and_hover_equivalence():
    P = np.array([[0, 0, 100], [0, 0, 100]])
    T = np.array([0.0, 600.0])
    ph = ["survey", "survey"]
    assert energy_equiv_s(P, T, ph, (0, 0), False, "linear") == 600.0
    # висение в калиброванной модели = линейной (паспортное время = время висения)
    e = energy_equiv_s(P, T, ["takeoff", "takeoff"], (0, 0), False, "calibrated")
    assert abs(e - 600.0) < 1.0
    # набор высоты 5 м/с дороже
    P2 = np.array([[0, 0, 0], [0, 0, 500]])
    T2 = np.array([0.0, 100.0])
    assert energy_equiv_s(P2, T2, ["takeoff", "takeoff"], (0, 0), False, "calibrated") > 105.0
    # самолет: горизонтальный крейсер = линейная, набор высоты и крен — дороже
    Pl = np.array([[0, 0, 200], [2200, 0, 200]])
    Tl = np.array([0.0, 100.0])
    assert abs(energy_equiv_s(Pl, Tl, ["transit_out"] * 2, (0, 0), True, "calibrated") - 100.0) < 1.0
    Pc = np.array([[0, 0, 200], [2200, 0, 500]])
    assert energy_equiv_s(Pc, Tl, ["transit_out"] * 2, (0, 0), True, "calibrated") > 110.0
    ang = np.linspace(0, 2 * np.pi, 37)
    Ps = np.column_stack([85 * np.cos(ang), 85 * np.sin(ang), np.full(37, 200.0)])
    Ts = np.linspace(0, 2 * np.pi * 85 / 22, 37)
    assert energy_equiv_s(Ps, Ts, ["survey"] * 37, (0, 0), True, "calibrated") > Ts[-1] * 1.03


def test_plan_with_calibrated_energy_valid(toksovo):
    from planner.geo import parse_geojson
    from planner.models import MissionParams
    from planner.optimizer import plan
    p = MissionParams(fleet=[{"model": "geoscan_gemini", "count": 3}], start_time="2026-09-25T09:00:00+03:00",
                      wind_speed_ms=4, wind_dir_deg=250, energy_model="calibrated", search_effort="fast", use_terrain=False)
    r = plan(parse_geojson(copy.deepcopy(toksovo)), p)
    assert r["summary"]["energy_model"] == "calibrated"
    assert r["validation"]["passed"], [c for c in r["validation"]["checks"] if not c["passed"]]
    assert max(s["battery_used_pct"] for s in r["sorties"]) <= 80.5
