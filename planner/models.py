"""Параметры миссии (вход REST API / CLI). Все поля документированы с единицами измерения."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MSK = timezone(timedelta(hours=3))


def _default_start() -> datetime:
    d = datetime.now(MSK) + timedelta(days=1)
    return d.replace(hour=9, minute=0, second=0, microsecond=0)


class FleetItem(BaseModel):
    model: Literal["geoscan_201", "geoscan_gemini", "geoscan_801"] = Field(
        ..., description="Тип БВС из каталога: geoscan_201 (самолет), geoscan_gemini, geoscan_801 (коптеры)")
    count: int = Field(1, ge=0, le=20, description="Количество бортов этого типа, шт")
    batteries: Optional[int] = Field(None, ge=1, le=20, description="Комплектов АКБ на один борт, шт (по умолчанию из каталога)")


class ExtraSite(BaseModel):
    lon: float = Field(..., ge=-180, le=180, description="Долгота WGS-84, градусы")
    lat: float = Field(..., ge=-90, le=90, description="Широта WGS-84, градусы")
    name: str = Field("Рекомендованная площадка", description="Название площадки")


class MissionParams(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    survey_type: Literal["rgb", "multispectral", "ir", "lidar", "geophysics"] = Field(
        "rgb", description="Тип съемки: rgb, multispectral, ir (тепловизор), lidar, geophysics")
    gsd_cm: Optional[float] = Field(None, gt=0.3, le=200, description="Требуемое пространственное разрешение GSD, см/пикс (по умолчанию — для типа съемки)")
    front_overlap: Optional[float] = Field(None, ge=0.5, le=0.95, description="Продольное перекрытие, доля 0..1 (по умолчанию 0.70)")
    side_overlap: Optional[float] = Field(None, ge=0.0, le=0.9, description="Поперечное перекрытие между галсами, доля 0..1 (по умолчанию 0.50)")
    fleet: List[FleetItem] = Field(..., description="Доступный парк БВС")
    objective: Literal["time", "flight"] = Field(
        "time", description="Критерий: time — минимум общего времени выполнения; flight — минимум суммарного налета")
    deadline_h: Optional[float] = Field(None, gt=0, le=240, description="Ограничение на длительность работ, ч (для критерия flight)")
    takeoff_weight_h: float = Field(0.0, ge=0, le=5, description="Эквивалент одного взлета-посадки в часах налета (износ), ч")
    start_time: datetime = Field(default_factory=_default_start, description="Начало работ (ISO 8601 с часовым поясом)")
    wind_speed_ms: float = Field(4.0, ge=0, le=40, description="Скорость ветра у земли (10 м), м/с")
    wind_dir_deg: float = Field(270.0, ge=0, lt=360, description="Направление ветра — откуда дует, градусы от севера по часовой")
    cloud_base_m: Optional[float] = Field(None, gt=50, le=5000, description="Нижняя граница облачности, м над землей")
    respect_daylight: bool = Field(True, description="Планировать съемку только при высоте Солнца ≥ min_sun_elevation_deg")
    min_sun_elevation_deg: float = Field(10.0, ge=-5, le=60, description="Минимальная высота Солнца для съемки, градусы")
    use_terrain: bool = Field(True, description="Учитывать рельеф (ЦМР) — полет на постоянной высоте над землей")
    energy_model: Literal["linear", "calibrated"] = Field(
        "linear", description="Модель расхода АКБ: linear — пропорционально времени (QA); calibrated — мощность мультикоптера "
                              "по режиму полета (скорость, набор/снижение, разгоны), откалибрована по открытой телеметрии")
    wind_model: Literal["qa", "physical"] = Field(
        "qa", description="Учет ветра: qa — методика QA-сессии (ветер — порог по ТТХ и выбор курса галсов; путевая скорость "
                          "постоянна, на расход АКБ не влияет); physical — путевая скорость по треугольнику скоростей "
                          "(встречный ветер замедляет перелет и съемку, попутный ускоряет) — консервативная оценка")
    temp_restrictions: Literal["active", "ignore"] = Field(
        "active", description="Временные ограничения без времени действия в данных (Type=врем_ограничение без active_from/active_to): "
                              "active — считать действующими весь период работ (консервативно); ignore — не учитывать "
                              "(действие проверено по NOTAM/плану ИВП)")
    airspace_mode: Literal["flight_plan", "no_flight_plan"] = Field(
        "flight_plan", description="flight_plan — полеты по плану полета, коридор до 700 м AGL (постановка QA); "
                                   "no_flight_plan — без плана полета по ФП ИВП (Пост. Правительства РФ №138): до 150 м от земли, "
                                   "прямая видимость, светлое время, только БВС до 30 кг")
    vlos_radius_km: float = Field(2.0, ge=0.3, le=10.0, description="Режим no_flight_plan: допустимое удаление коптера от площадки "
                                                                  "(прямая видимость; в правилах числом не задана), км")
    reserve_pct: float = Field(20.0, ge=20, le=60, description="Неснижаемый остаток заряда на посадке, % (QA: не менее 20%)")
    extra_sites: List[ExtraSite] = Field(default_factory=list, description="Дополнительные площадки (например, рекомендованные сервисом)")
    search_effort: Literal["fast", "normal", "thorough"] = Field("normal", description="Глубина поиска оптимизатора")
    optimizer: Literal["grid", "bayes"] = Field(
        "bayes", description="Метод поиска параметров плана: grid — сетка + локальное уточнение; bayes — байесовская "
                            "оптимизация (TPE, Optuna) с теплым стартом от сетки, непрерывные доли/колонки и размер ячеек")

    @field_validator("start_time")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=MSK)

    @model_validator(mode="after")
    def _fleet(self):
        models = [f.model for f in self.fleet]
        if len(models) != len(set(models)):
            raise ValueError("Каждый тип БВС должен встречаться в парке один раз; задайте количество в count")
        if self.survey_type in ("rgb", "multispectral", "ir"):
            if self.front_overlap is not None and self.front_overlap < 0.70:
                raise ValueError("Для кадровой съемки продольное перекрытие должно быть не менее 70%")
            if self.side_overlap is not None and self.side_overlap < 0.50:
                raise ValueError("Для кадровой съемки поперечное перекрытие должно быть не менее 50%")
        total = sum(f.count for f in self.fleet)
        if total == 0:
            raise ValueError("Парк БВС пуст — задайте хотя бы один борт")
        if total > 30:
            raise ValueError("Не более 30 бортов в одном плане")
        return self
