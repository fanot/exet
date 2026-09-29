"""Калибровка модели энергии мультикоптера по открытой телеметрии (офлайн).

Данные: Rodrigues et al., «Data Collected with Package Delivery Quadcopter Drone» (CMU, DJI Matrice 100),
figshare DOI 10.1184/R1/12683453, лицензия CC BY 4.0. 209 полетов, 5 Гц: ток и напряжение АКБ,
анемометр (воздушная скорость), скорости, ускорения, масса груза.

Идея переноса на борта Геоскан. Абсолютная мощность зависит от конкретного аппарата, а форма
зависимости от режима полета у мультикоптеров схожа. Поэтому модель учит БЕЗРАЗМЕРНЫЙ коэффициент
    f = P / P_hover   (P_hover — мощность висения этого аппарата без груза)
от режима: воздушная скорость, вертикальная скорость, горизонтальное ускорение, относительная масса груза.
В планировщике мощность = P_ref(ТТХ борта) · f(режим), где P_ref = энергия АКБ / макс. время полета.

Модель: HistGradientBoostingRegressor с физическими ограничениями монотонности
(набор высоты и ускорение не уменьшают мощность, груз — увеличивает).
Проверка: GroupKFold по полетам (без утечки данных одного полета между train/test),
сравнение с линейной моделью «энергия ∝ времени» на уровне целого полета.

В сервис идет не sklearn-модель, а компактная таблица f на сетке режимов (planner/energy_model.json),
интерполируемая numpy — без ML-зависимостей в рантайме.

Запуск:  python scripts/train_energy.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "telemetry" / "cmu"
OUT = ROOT / "planner" / "energy_model.json"
M0_KG = 3.68          # масса Matrice 100 с АКБ TB47D и навесным оборудованием эксперимента (оценка по статье)
WIN = 5               # окно агрегации: 5 отсчетов = 1 с
FEATS = ["v_air", "v_z", "a_h", "payload_frac"]
MONO = [0, 1, 1, 1]   # v_air — U-образная зависимость, остальное — не убывает


def load_flights():
    P = pd.read_csv(DATA / "parameters.csv")
    P = P[P.route.str.startswith("R") | (P.route == "H")]  # только реальные полеты (без наземных тестов A1–A3)
    frames = []
    for _, r in P.iterrows():
        f = DATA / "flights" / f"{int(r.flight)}.csv"
        if not f.exists():
            continue
        d = pd.read_csv(f)
        d["P"] = d.battery_voltage * d.battery_current
        z0 = d.position_z.iloc[:10].median()
        d["agl"] = d.position_z - z0
        d["air"] = (d.agl > 1.0) & (d.P > 100)
        # оконная агрегация 1 с
        g = np.arange(len(d)) // WIN
        a = d.groupby(g).agg(t=("time", "first"), P=("P", "mean"), air=("air", "min"),
                             vx=("velocity_x", "mean"), vy=("velocity_y", "mean"), vz=("velocity_z", "mean"),
                             ax=("linear_acceleration_x", "mean"), ay=("linear_acceleration_y", "mean"),
                             wind=("wind_speed", "mean"))
        a = a[a.air]
        if len(a) < 20:
            continue
        a["v_h"] = np.hypot(a.vx, a.vy)
        # анемометр на борту дает скорость набегающего потока (воздушную скорость) с учетом собственного движения
        a["v_air"] = a.wind
        a["v_z"] = a.vz
        a["a_h"] = np.hypot(a.ax, a.ay)
        a["payload_frac"] = r.payload / 1000.0 / M0_KG
        a["flight"] = int(r.flight)
        a["route"] = r.route
        a["dt"] = WIN * 0.2
        frames.append(a[["flight", "route", "t", "dt", "P", "v_h", "v_air", "v_z", "a_h", "payload_frac"]])
    return pd.concat(frames, ignore_index=True)


def main():
    df = load_flights()
    # опорная мощность висения без груза: режим почти без движения у полетов без груза
    hov = df[(df.payload_frac == 0) & (df.v_h < 0.5) & (df.v_z.abs() < 0.2) & (df.a_h < 0.3)]
    p_hover = float(hov.P.median())
    df["f"] = df.P / p_hover
    print(f"окон (1 с): {len(df)}, полетов: {df.flight.nunique()}, P_hover = {p_hover:.0f} Вт")

    X, y, groups = df[FEATS].values, df.f.values, df.flight.values
    model = lambda: HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                                  min_samples_leaf=80, l2_regularization=1.0,
                                                  monotonic_cst=MONO, random_state=0)
    # --- кросс-валидация по полетам
    rows = []
    for k, (tr, te) in enumerate(GroupKFold(n_splits=5).split(X, y, groups)):
        m = model().fit(X[tr], y[tr])
        pred = m.predict(X[te])
        dtr, dte = df.iloc[tr], df.iloc[te].copy()
        dte["pred"] = pred
        p_mean_train = float((dtr.P * dtr.dt).sum() / dtr.dt.sum())  # линейная модель: средняя мощность × время
        for fid, g in dte.groupby("flight"):
            e_true = float((g.P * g.dt).sum())
            e_lin = p_mean_train * float(g.dt.sum())
            e_ml = float((g.pred * p_hover * g.dt).sum())
            rows.append((fid, e_true, e_lin, e_ml))
        ss = ((y[te] - pred) ** 2).sum() / ((y[te] - y[te].mean()) ** 2).sum()
        print(f"  fold {k}: R² окон = {1 - ss:.3f}")
    r = pd.DataFrame(rows, columns=["flight", "e_true", "e_lin", "e_ml"])
    mape_lin = float((abs(r.e_lin - r.e_true) / r.e_true).mean() * 100)
    mape_ml = float((abs(r.e_ml - r.e_true) / r.e_true).mean() * 100)
    p90_lin = float(np.percentile(abs(r.e_lin - r.e_true) / r.e_true * 100, 90))
    p90_ml = float(np.percentile(abs(r.e_ml - r.e_true) / r.e_true * 100, 90))
    under_lin = float(((r.e_lin < r.e_true * 0.9)).mean() * 100)
    under_ml = float(((r.e_ml < r.e_true * 0.9)).mean() * 100)
    print(f"Ошибка энергии полета (отложенные полеты, {len(r)}):")
    print(f"  линейная по времени: MAPE {mape_lin:.1f}%, P90 {p90_lin:.1f}%, недооценка >10%: {under_lin:.0f}% полетов")
    print(f"  калиброванная ML:    MAPE {mape_ml:.1f}%, P90 {p90_ml:.1f}%, недооценка >10%: {under_ml:.0f}% полетов")

    # --- финальная модель на всех данных → таблица на сетке режимов
    m = model().fit(X, y)
    g_air = np.round(np.arange(0.0, 20.01, 0.5), 2)
    g_vz = np.round(np.arange(-5.0, 5.01, 0.5), 2)
    g_a = np.round(np.arange(0.0, 4.01, 1.0), 2)
    A, Z, C = np.meshgrid(g_air, g_vz, g_a, indexing="ij")
    grid = np.column_stack([A.ravel(), Z.ravel(), C.ravel(), np.zeros(A.size)])
    # данные горизонтального полета (анемометр) есть до ~7 м/с воздушной скорости: дальше не экстраполируем —
    # берем значение на краю обученной области (консервативно: без «провала» мощности на больших скоростях)
    V_MAX_SUPPORTED = 6.5
    grid[:, 0] = np.minimum(grid[:, 0], V_MAX_SUPPORTED)
    table = m.predict(grid).reshape(A.shape)
    # границы обученной области: за ними — значение ближайшего края (плюс флаг экстраполяции)
    rng = {c: [float(df[c].quantile(0.01)), float(df[c].quantile(0.99))] for c in ["v_air", "v_z", "a_h"]}
    out = {
        "kind": "multirotor_power_factor",
        "description": "f = P/P_hover как функция (v_air м/с, v_z м/с, a_h м/с²), груз = 0; P = P_ref·f",
        "source": "Rodrigues et al. 2021, DJI Matrice 100, figshare 10.1184/R1/12683453, CC BY 4.0",
        "p_hover_source_w": round(p_hover, 1),
        "axes": {"v_air": g_air.tolist(), "v_z": g_vz.tolist(), "a_h": g_a.tolist()},
        "f": np.round(table, 4).tolist(),
        "trained_range": rng,
        "v_air_clamp": V_MAX_SUPPORTED,
        "validation": {"flights": int(len(r)), "mape_linear_pct": round(mape_lin, 1), "mape_calibrated_pct": round(mape_ml, 1),
                       "p90_linear_pct": round(p90_lin, 1), "p90_calibrated_pct": round(p90_ml, 1),
                       "underestimate_gt10_linear_pct": round(under_lin, 1), "underestimate_gt10_calibrated_pct": round(under_ml, 1)},
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    # срез для отчета
    iz = list(g_vz).index(0.0)
    print("f(v_air) при v_z=0, a=0:", {float(v): round(float(table[i, iz, 0]), 2) for i, v in enumerate(g_air) if v in (0, 3, 6, 9, 12, 15)})
    print("f(v_z) при v_air=0:", {float(v): round(float(table[0, j, 0]), 2) for j, v in enumerate(g_vz) if v in (-3, -1, 0, 1, 3, 5)})
    print(f"таблица → {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    sys.exit(main())
