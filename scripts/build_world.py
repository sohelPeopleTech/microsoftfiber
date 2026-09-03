"""Turn Natural Earth 110m country polygons into a compact inline-SVG world.

Natural Earth is public domain. The output is one SVG path string per landmass
in equirectangular projection, checked into the repo so the app needs no CDN and
no build step -- the same reason every other chart here is hand-authored.
"""
import json
import ssl
import urllib.request

import certifi

SRC = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
       "master/geojson/ne_110m_admin_0_countries.geojson")

ctx = ssl.create_default_context(cafile=certifi.where())
with urllib.request.urlopen(SRC, timeout=60, context=ctx) as fh:
    gj = json.load(fh)

print(f"source: {len(gj['features'])} countries")

# Equirectangular. x spans 0..360 from -180, y spans 0..180 from +90 down.
# Keeping the viewBox in degrees means a region's lat/lon needs no conversion
# beyond a subtraction, which keeps the marker maths readable in the browser.
DROP = {"Antarctica"}
MIN_AREA_DEG2 = 0.6      # shoelace area; drops specks that cost bytes and show nothing
PRECISION = 1            # ~11 km at the equator, far below one screen pixel


def ring_area(ring):
    a = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


#: Douglas-Peucker tolerance in degrees. The map renders about 1000px wide for
#: 360 degrees, so 0.4 deg is roughly one screen pixel -- detail below it costs
#: bytes and cannot be seen.
TOLERANCE = 0.4


def simplify(pts, tol):
    """Douglas-Peucker, iterative so a long coastline cannot blow the stack."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        x1, y1 = pts[lo]
        x2, y2 = pts[hi]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5
        worst, at = -1.0, lo
        for i in range(lo + 1, hi):
            px, py = pts[i]
            if norm == 0:
                d = ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
            else:
                d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm
            if d > worst:
                worst, at = d, i
        if worst > tol:
            keep[at] = True
            stack.append((lo, at))
            stack.append((at, hi))
    return [p for p, k in zip(pts, keep) if k]


def emit(ring):
    pts = [(lon + 180.0, 90.0 - lat) for lon, lat in ring]
    pts = simplify(pts, TOLERANCE)
    out, last = [], None
    for x, y in pts:
        x, y = round(x, PRECISION), round(y, PRECISION)
        if (x, y) == last:      # collapse duplicates created by rounding
            continue
        last = (x, y)
        out.append(f"{x:g},{y:g}")
    if len(out) < 3:
        return None
    return "M" + "L".join(out) + "Z"


paths, kept, dropped = [], 0, 0
for feat in gj["features"]:
    name = feat["properties"].get("NAME") or ""
    if name in DROP:
        continue
    geom = feat.get("geometry") or {}
    polys = ([geom["coordinates"]] if geom.get("type") == "Polygon"
             else geom.get("coordinates", []))
    for poly in polys:
        outer = poly[0]
        if ring_area(outer) < MIN_AREA_DEG2:
            dropped += 1
            continue
        d = emit(outer)
        if d:
            paths.append(d)
            kept += 1

blob = "".join(paths)
print(f"kept {kept} rings, dropped {dropped} specks")
print(f"path data: {len(blob) / 1024:.1f} KB")

out = ("/Users/yaswanthg/fabric-capacity-intelligence/webapp/static/world.js")
with open(out, "w") as fh:
    fh.write(
        "/* World coastlines for the capacity map.\n"
        "\n"
        "   Natural Earth 1:110m admin-0 country polygons, public domain, outer\n"
        "   rings only, simplified to one decimal degree and projected\n"
        "   equirectangular into a 360x180 viewBox. Checked in rather than\n"
        "   fetched so the app keeps working without a CDN, which is the same\n"
        "   reason every chart in this project is hand-authored SVG.\n"
        "\n"
        "   Because the viewBox is degrees, a region marker is placed with\n"
        "   x = lon + 180, y = 90 - lat and no projection code in between.\n"
        "\n"
        "   Regenerate with scripts/build_world.py. */\n"
        f"const WORLD_PATH = \"{blob}\";\n"
        "\n"
        "/* Full extent is 360x180. The map is drawn cropped: Antarctica is gone\n"
        "   and there are no capacity pools above the Arctic circle, so a third of\n"
        "   the full frame is empty ocean that only shrinks everything else. */\n"
        "const WORLD_VIEWBOX = { x: 0, y: 16, w: 360, h: 132 };\n"
    )
print(f"wrote {out}")
