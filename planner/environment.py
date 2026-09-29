"""Ветер по высоте и астрономическое окно съемки."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import List, Tuple


def wind_at(h_m: float, wind10_ms: float, alpha: float = 0.143) -> float:
    """Степенной профиль ветра: замер на 10 м пересчитывается на высоту полета.

    QA: борт замеряет ветер в начале полета, прогноз «по разным высотам разный» —
    для проверки выполнимости берем ветер на рабочей высоте, а не у земли.
    """
    return wind10_ms * (max(h_m, 10.0) / 10.0) ** alpha


def sun_elevation_deg(lat: float, lon: float, t: datetime) -> float:
    """Высота Солнца (алгоритм NOAA, точность ~0.5°)."""
    t = t.astimezone(timezone.utc)
    jd = t.timestamp() / 86400.0 + 2440587.5
    jc = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc)) + math.sin(2 * mr) * (0.019993 - 0.000101 * jc)
         + math.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    eps0 = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    decl = math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(app_long)))
    y = math.tan(math.radians(eps / 2)) ** 2
    l0r = math.radians(l0)
    eq_time = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                               - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    minutes = t.hour * 60 + t.minute + t.second / 60
    true_solar = (minutes + eq_time + 4 * lon) % 1440
    ha = true_solar / 4 - 180
    zen = math.acos(max(-1.0, min(1.0, math.sin(math.radians(lat)) * math.sin(decl)
                                  + math.cos(math.radians(lat)) * math.cos(decl) * math.cos(math.radians(ha)))))
    return 90 - math.degrees(zen)


def daylight_windows(lat: float, lon: float, start: datetime, days: int, min_elev_deg: float) -> List[Tuple[float, float]]:
    """Интервалы (epoch s), когда Солнце выше min_elev_deg, начиная с даты старта."""
    windows = []
    step = timedelta(minutes=5)
    t = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = t + timedelta(days=days)
    cur_start = None
    prev_t, prev_up = t, None
    while t <= end:
        up = sun_elevation_deg(lat, lon, t) >= min_elev_deg
        crossing = t
        if prev_up is not None and up != prev_up:
            # Locate the actual threshold instead of admitting up to five minutes of darkness.
            lo, hi = prev_t, t
            while (hi - lo).total_seconds() > 0.05:
                mid = lo + (hi - lo) / 2
                if (sun_elevation_deg(lat, lon, mid) >= min_elev_deg) == prev_up:
                    lo = mid
                else:
                    hi = mid
            crossing = hi if up else lo
        if up and cur_start is None:
            cur_start = crossing
        elif not up and cur_start is not None:
            windows.append((cur_start.timestamp(), crossing.timestamp()))
            cur_start = None
        prev_t, prev_up = t, up
        t += step
    if cur_start is not None:
        windows.append((cur_start.timestamp(), end.timestamp()))
    return [(max(a, start.timestamp()), b) for a, b in windows if b > start.timestamp()]
