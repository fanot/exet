"""Цифровая модель рельефа.

Источник — открытые тайлы Terrarium (Mapzen/AWS Open Data, SRTM/ASTER/GMTED, ~20 м на z12
в средних широтах). Тайлы загружаются один раз на сценарий и кэшируются на диск, дальше
высоты берутся билинейной интерполяцией из массива в памяти — опрос по сети на каждую
точку не выполняется (QA: «обращение к ЦМР занимает много времени»).
Если сеть недоступна или ЦМР отключена — рельеф считается плоским, в план пишется предупреждение.
"""
from __future__ import annotations

import io
import math
import os
import urllib.request
from pathlib import Path

import numpy as np

TILE_URL = os.environ.get("DEM_TILE_URL", "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png")
CACHE_DIR = Path(os.environ.get("DEM_CACHE_DIR", Path(__file__).resolve().parent.parent / "data" / "dem_cache"))


def _lonlat_to_px(lon, lat, z):
    n = 2 ** z * 256
    x = (np.asarray(lon) + 180.0) / 360.0 * n
    lat_r = np.radians(np.asarray(lat))
    y = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


class Terrain:
    def __init__(self, bbox_ll, enabled: bool = True, zoom: int = 12, margin_deg: float = 0.03):
        self.enabled = False
        self.source = "flat (рельеф не учитывается)"
        self.warning = None
        self.zoom = zoom
        if not enabled:
            return
        minx, miny, maxx, maxy = bbox_ll
        if miny < -85.05112878 or maxy > 85.05112878:
            self.warning = "Район вне широт покрытия Web Mercator ЦМР (±85.05°) — рельеф проверить невозможно"
            return
        minx, miny, maxx, maxy = minx - margin_deg, miny - margin_deg, maxx + margin_deg, maxy + margin_deg
        miny, maxy = max(-85.05112878, miny), min(85.05112878, maxy)
        # большая территория (задание организаторов — 65×58 км): уровень тайлов понижается, а не отключается рельеф
        z = zoom
        while True:
            x0, y1 = _lonlat_to_px(minx, miny, z)
            x1, y0 = _lonlat_to_px(maxx, maxy, z)
            tx0, tx1 = int(x0 // 256), int(x1 // 256)
            ty0, ty1 = int(y0 // 256), int(y1 // 256)
            if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= 100 or z <= 9:
                break
            z -= 1
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > 100:
            self.warning = "Территория слишком велика для ЦМР — рельеф не учитывается"
            return
        self.zoom = z
        if z < zoom:
            self.warning = f"Территория большая — ЦМР взята с уровня z{z} (≈ {40 * 2 ** (12 - z) // 2:.0f} м/пикс) вместо z{zoom}"
        try:
            from PIL import Image

            mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), dtype=np.float32)
            for ty in range(ty0, ty1 + 1):
                for tx in range(tx0, tx1 + 1):
                    data = self._fetch(z, tx, ty)
                    img = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float32)
                    elev = img[:, :, 0] * 256.0 + img[:, :, 1] + img[:, :, 2] / 256.0 - 32768.0
                    oy, ox = (ty - ty0) * 256, (tx - tx0) * 256
                    mosaic[oy:oy + 256, ox:ox + 256] = elev
        except Exception as e:  # noqa: BLE001
            self.warning = f"ЦМР недоступна ({type(e).__name__}) — рельеф считается плоским"
            return
        self.mosaic = mosaic
        self.px0, self.py0 = tx0 * 256, ty0 * 256
        self.enabled = True
        self.source = f"Terrarium DEM z{z} (~{40075016 * math.cos(math.radians((miny + maxy) / 2)) / 2 ** z / 256:.0f} м/пикс)"

    @staticmethod
    def _fetch(z, x, y) -> bytes:
        path = CACHE_DIR / str(z) / str(x) / f"{y}.png"
        if path.exists():
            return path.read_bytes()
        url = TILE_URL.format(z=z, x=x, y=y)
        req = urllib.request.Request(url, headers={"User-Agent": "geoscan-planner/1.0"})
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = r.read()
                break
            except Exception as e:  # noqa: BLE001 — повтор при сетевом сбое
                last = e
        else:
            raise last
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    def elevation(self, lon, lat) -> np.ndarray:
        lon = np.atleast_1d(np.asarray(lon, dtype=float))
        lat = np.atleast_1d(np.asarray(lat, dtype=float))
        if not self.enabled:
            return np.zeros_like(lon)
        px, py = _lonlat_to_px(lon, lat, self.zoom)
        fx = np.clip(px - self.px0 - 0.5, 0, self.mosaic.shape[1] - 1.001)
        fy = np.clip(py - self.py0 - 0.5, 0, self.mosaic.shape[0] - 1.001)
        ix, iy = fx.astype(int), fy.astype(int)
        dx, dy = fx - ix, fy - iy
        m = self.mosaic
        v = (m[iy, ix] * (1 - dx) * (1 - dy) + m[iy, ix + 1] * dx * (1 - dy)
             + m[iy + 1, ix] * (1 - dx) * dy + m[iy + 1, ix + 1] * dx * dy)
        return np.maximum(v, -100.0)


class TerrainXY:
    """Рельеф в локальных метрических координатах."""

    def __init__(self, terrain: Terrain, frame):
        self.t, self.frame = terrain, frame

    @property
    def enabled(self):
        return self.t.enabled

    def h(self, x, y) -> np.ndarray:
        x = np.atleast_1d(np.asarray(x, dtype=float))
        y = np.atleast_1d(np.asarray(y, dtype=float))
        if not self.t.enabled:
            return np.zeros_like(x)
        lon, lat = self.frame.to_ll(x, y)
        return self.t.elevation(lon, lat)

    def max_along(self, pts, step: float = 25.0) -> float:
        """Максимальная высота рельефа вдоль ломаной (шаг опроса ~25 м)."""
        pts = np.asarray(pts, dtype=float)
        if not self.t.enabled or len(pts) == 0:
            return 0.0
        xs, ys = [pts[0, 0]], [pts[0, 1]]
        for a, b in zip(pts[:-1], pts[1:]):
            d = float(np.hypot(*(b - a)))
            n = max(1, int(math.ceil(d / step)))
            s = np.linspace(0, 1, n + 1)[1:]
            xs.extend(a[0] + (b[0] - a[0]) * s)
            ys.extend(a[1] + (b[1] - a[1]) * s)
        return float(np.max(self.h(np.array(xs), np.array(ys))))
