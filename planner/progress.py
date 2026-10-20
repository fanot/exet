"""Planned survey footprint over time, derived from an existing saved plan."""
from collections import defaultdict

import shapely
from shapely.geometry import LineString, mapping, shape

from .geo import LocalFrame


def coverage_timeline(plan, buckets=240):
    """Return new, non-overlapping survey areas and cumulative planned coverage.

    Segments are grouped into short time bins so an existing plan can be animated
    without running spatial unions for every frame in the browser. The final
    percentage is calibrated to the planner's validated allowed-area coverage.
    """
    sorties = plan.get("sorties") or []
    requested_json = (plan.get("geo") or {}).get("requested")
    summary = plan.get("summary") or {}
    if not sorties or not requested_json:
        return {"steps": [], "final_pct": summary.get("coverage_allowed_pct"),
                "allowed_km2": summary.get("area_allowed_km2")}

    requested_ll = shape(requested_json)
    center = requested_ll.centroid
    frame = LocalFrame(center.x, center.y)
    requested = frame.geom_to_xy(requested_ll)
    models = plan.get("models") or {}
    start = min(s["t_takeoff"] for s in sorties)
    end = max(s["t_landing"] for s in sorties)
    span = max(end - start, 1)
    buckets = max(1, min(int(buckets), 360))
    grouped = defaultdict(list)
    times = defaultdict(list)
    for sortie in sorties:
        model = models.get(sortie.get("model")) or {}
        width = model.get("swath_m")
        if not width or width <= 0:
            continue
        points = sortie.get("waypoints") or []
        for a, b in zip(points, points[1:]):
            if b[5] != "survey" or b[4] <= a[4]:
                continue
            p0 = frame.to_xy(a[0], a[1])
            p1 = frame.to_xy(b[0], b[1])
            if p0 == p1:
                continue
            polygon = LineString([p0, p1]).buffer(width / 2, cap_style="flat")
            index = min(buckets - 1, int((b[4] - start) / span * buckets))
            grouped[index].append(polygon)
            times[index].append((a[4], b[4]))

    cumulative = shapely.GeometryCollection()
    steps = []
    total_area = 0.0
    for index in sorted(grouped):
        footprint = shapely.union_all(grouped[index]).intersection(requested)
        if footprint.is_empty:
            continue
        fresh = footprint.difference(cumulative) if not cumulative.is_empty else footprint
        if fresh.is_empty or fresh.area < 0.01:
            continue
        cumulative = shapely.union_all([cumulative, fresh]) if not cumulative.is_empty else fresh
        total_area = cumulative.area
        # The geometry stays within the requested area; simplify display only.
        display = fresh.simplify(1.5, preserve_topology=True)
        steps.append({"from": min(a for a, _ in times[index]),
                      "t": max(b for _, b in times[index]),
                      "area_m2": total_area,
                      "geometry": mapping(frame.geom_to_ll(display))})

    final_pct = summary.get("coverage_allowed_pct")
    allowed_km2 = summary.get("area_allowed_km2")
    for step in steps:
        fraction = step["area_m2"] / total_area if total_area else 0
        step["pct"] = round(fraction * final_pct, 2) if final_pct is not None else None
        step["covered_km2"] = round(fraction * final_pct / 100 * allowed_km2, 3) if final_pct is not None and allowed_km2 is not None else None
        del step["area_m2"]
    return {"steps": steps, "final_pct": final_pct, "allowed_km2": allowed_km2}
