"""Растровая картинка плана (для отчетов/презентации): зоны, маршруты по бортам, площадки; и диаграмма Ганта.

python scripts/render_plan.py out/plan_full.json scenarios/demo_toksovo.geojson out/plan.png [out/gantt.png]
"""
import json
import math
import sys

from PIL import Image, ImageDraw, ImageFont

PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#e377c2", "#8c564b", "#bcbd22", "#7f7f7f"]


def font(sz):
    for f in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, sz)
        except OSError:
            pass
    return ImageFont.load_default()


def render_map(plan, scen, out, W=1600, H=1200, transparent=False):
    lons, lats = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            lons.append(c[0]); lats.append(c[1])
        else:
            for x in c:
                walk(x)
    # рамка — область съемки и площадки (зоны и препятствия региональных файлов тянутся далеко за район работ)
    for f in scen["features"]:
        if f["properties"]["kind"] in ("survey_area", "site", "reserve_site"):
            walk(f["geometry"]["coordinates"])
    for st in plan.get("sites", []):
        lons.append(st["lon"]); lats.append(st["lat"])
    if not lons:
        for f in scen["features"]:
            walk(f["geometry"]["coordinates"])
    lat0 = sum(lats) / len(lats)
    kx = math.cos(math.radians(lat0))
    x0, x1, y0, y1 = min(lons), max(lons), min(lats), max(lats)
    pad = 40
    s = min((W - 2 * pad) / ((x1 - x0) * kx), (H - 2 * pad) / (y1 - y0))

    def P(lon, lat):
        return (pad + (lon - x0) * kx * s, H - pad - (lat - y0) * s)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0)) if transparent else Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img, "RGBA")

    def rings(g):
        if g["type"] == "Polygon":
            return [g["coordinates"]]
        if g["type"] == "MultiPolygon":
            return g["coordinates"]
        return []
    for f in scen["features"]:
        k, p, g = f["properties"]["kind"], f["properties"], f["geometry"]
        for poly in rings(g):
            pts = [P(*c[:2]) for c in poly[0]]
            if k == "survey_area":
                d.polygon(pts, fill=(11, 99, 206, 22), outline=(11, 99, 206, 255))
                for h in poly[1:]:
                    d.polygon([P(*c[:2]) for c in h], fill=(255, 255, 255, 255), outline=(11, 99, 206, 255))
            elif k == "no_fly":
                if p.get("active_from") or p.get("category") == "TEMP":
                    d.polygon(pts, fill=(224, 123, 0, 45), outline=(224, 123, 0, 255))
                elif p.get("alt_min_m"):
                    d.polygon(pts, fill=(123, 63, 179, 18), outline=(123, 63, 179, 200))
                else:
                    d.polygon(pts, fill=(198, 40, 40, 70), outline=(198, 40, 40, 255))
    colors = {dr["id"]: dr.get("color") or PALETTE[i % len(PALETTE)] for i, dr in enumerate(plan["drones"])}
    for so in plan["sorties"]:
        col = colors[so["drone_id"]]
        W_ = so["waypoints"]
        for a, b in zip(W_[:-1], W_[1:]):
            surv = b[5] == "survey"
            d.line([P(a[0], a[1]), P(b[0], b[1])], fill=col + ("ff" if surv else "70"), width=2 if surv else 1)
    for f in scen["features"]:
        k, g = f["properties"]["kind"], f["geometry"]
        if k == "obstacle":
            if g["type"] == "Point":
                x, y = P(*g["coordinates"][:2]); d.ellipse([x - 6, y - 6, x + 6, y + 6], fill="#ffd600", outline="black", width=2)
            elif g["type"] in ("Polygon", "MultiPolygon"):
                for poly in rings(g):
                    pts = [P(*c[:2]) for c in poly[0]]
                    if len(pts) > 2:
                        d.polygon(pts, fill=(17, 24, 39, 200), outline=(17, 24, 39, 255))
            else:
                d.line([P(*c[:2]) for c in g["coordinates"]], fill="black", width=3)
    for st in plan.get("sites") or []:
        x, y = P(st["lon"], st["lat"])
        fill = "#0891b2" if st.get("auto") else "#888" if st.get("reserve") else "#111"
        d.rectangle([x - 8, y - 8, x + 8, y + 8], fill=fill, outline="white", width=2)
    if not plan.get("sites"):
        for f in scen["features"]:
            if f["properties"]["kind"] in ("site", "reserve_site"):
                x, y = P(*f["geometry"]["coordinates"][:2])
                d.rectangle([x - 9, y - 9, x + 9, y + 9], fill="#111" if f["properties"]["kind"] == "site" else "#888", outline="white", width=2)
    img.save(out)


def render_gantt(plan, out, W=1600):
    ds = plan["drones"]
    rowH, left, top = 34, 130, 40
    H = top + rowH * len(ds) + 20
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    so = plan["sorties"]
    t0, t1 = min(s["t_takeoff"] for s in so), max(s["t_landing"] for s in so)
    # сжатие ночей
    segs = []
    for a, b in sorted((s["t_takeoff"], s["t_landing"]) for s in so):
        if segs and a - segs[-1][1] < 3 * 3600:
            segs[-1][1] = max(segs[-1][1], b)
        else:
            segs.append([a, b])
    gap = 30
    total = sum(b - a for a, b in segs)
    sc = (W - left - 20 - gap * (len(segs) - 1)) / total

    def gx(t):
        x = left
        for a, b in segs:
            if t <= b:
                return x + max(0, t - a) * sc
            x += (b - a) * sc + gap
        return x
    f, fs = font(18), font(15)
    for a, b in segs:
        t = math.ceil(a / 3600) * 3600
        while t <= b:
            x = gx(t); d.line([x, top - 6, x, H - 10], fill="#ddd"); d.text((x + 3, 10), __import__("datetime").datetime.fromtimestamp(t).strftime("%H:%M"), fill="#555", font=fs)
            t += 7200
    colors = {dr["id"]: dr.get("color") or PALETTE[i % len(PALETTE)] for i, dr in enumerate(ds)}
    for i, dr in enumerate(ds):
        y = top + i * rowH
        d.text((8, y + 6), dr["id"], fill="black", font=f)
        for s in so:
            if s["drone_id"] == dr["id"]:
                d.rounded_rectangle([gx(s["t_takeoff"]), y + 4, max(gx(s["t_landing"]), gx(s["t_takeoff"]) + 2), y + rowH - 6], radius=3, fill=colors[dr["id"]])
    img.save(out)


if __name__ == "__main__":
    plan = json.load(open(sys.argv[1], encoding="utf-8"))
    scen = json.load(open(sys.argv[2], encoding="utf-8"))
    render_map(plan, scen, sys.argv[3])
    if len(sys.argv) > 4:
        render_gantt(plan, sys.argv[4])
    if len(sys.argv) > 5:
        render_map(plan, scen, sys.argv[5], transparent=True)
