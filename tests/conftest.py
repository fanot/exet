import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def toksovo():
    from planner.geo import parse_geojson
    return json.loads((ROOT / "scenarios" / "demo_toksovo.geojson").read_text(encoding="utf-8"))


def square(lon, lat, km):
    import math
    dlat = km / 111.32 / 2
    dlon = km / (111.32 * math.cos(math.radians(lat))) / 2
    return [[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat], [lon - dlon, lat + dlat], [lon - dlon, lat - dlat]]
