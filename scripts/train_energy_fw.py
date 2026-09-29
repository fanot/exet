"""Калибровка модели энергии самолета по открытой телеметрии (офлайн).

Данные: IDF_DS «Fixed-Wing UAS Telemetry Benchmark: 240 Flights for ML and Intelligent Trajectory
Optimization», Zenodo 10.5281/zenodo.16992975, CC BY 4.0. Используется часть SpeedyBee F405 / INAV:
110 кругов по маршрутным точкам, бортовой лог ~60 Гц: напряжение и ток АКБ, крен, скорость NEU,
оценка ветра автопилотом.

Перенос на Геоскан 201 — через безразмерные признаки и безразмерную цель:
    f = P / P_level,  P_level — мощность горизонтального прямолинейного полета на крейсерской скорости
    В ЭТОМ ЖЕ полете (круге): абсолютный уровень мощности сильно меняется от сессии к сессии (погода,
    АКБ, загрузка) и в планировщике берется из паспорта борта; модель отвечает только за надбавку за маневр;
    признаки: v_ratio = v_air / v_cruise, v_z (м/с), bank (градусы крена).
Паспортная продолжительность 201 (180 мин) трактуется как крейсерский режим, поэтому в планировщике
доля заряда = Σ f/f_level · dt / T_max.

Проверка: GroupKFold по блокам из 10 кругов (соседние круги одного полета не попадают в train и test
одновременно). Сравнение на уровне круга при известной базовой мощности: «база × время» (линейная модель)
против «база × Σ f(маневр)». Абсолютную мощность без базы ни одна из моделей не предсказывает
(ошибка ~22% у обеих) — поэтому база берется из ТТХ.
"""
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "telemetry" / "idf" / "Speedybee DataSet" / "processed" / "telemetry"
OUT = ROOT / "planner" / "energy_model_fw.json"
FEATS = ["v_ratio", "v_z", "bank"]
MONO = [0, 1, 1]  # набор высоты и крен не уменьшают мощность
COLS = ["time (us)", "vbat (V)", "amperage (A)", "attitude[0]", "navVel[0]", "navVel[1]", "navVel[2]", "wind[0]", "wind[1]", "navState"]


def load():
    frames = []
    skipped = 0
    for k, f in enumerate(sorted(glob.glob(str(DATA / "Lap*.csv")))):
        head = pd.read_csv(f, nrows=0).columns
        if not set(COLS) <= set(head):
            skipped += 1  # в части логов нет оценки ветра — без нее нельзя получить воздушную скорость
            continue
        d = pd.read_csv(f, usecols=COLS)
        d["t"] = (d["time (us)"] - d["time (us)"].iloc[0]) / 1e6
        d["P"] = d["vbat (V)"] * d["amperage (A)"]
        g = d.groupby(np.floor(d.t).astype(int)).agg(P=("P", "mean"), roll=("attitude[0]", "mean"),
                                                       vn=("navVel[0]", "mean"), ve=("navVel[1]", "mean"), vu=("navVel[2]", "mean"),
                                                       wn=("wind[0]", "mean"), we=("wind[1]", "mean"))
        g["v_air"] = np.hypot(g.vn - g.wn, g.ve - g.we) / 100.0
        g["v_z"] = g.vu / 100.0
        g["bank"] = np.abs(g.roll) / 10.0
        g["lap"] = k
        g["dt"] = 1.0
        frames.append(g[(g.v_air > 5) & (g.P > 20)])
    print(f"кругов без оценки ветра (пропущены): {skipped}")
    return pd.concat(frames, ignore_index=True)


def main():
    df = load()
    lvl = df[(df.v_z.abs() < 0.3) & (df.bank < 8)]
    base = lvl.groupby("lap").P.median()
    vcr = lvl.groupby("lap").v_air.median()
    df = df[df.lap.isin(base.index)].copy()
    df["p_base"] = df.lap.map(base)
    df["v_ratio"] = df.v_air / df.lap.map(vcr)
    df["f"] = df.P / df.p_base
    v_cruise, p_level = float(vcr.median()), float(base.median())
    print(f"окон (1 с): {len(df)}, кругов: {df.lap.nunique()}, v_cruise ≈ {v_cruise:.1f} м/с, P_level ≈ {p_level:.0f} Вт")

    X, y = df[FEATS].values, df.f.values
    groups = (df.lap // 10).values
    model = lambda: HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=100,
                                                  l2_regularization=1.0, monotonic_cst=MONO, random_state=0)
    rows = []
    for k, (tr, te) in enumerate(GroupKFold(n_splits=5).split(X, y, groups)):
        m = model().fit(X[tr], y[tr])
        pred = m.predict(X[te])
        dtr, dte = df.iloc[tr], df.iloc[te].copy()
        dte["pred"] = pred
        for lap, g in dte.groupby("lap"):
            pb = float(g.p_base.iloc[0])
            rows.append((lap, float(g.P.sum()), pb * len(g), float((g.pred * pb).sum())))
        ss = ((y[te] - pred) ** 2).sum() / ((y[te] - y[te].mean()) ** 2).sum()
        print(f"  fold {k}: R² окон = {1 - ss:.3f}")
    r = pd.DataFrame(rows, columns=["lap", "e_true", "e_lin", "e_ml"])
    err = lambda c: abs(r[c] - r.e_true) / r.e_true * 100
    val = {"laps": int(len(r)), "mape_linear_pct": round(float(err("e_lin").mean()), 1), "mape_calibrated_pct": round(float(err("e_ml").mean()), 1),
           "p90_linear_pct": round(float(np.percentile(err("e_lin"), 90)), 1), "p90_calibrated_pct": round(float(np.percentile(err("e_ml"), 90)), 1)}
    print("Ошибка энергии круга (отложенные):", val)

    m = model().fit(X, y)
    rng = {c: [float(df[c].quantile(0.01)), float(df[c].quantile(0.99))] for c in FEATS}
    g_v = np.round(np.arange(0.5, 1.61, 0.05), 3)
    g_z = np.round(np.arange(-5.0, 5.01, 0.5), 2)
    g_b = np.round(np.arange(0.0, 45.1, 5.0), 1)
    A, Z, B = np.meshgrid(g_v, g_z, g_b, indexing="ij")
    grid = np.column_stack([A.ravel(), Z.ravel(), B.ravel()])
    # без экстраполяции за область данных: прижимаем к 1–99 перцентилю
    for j, c in enumerate(FEATS):
        grid[:, j] = np.clip(grid[:, j], rng[c][0], rng[c][1])
    table = m.predict(grid).reshape(A.shape)
    out = {"kind": "fixed_wing_power_factor",
           "description": "f = P/P_level от (v_ratio = v_air/v_cruise, v_z м/с, bank °)",
           "source": "IDF_DS SpeedyBee/INAV, Zenodo 10.5281/zenodo.16992975, CC BY 4.0",
           "v_cruise_source_ms": round(v_cruise, 2), "p_level_source_w": round(p_level, 1),
           "axes": {"v_ratio": g_v.tolist(), "v_z": g_z.tolist(), "bank": g_b.tolist()},
           "f": np.round(table, 4).tolist(), "trained_range": rng, "validation": val}
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    iv = int(np.argmin(abs(g_v - 1.0))); iz = int(np.argmin(abs(g_z)))
    print("f(v_z) при v=крейсер, крен 0:", {float(z): round(float(table[iv, j, 0]), 2) for j, z in enumerate(g_z) if z in (-3, -1, 0, 1, 3)})
    print("f(крен) горизонтально:", {float(b): round(float(table[iv, iz, k]), 2) for k, b in enumerate(g_b) if b in (0, 15, 30, 45)})
    print("f(v_ratio):", {float(v): round(float(table[i, iz, 0]), 2) for i, v in enumerate(g_v) if round(v, 2) in (0.7, 0.85, 1.0, 1.15, 1.3)})
    print(f"таблица → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
