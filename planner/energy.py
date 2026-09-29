"""Модель расхода АКБ.

Два режима (MissionParams.energy_model):
  * "linear"     — расход пропорционален времени полета (согласовано на QA-сессии; по умолчанию);
  * "calibrated" — для мультикоптеров мощность зависит от режима полета:
        P(t) = P_hover · f(v_air, v_z, a_h) / f_hover,
    где f — безразмерная кривая мощности, обученная градиентным бустингом на открытой телеметрии
    (DJI Matrice 100, 209 полетов, CMU / figshare 10.1184/R1/12683453, CC BY 4.0; см. scripts/train_energy.py),
    f_hover = f(0, 0, 0). Паспортное время полета T_max трактуется как время висения, поэтому доля
    израсходованного заряда = Σ f/f_hover · dt / T_max; при чистом висении режимы совпадают.
    Самолет (Геоскан 201): P(t) = P_level · f(v_z, крен) / f_level по телеметрии IDF_DS
    (SpeedyBee/INAV, 102 круга, Zenodo 10.5281/zenodo.16992975, CC BY 4.0; scripts/train_energy_fw.py);
    паспортные 180 мин трактуются как горизонтальный крейсерский полет. Зависимость от воздушной скорости
    в данных нефизична (артефакт оценки ветра автопилотом), поэтому скорость фиксируется крейсерской —
    модель учитывает только надбавки за набор/снижение и крен (развороты, спирали).

В рантайме нет ML-зависимостей: таблица f на сетке (energy_model.json) интерполируется numpy.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

TABLE_PATH = Path(__file__).with_name("energy_model.json")
TABLE_FW_PATH = Path(__file__).with_name("energy_model_fw.json")


class PowerTable:
    def __init__(self, data: dict, axes=("v_air", "v_z", "a_h"), ref=(0.0, 0.0, 0.0)):
        self.axes = [np.asarray(data["axes"][k], dtype=float) for k in axes]
        self.f = np.asarray(data["f"], dtype=float)
        self.meta = {k: v for k, v in data.items() if k not in ("f", "axes")}
        self.f_hover = float(self(*ref)[0])  # опорный режим: висение (коптер) / горизонтальный полет (самолет)

    def __call__(self, v_air, v_z, a_h) -> np.ndarray:
        """Трилинейная интерполяция с прижатием к границам сетки."""
        pts = [np.atleast_1d(np.asarray(x, dtype=float)) for x in (v_air, v_z, a_h)]
        n = max(len(p) for p in pts)
        pts = [np.broadcast_to(p, (n,)) for p in pts]
        idx, w = [], []
        for ax, p in zip(self.axes, pts):
            p = np.clip(p, ax[0], ax[-1])
            i = np.clip(np.searchsorted(ax, p) - 1, 0, len(ax) - 2)
            t = (p - ax[i]) / (ax[i + 1] - ax[i])
            idx.append(i)
            w.append(t)
        out = np.zeros(n)
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    ww = (w[0] if dx else 1 - w[0]) * (w[1] if dy else 1 - w[1]) * (w[2] if dz else 1 - w[2])
                    out += ww * self.f[idx[0] + dx, idx[1] + dy, idx[2] + dz]
        return out


@lru_cache(maxsize=1)
def load_table() -> PowerTable | None:
    if not TABLE_PATH.exists():
        return None
    return PowerTable(json.loads(TABLE_PATH.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def load_table_fw() -> PowerTable | None:
    if not TABLE_FW_PATH.exists():
        return None
    return PowerTable(json.loads(TABLE_FW_PATH.read_text(encoding="utf-8")), axes=("v_ratio", "v_z", "bank"), ref=(1.0, 0.0, 0.0))


def table_info() -> dict | None:
    t, fw = load_table(), load_table_fw()
    if t is None:
        return None
    out = {"source": t.meta.get("source"), "validation": t.meta.get("validation"), "v_air_clamp": t.meta.get("v_air_clamp")}
    if fw is not None:
        out["fixed_wing"] = {"source": fw.meta.get("source"), "validation": fw.meta.get("validation")}
    return out


def energy_equiv_s(P, T, phases, wind_xy, is_plane: bool, mode: str, accel: float = 2.0) -> float:
    """Энергия вылета в «секундах висения» (эквивалент времени для линейного бюджета АКБ).

    P — точки (x, y, z) в метрах, T — время, с; phases — этап каждой точки; wind_xy — вектор ветра, м/с.
    """
    T = np.asarray(T, dtype=float)
    dur = float(T[-1] - T[0]) if len(T) > 1 else 0.0
    table = load_table_fw() if is_plane else load_table()
    if mode != "calibrated" or table is None or len(T) < 2:
        return dur
    P = np.asarray(P, dtype=float)
    dt = np.diff(T)
    ok = dt > 1e-6
    d = np.diff(P, axis=0)
    if is_plane:
        return _plane_energy(table, d, dt, ok, np.asarray(phases[1:]))
    vx = np.where(ok, d[:, 0] / np.where(ok, dt, 1), 0.0)
    vy = np.where(ok, d[:, 1] / np.where(ok, dt, 1), 0.0)
    vz = np.where(ok, d[:, 2] / np.where(ok, dt, 1), 0.0)
    wx, wy = wind_xy
    v_air = np.hypot(vx - wx, vy - wy)
    dh = np.hypot(d[:, 0], d[:, 1])
    ph = np.asarray(phases[1:])
    # удержание в точке на съемке — торможение/разгон на развороте галса
    hold = ok & (dh < 0.5) & (np.abs(d[:, 2]) < 0.5) & (ph == "survey")
    a_h = np.where(hold, accel, 0.0)
    f = table(v_air, vz, a_h) / table.f_hover
    f = np.where(ph == "ground", 0.0, f)  # на земле моторы не работают
    return float(np.sum(f * np.where(ok, dt, 0.0)))


def _plane_energy(table: PowerTable, d, dt, ok, ph) -> float:
    """Самолет: надбавки за набор/снижение и крен; крен — из скорости разворота траектории tg(γ) = v·ω/g."""
    dts = np.where(ok, dt, 1.0)
    dh = np.hypot(d[:, 0], d[:, 1])
    v = dh / dts
    vz = d[:, 2] / dts
    hdg = np.arctan2(d[:, 1], d[:, 0])
    dpsi = np.zeros_like(hdg)
    dpsi[1:] = (np.diff(hdg) + np.pi) % (2 * np.pi) - np.pi
    omega = np.abs(dpsi) / dts
    bank = np.degrees(np.arctan(v * omega / 9.81))
    bank = np.where(dh > 1.0, np.clip(bank, 0, 45), 0.0)
    f = table(np.ones_like(v), vz, bank) / table.f_hover
    # на земле (катапульта) и под парашютом (вертикальное снижение без хода) мотор не работает
    motor_off = (ph == "ground") | ((ph == "landing") & (dh < 1.0) & (d[:, 2] < 0))
    f = np.where(motor_off, 0.0, f)
    return float(np.sum(f * np.where(ok, dt, 0.0)))
