"""Загрузка каталога БВС / сенсоров / типов съемки из catalog.yaml."""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

import yaml

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")


@dataclass
class DroneModel:
    key: str
    title: str
    type: str  # fixed_wing | multirotor
    endurance_min: float
    cruise_ms: float
    max_speed_ms: float
    max_wind_ms: float
    min_alt_agl_m: float
    max_alt_agl_m: float
    climb_ms: float
    sink_ms: float
    turnaround_min: float
    charge_min: float
    batteries: int
    separation_m: float
    nofly_buffer_m: float
    payloads: Dict[str, str]
    max_route_km: Optional[float] = None
    airspeed_min_ms: float = 0.0
    airspeed_max_ms: Optional[float] = None
    bank_deg: float = 30.0
    launch_s: float = 0.0
    parachute_alt_m: float = 0.0
    parachute_ms: float = 5.0
    accel_ms2: float = 2.0
    maintenance_flights: Optional[int] = None    # ТО каждые N полетов (ответ организаторов: 201 — 80)
    maintenance_hours: Optional[float] = None    # ТО каждые N часов налета (801 — 160)
    maintenance_note: str = ""
    obstacle_v_clearance_m: Optional[float] = None
    max_alt_amsl_m: float = 4000.0

    @property
    def is_plane(self) -> bool:
        return self.type == "fixed_wing"

    @property
    def turn_radius_m(self) -> float:
        if not self.is_plane:
            return 0.0
        return self.cruise_ms ** 2 / (9.81 * math.tan(math.radians(self.bank_deg)))


@dataclass
class Catalog:
    glob: dict
    survey_types: Dict[str, dict]
    sensors: Dict[str, dict]
    drones: Dict[str, DroneModel] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _load_raw() -> dict:
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_catalog() -> Catalog:
    raw = copy.deepcopy(_load_raw())
    drones = {k: DroneModel(key=k, **v) for k, v in raw["drones"].items()}
    return Catalog(glob=raw["global"], survey_types=raw["survey_types"], sensors=raw["sensors"], drones=drones)
