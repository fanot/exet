"""Геометрия съемки: высота по GSD, ширина полосы, шаг галсов, базис, скорость.

Камера (кадровая съемка):
    H   = GSD · f / p,          p = ширина матрицы / число пикселей
    W   = w_sensor · H / f      — ширина кадра поперек маршрута (длинная сторона матрицы)
    L   = h_sensor · H / f      — длина кадра вдоль маршрута
    шаг галсов  d = W_eff · (1 − поперечное перекрытие)
    базис       b = L_eff · (1 − продольное перекрытие)
Скорость ограничена: ТТХ (с учетом ветра — автопилот держит скорость относительно земли),
частотой срабатывания затвора v ≤ b / t_min и смазом v ≤ 0.5·GSD / t_exp.
Для самолета учитывается угол сноса α = atan2(|w⊥|, V_ground − |w∥|): кадр повернут относительно линии
пути, эффективная полоса W_eff = W·cosα − L·sinα (без этого теряется поперечное перекрытие).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .catalog import Catalog, DroneModel
from .environment import wind_at


@dataclass
class SurveyGeom:
    model: str
    payload_key: str
    payload_title: str
    kind: str
    altitude_agl: float
    gsd_cm: Optional[float]
    swath_m: float
    along_m: float
    side_overlap: float
    front_overlap: float
    speed_ms: float
    transit_speed_ms: float
    trigger_s: Optional[float]
    wind_ms: float
    is_plane: bool
    fixed_spacing_m: Optional[float] = None
    notes: List[str] = field(default_factory=list)
    va_min: float = 0.0          # мин. воздушная скорость (самолет)
    va_max: float = 15.0         # макс. воздушная скорость
    cruise: float = 12.0
    v_cap: float = 99.0          # ограничение съемки (затвор/смаз/плотность точек)
    const_ground: bool = False   # методика QA: путевая скорость постоянна, ветер — только порог по ТТХ и выбор курса
    min_trigger_s: float = 0.0

    def _camera_speed(self, v, w_par, w_perp):
        if self.kind == "camera" and self.min_trigger_s > 0 and v > 0:
            base = self.photo_base(self.crab_rad(w_perp, w_par, v))
            if base < v * self.min_trigger_s - 1e-6:
                return 0.0
        return v

    def survey_speed(self, w_par: float, w_perp: float) -> float:
        """Путевая скорость на галсах заданного направления (галсы летаются в обе стороны).

        Коптер: воздушная скорость ≤ Va_max в обоих направлениях → v ≤ √(Va² − w⊥²) − |w∥|.
        Самолет: Va ∈ [Va_min, Va_max] в обоих направлениях → v ∈ [|w∥| + √(Va_min² − w⊥²), √(Va_max² − w⊥²) − |w∥|].
        Возвращает 0, если направление недопустимо.
        В режиме методики QA (const_ground) автопилот держит путевую скорость — ветер на нее не влияет.
        """
        if self.const_ground:
            return self._camera_speed(self.speed_ms, w_par, w_perp)
        wp, wq = abs(w_par), abs(w_perp)
        if wq >= self.va_max:
            return 0.0
        hi = math.sqrt(self.va_max ** 2 - wq ** 2) - wp
        if self.is_plane:
            lo = wp + math.sqrt(max(0.0, self.va_min ** 2 - wq ** 2))
            v = min(max(self.cruise, lo), hi, self.v_cap)
            return self._camera_speed(v, w_par, w_perp) if v >= lo - 1e-6 and v > 0 else 0.0
        v = min(self.cruise, hi, self.v_cap)
        return v if v >= 2.0 else 0.0

    def transit_speed(self, w_par: float, w_perp: float) -> float:
        """Путевая скорость перелета в одну сторону (w_par > 0 — попутный ветер)."""
        if self.const_ground:
            return self.transit_speed_ms
        wq = min(abs(w_perp), self.va_max * 0.9)
        va = self.cruise if self.is_plane else self.va_max * 0.93
        va = max(va, wq + 0.5)
        return max(2.0, w_par + math.sqrt(va ** 2 - wq ** 2))

    def crab_rad(self, crosswind_ms: float, alongwind_ms: float = 0.0, ground_speed_ms: Optional[float] = None) -> float:
        if not self.is_plane or self.speed_ms <= 0:
            return 0.0  # коптер держит курс по линии пути, кадр не разворачивается
        # V_air = V_ground - wind. Use the worse of the two survey directions;
        # asin(w_cross / V_ground) incorrectly treats ground speed as airspeed.
        v = self.speed_ms if ground_speed_ms is None else ground_speed_ms
        return math.atan2(abs(crosswind_ms), max(v - abs(alongwind_ms), 1e-6))

    def spacing(self, crab: float = 0.0) -> float:
        if self.fixed_spacing_m:
            return self.fixed_spacing_m
        w_eff = self.swath_m * math.cos(crab) - (self.along_m * math.sin(crab) if self.kind == "camera" else 0.0)
        return max(1.0, w_eff * (1 - self.side_overlap))

    def swath_eff(self, crab: float = 0.0) -> float:
        if self.kind != "camera":
            return self.swath_m
        return max(1.0, self.swath_m * math.cos(crab) - self.along_m * math.sin(crab))

    def photo_base(self, crab: float = 0.0) -> Optional[float]:
        if self.kind != "camera":
            return None
        l_eff = self.along_m * math.cos(crab)
        return max(0.5, l_eff * (1 - self.front_overlap))

    def to_dict(self):
        return {
            "model": self.model, "payload": self.payload_key, "payload_title": self.payload_title, "kind": self.kind,
            "altitude_agl_m": round(self.altitude_agl, 1), "gsd_cm": None if self.gsd_cm is None else round(self.gsd_cm, 2),
            "swath_m": round(self.swath_m, 1), "frame_along_m": round(self.along_m, 1),
            "line_spacing_m": round(self.spacing(), 1),
            "photo_base_m": None if self.photo_base() is None else round(self.photo_base(), 1),
            "survey_speed_ms": round(self.speed_ms, 2), "transit_speed_ms": round(self.transit_speed_ms, 2),
            "trigger_interval_s": None if self.trigger_s is None else round(self.trigger_s, 2),
            "wind_at_altitude_ms": round(self.wind_ms, 1), "notes": self.notes,
        }


def compute_survey_geom(cat: Catalog, model: DroneModel, survey_type: str, gsd_cm: Optional[float],
                        front_overlap: Optional[float], side_overlap: Optional[float], wind10_ms: float,
                        max_alt_agl: float, cloud_base_m: Optional[float] = None,
                        altitude_override_m: Optional[float] = None,
                        wind_model: str = "qa") -> Tuple[Optional[SurveyGeom], List[str]]:
    """Возвращает (геометрия, причины невозможности)."""
    st = cat.survey_types[survey_type]
    reasons: List[str] = []
    payload_key = model.payloads.get(survey_type)
    if payload_key is None:
        return None, [f"{model.title}: нет полезной нагрузки для съемки «{st['title']}»"]
    sensor = cat.sensors[payload_key]
    notes: List[str] = []
    if sensor.get("assumption"):
        notes.append(f"Допущение по сенсору: {sensor['assumption']}")

    hi = min(model.max_alt_agl_m, max_alt_agl)
    if cloud_base_m is not None:
        hi = min(hi, cloud_base_m - 50.0)
    lo = model.min_alt_agl_m
    if hi < lo:
        return None, [f"{model.title}: допустимый потолок {hi:.0f} м ниже минимальной безопасной высоты {lo:.0f} м"]
    fo = front_overlap if front_overlap is not None else st.get("front_overlap", 0.0)
    so = side_overlap if side_overlap is not None else st.get("side_overlap", 0.0)
    gsd = None
    fixed_spacing = None
    trigger = None
    kind = sensor["kind"]

    if kind == "camera":
        gsd = gsd_cm if gsd_cm is not None else st["gsd_cm"]
        pixel_mm = sensor["sensor_w_mm"] / sensor["px_w"]
        h = altitude_override_m or (gsd / 100.0) * sensor["focal_mm"] / pixel_mm
        if h > hi:
            notes.append(f"Высота по GSD {h:.0f} м выше допустимой {hi:.0f} м — летим на {hi:.0f} м, GSD лучше заданного")
            h = hi
        if h < lo:
            min_gsd = lo * pixel_mm / sensor["focal_mm"] * 100
            return None, [f"{model.title}: для GSD {gsd:.1f} см нужна высота {h:.0f} м < минимально безопасной {lo:.0f} м. "
                          f"Увеличьте GSD до ≥ {min_gsd:.1f} см или используйте другой тип БВС"]
        gsd_real = h * pixel_mm / sensor["focal_mm"] * 100
        swath = sensor["sensor_w_mm"] * h / sensor["focal_mm"]
        along = sensor["sensor_h_mm"] * h / sensor["focal_mm"]
        base = along * (1 - fo)
        v_trigger = base / sensor["min_trigger_s"]
        v_blur = 0.5 * (gsd_real / 100) / sensor["exposure_s"]
        v_cap = min(v_trigger, v_blur)
        gsd = gsd_real
    elif kind == "lidar":
        h = min(max(altitude_override_m or st["altitude_m"], lo), hi)
        swath = 2 * h * math.tan(math.radians(sensor["fov_deg"] / 2))
        along = 0.0
        v_cap = sensor["points_per_s"] / (swath * st["point_density_m2"])
    else:  # fixed: геофизика — шаг и высота заданы методикой
        h = min(max(altitude_override_m or st["altitude_m"], lo), hi)
        fixed_spacing = st["line_spacing_m"]
        swath = fixed_spacing
        along = 0.0
        v_cap = sensor.get("speed_ms", model.cruise_ms)
        if model.is_plane:
            v_cap = model.cruise_ms
        so = 0.0

    w = wind_at(h, wind10_ms, cat.glob.get("wind_shear_alpha", 0.143))
    if w > model.max_wind_ms:
        return None, [f"{model.title}: ветер на высоте {h:.0f} м ≈ {w:.1f} м/с превышает допустимые {model.max_wind_ms:.0f} м/с"]

    qa = wind_model == "qa"
    if qa:
        # методика QA-сессии: автопилот держит путевую скорость, ветер не меняет ни скорость, ни расход АКБ —
        # только порог выполнимости по ТТХ (проверен выше) и выбор курса галсов (снос кадра самолета)
        if model.is_plane:
            v = min(max(model.cruise_ms, model.airspeed_min_ms), model.airspeed_max_ms, v_cap)
            if v < model.airspeed_min_ms - 1e-6:
                return None, [f"{model.title}: съемка допускает путевую скорость ≤ {v_cap:.1f} м/с (базис/затвор), а минимальная "
                              f"скорость самолета {model.airspeed_min_ms:.1f} м/с. Увеличьте GSD или уменьшите продольное перекрытие"]
            vt = model.cruise_ms
        else:
            v = min(model.cruise_ms, v_cap)
            vt = model.max_speed_ms * 0.93
    elif model.is_plane:
        # допустимость проверяется для лучшего курса галсов (поперек ветра): Va ∈ [Va_min, Va_max];
        # конкретный курс выбирается в coverage.choose_heading с учетом этого же ограничения
        v_lo = math.sqrt(max(0.0, model.airspeed_min_ms ** 2 - w ** 2))
        v_hi = math.sqrt(max(0.0, model.airspeed_max_ms ** 2 - w ** 2))
        v = min(max(model.cruise_ms, v_lo), v_hi, v_cap)
        if v < v_lo - 1e-6 or v <= 0:
            if v_cap < v_lo:
                return None, [f"{model.title}: при ветре {w:.1f} м/с путевая скорость должна быть ≥ {v_lo:.1f} м/с, "
                              f"а съемка допускает ≤ {v_cap:.1f} м/с (базис/затвор). Увеличьте GSD или уменьшите продольное перекрытие"]
            return None, [f"{model.title}: ветер {w:.1f} м/с не оставляет допустимого диапазона воздушной скорости"]
        vt = model.cruise_ms
    else:
        v = min(model.cruise_ms, model.max_speed_ms - w, v_cap)
        vt = max(3.0, min(model.max_speed_ms * 0.93, model.max_speed_ms - w))
        if v < 2.0:
            return None, [f"{model.title}: допустимая скорость съемки {v:.1f} м/с слишком мала (ветер {w:.1f} м/с)"]
    if kind == "camera":
        trigger = along * (1 - fo) / v
    title = sensor.get("title", payload_key)
    extra = dict(va_min=model.airspeed_min_ms if model.is_plane else 0.0,
                 va_max=(model.airspeed_max_ms if model.is_plane else model.max_speed_ms),
                 cruise=model.cruise_ms, v_cap=v_cap)
    return SurveyGeom(**extra, const_ground=qa, min_trigger_s=sensor.get("min_trigger_s", 0.0), model=model.key, payload_key=payload_key, payload_title=title, kind=kind, altitude_agl=h,
                      gsd_cm=gsd, swath_m=swath, along_m=along, side_overlap=so, front_overlap=fo, speed_ms=v,
                      transit_speed_ms=vt, trigger_s=trigger, wind_ms=w, is_plane=model.is_plane,
                      fixed_spacing_m=fixed_spacing, notes=notes), reasons
