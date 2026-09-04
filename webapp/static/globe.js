/* An orthographic globe, hand-authored, with no library behind it.

   Review asked for the map to be three-dimensional: regions on a globe, and
   clicking one turns the globe to it and shows that region's data centres.

   The usual way to do this is three.js or globe.gl, which would be the first
   external script in an application that has none, and the first vendored
   front-end dependency in a project whose thin-dependency principle has
   already been broken once and had to be flagged to review. It is not needed.
   An orthographic projection is six lines of trigonometry, and the coastline
   data is already checked in.

   WHY THE EXISTING PATH CAN BE REUSED
       world.js holds Natural Earth coastlines pre-projected equirectangular
       into a 360x180 box. That projection is x = lon + 180, y = 90 - lat, and
       it inverts exactly -- so the path parses straight back to longitude and
       latitude and re-projects onto a sphere. No new data, no fetch.

   WHAT MAKES IT READ AS A SPHERE
       Three things, none of them a 3D engine: the near hemisphere is clipped
       at the horizon so the far side genuinely is not drawn, a radial gradient
       lights it from the upper left, and a graticule curves with the surface.
       Land that crosses the limb is cut at the horizon rather than dropped, or
       continents would flicker whole as the globe turns.

   https://en.wikipedia.org/wiki/Orthographic_map_projection
*/

const RAD = Math.PI / 180;

/* Simplified India mainland outline, in longitude/latitude. The map uses
   Azure region names rather than country names, so centralindia is the
   region that owns this geography. */
const INDIA_BOUNDARY = [
  [68.1, 23.7], [68.8, 22.0], [70.2, 20.5], [72.0, 20.0],
  [72.8, 18.7], [73.5, 16.0], [74.2, 13.0], [75.4, 10.5],
  [77.0, 8.3], [78.5, 8.2], [79.5, 10.4], [80.5, 12.2],
  [80.3, 14.4], [81.4, 16.0], [83.0, 17.6], [84.8, 18.5],
  [86.5, 19.8], [88.0, 21.4], [89.8, 22.2], [88.7, 24.0],
  [89.8, 25.3], [88.2, 26.5], [88.2, 27.7], [86.7, 28.8],
  [84.5, 28.4], [82.5, 29.8], [80.5, 30.5], [78.5, 30.8],
  [76.5, 32.0], [74.5, 31.2], [72.5, 30.0], [70.7, 28.0],
  [69.4, 26.0], [68.1, 23.7],
];

/* The coastlines as longitude/latitude rings, parsed once.

   Kept lazily rather than at load: the map is one screen of twelve, and
   parsing four thousand points for a reader who never opens it is work nobody
   asked for. */
let GLOBE_RINGS = null;

function globeRings() {
  if (GLOBE_RINGS) return GLOBE_RINGS;
  GLOBE_RINGS = WORLD_PATH.split("M").slice(1).map((sub) =>
    sub.replace(/Z\s*$/, "").split("L").map((pair) => {
      const [x, y] = pair.split(",").map(Number);
      return [x - 180, 90 - y];          // exactly inverts the flat projection
    }).filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1])));
  return GLOBE_RINGS;
}

/* One point on the sphere, seen from a viewer above (lon0, lat0).

   `front` is the sign of the cosine of angular distance from the centre: at or
   below zero the point is round the back and must not be drawn. */
function project(lon, lat, lon0, lat0, r, cx, cy) {
  const p = lat * RAD, l = (lon - lon0) * RAD;
  const p0 = lat0 * RAD;
  const cosc = Math.sin(p0) * Math.sin(p) + Math.cos(p0) * Math.cos(p) * Math.cos(l);
  return {
    x: cx + r * Math.cos(p) * Math.sin(l),
    // Screen y grows downward, the sphere's does not, hence the subtraction.
    y: cy - r * (Math.cos(p0) * Math.sin(p) - Math.sin(p0) * Math.cos(p) * Math.cos(l)),
    front: cosc,
  };
}

/* Where an edge crosses the horizon, by bisection.

   Solving it exactly means intersecting a great circle with the limb; eight
   halvings put the cut inside a thousandth of a degree, which no reader can
   see and no continent needs. */
function horizonCut(a, b, lon0, lat0, r, cx, cy) {
  let lo = 0, hi = 1;
  for (let i = 0; i < 8; i += 1) {
    const mid = (lo + hi) / 2;
    const p = project(a[0] + (b[0] - a[0]) * mid, a[1] + (b[1] - a[1]) * mid,
                      lon0, lat0, r, cx, cy);
    if (p.front > 0) lo = mid; else hi = mid;
  }
  return project(a[0] + (b[0] - a[0]) * lo, a[1] + (b[1] - a[1]) * lo,
                 lon0, lat0, r, cx, cy);
}

/* One coastline ring, clipped to the visible hemisphere.

   Rings that straddle the limb are cut at it rather than discarded. Dropping
   them makes continents blink out whole as the globe turns, which reads as a
   rendering fault rather than as a horizon. */
function ringPath(ring, lon0, lat0, r, cx, cy) {
  const out = [];
  let run = [];
  for (let i = 0; i < ring.length; i += 1) {
    const a = ring[i], b = ring[(i + 1) % ring.length];
    const pa = project(a[0], a[1], lon0, lat0, r, cx, cy);
    const pb = project(b[0], b[1], lon0, lat0, r, cx, cy);
    if (pa.front > 0) run.push(pa);
    if ((pa.front > 0) !== (pb.front > 0)) {
      run.push(horizonCut(pa.front > 0 ? a : b, pa.front > 0 ? b : a,
                          lon0, lat0, r, cx, cy));
      if (run.length > 2) out.push(run);
      run = [];
    }
  }
  if (run.length > 2) out.push(run);
  return out.map((seg) =>
    seg.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join("") + "Z"
  ).join("");
}

/* Meridians and parallels, so the surface curves rather than sits flat. */
function graticule(lon0, lat0, r, cx, cy) {
  const seg = [];
  for (let lon = -180; lon < 180; lon += 30) {
    const pts = [];
    for (let lat = -90; lat <= 90; lat += 3) pts.push([lon, lat]);
    seg.push(pts);
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    const pts = [];
    for (let lon = -180; lon <= 180; lon += 3) pts.push([lon, lat]);
    seg.push(pts);
  }
  return seg.map((pts) => {
    let d = "", pen = false;
    pts.forEach(([lon, lat]) => {
      const p = project(lon, lat, lon0, lat0, r, cx, cy);
      if (p.front <= 0) { pen = false; return; }
      d += `${pen ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`;
      pen = true;
    });
    return d;
  }).join("");
}

/* A tie line from a region to one of its sites, stopping short at both ends.

   Drawn centre to centre, ten of these converge on one point and render as a
   black starburst that buries the region marker underneath it -- which reads
   as a drawing fault rather than as ten sites. Starting each line outside the
   region marker and stopping it before the site leaves both visible. */
function spoke(s, hit, markerR) {
  const dx = s.x - hit.x, dy = s.y - hit.y;
  const len = Math.hypot(dx, dy) || 1;
  const near = Math.min(markerR + 4, len * 0.42);
  const far = Math.min(9, len * 0.42);
  return `x1="${(hit.x + (dx / len) * near).toFixed(1)}" `
       + `y1="${(hit.y + (dy / len) * near).toFixed(1)}" `
       + `x2="${(s.x - (dx / len) * far).toFixed(1)}" `
       + `y2="${(s.y - (dy / len) * far).toFixed(1)}"`;
}

/* The globe, with regions on it and optionally one region's data centres.

   `view` carries where the viewer is (lon0, lat0) and how far in (zoom), so
   the caller owns the animation and this stays a pure render. */
function globeMap(d, view, focus) {
  const S = 560, cx = S / 2, cy = S / 2;
  const base = S * 0.44;
  const r = base * (view.zoom || 1);
  const { lon0, lat0 } = view;

  const pts = d.points
    .filter((p) => p.lat != null && p.lon != null)
    .map((p) => ({ ...p, ...project(p.lon, p.lat, lon0, lat0, r, cx, cy) }));
  const maxUnits = Math.max(...pts.map((p) => p.capacityUnits), 1);
  const radius = (u) => 4 + 9 * Math.sqrt(u / maxUnits);

  const hit = focus && pts.find((p) => p.region === focus.region);
  const sites = hit && focus.sites ? focus.sites : [];
  /* Each site now carries a real latitude and longitude -- its region's
     published point plus a small generated offset, computed once in the
     dimensional model (attach_datacentre_coordinates). So the placement here is
     the true bearing and relative spacing of those coordinates, not a hash of
     the name.

     The one liberty taken is scale. A data centre sits within ~75 km of its
     region's point, which is under a degree, and a globe cannot resolve a
     degree without zooming past the point of a globe -- ten markers would stack
     on the region dot. So the offset from the region is magnified by a constant
     before it is drawn: which site is north-east of which, and which two are
     close together, are faithful to the coordinates; the absolute distance from
     the region marker is not. The dotted fence is a reminder that the cluster
     is schematic.

     Sites with no coordinate (should not happen, but the model can return null
     for a region missing from the geography table) fall back to a deterministic
     golden-angle scatter so the marker still lands somewhere stable. */
  const GOLDEN = Math.PI * (3 - Math.sqrt(5));
  const siteMax = Math.max(...sites.map((st) => st.capacityUnits || 0), 1);
  //: Real within-region offsets are sub-degree; this scales them up until the
  //: cluster reads on a globe. Lower than it once was because a region now
  //: zooms much closer, so less magnification covers the same screen distance.
  const SITE_SPREAD_MAG = 5;

  function jitter(name, salt) {
    // A small deterministic hash. Not cryptographic -- it only has to be stable
    // between redraws and different between neighbouring site names.
    let h = 2166136261 ^ salt;
    for (let i = 0; i < name.length; i++) {
      h = Math.imul(h ^ name.charCodeAt(i), 16777619);
    }
    return ((h >>> 0) % 10000) / 10000;
  }

  const sitePts = sites.map((st, i) => {
    const name = String(st.datacentre || i);
    let lat, lon, ringDeg;
    if (st.lat != null && st.lon != null) {
      // True offset from the region, magnified so it is visible.
      const dLat = (st.lat - hit.lat) * SITE_SPREAD_MAG;
      const dLon = (st.lon - hit.lon) * SITE_SPREAD_MAG;
      lat = hit.lat + dLat;
      lon = hit.lon + dLon;
      ringDeg = Math.hypot(dLat, dLon * Math.cos(hit.lat * RAD));
    } else {
      const base = sites.length === 1 ? -Math.PI / 2 : i * GOLDEN - Math.PI / 2;
      // Up to a third of the gap between neighbours, so ordering is preserved.
      const a = base + (jitter(name, 1) - 0.5) * (Math.PI * 2 / Math.max(sites.length, 3)) * 0.66;
      const big = Math.sqrt((st.capacityUnits || 0) / siteMax);
      ringDeg = 2.6 + (1 - big) * 2.4 + jitter(name, 2) * 1.6;
      lat = hit.lat + Math.sin(a) * ringDeg;
      lon = hit.lon + Math.cos(a) * ringDeg / Math.max(0.25, Math.cos(hit.lat * RAD));
    }
    return { st, ringDeg, ...project(lon, lat, lon0, lat0, r, cx, cy) };
  });

  /* A filled boundary enclosing the selected marker and its sites.

     The old dotted circle described a radius around the cluster, but it did
     not read as a selected geography. A padded convex hull gives the selected
     region a country-like silhouette that follows the actual site spread and
     stays stable while the globe rotates. */
  const boundary = (() => {
    if (hit && focus.region === "centralindia") {
      const points = INDIA_BOUNDARY.map(([lon, lat]) => {
        const point = project(lon, lat, lon0, lat0, r, cx, cy);
        return `${point.x.toFixed(1)},${point.y.toFixed(1)}`;
      }).join(" ");
      return `<polygon class="region-fence country-fence" points="${points}"
        fill="var(--brand)" fill-opacity=".14"
        stroke="var(--brand)" stroke-width="1.8" stroke-dasharray="6 5"
        stroke-linejoin="round" opacity=".92" pointer-events="none"/>`;
    }
    if (!hit || !sitePts.length) return "";
    const source = [{ x: hit.x, y: hit.y }, ...sitePts.map((s) => ({ x: s.x, y: s.y }))];
    const sorted = source.slice().sort((a, b) => a.x - b.x || a.y - b.y);
    const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
    const lower = [];
    sorted.forEach((p) => {
      while (lower.length > 1 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
      lower.push(p);
    });
    const upper = [];
    sorted.slice().reverse().forEach((p) => {
      while (upper.length > 1 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
      upper.push(p);
    });
    const hull = lower.slice(0, -1).concat(upper.slice(0, -1));
    if (hull.length < 3) return "";
    const centre = hull.reduce((sum, p) => ({
      x: sum.x + p.x / hull.length, y: sum.y + p.y / hull.length,
    }), { x: 0, y: 0 });
    const padded = hull.map((p) => {
      const dx = p.x - centre.x, dy = p.y - centre.y;
      const length = Math.hypot(dx, dy) || 1;
      return `${(p.x + dx / length * 14).toFixed(1)},${(p.y + dy / length * 14).toFixed(1)}`;
    });
    return `<polygon class="region-fence" points="${padded.join(" ")}"
      fill="var(--brand)" fill-opacity=".13"
      stroke="var(--brand)" stroke-width="1.8" stroke-dasharray="6 5"
      stroke-linejoin="round" opacity=".92" pointer-events="none"/>`;
  })();

  return `<svg class="chart globe" viewBox="0 0 ${S} ${S}" role="img"
      aria-label="${hit ? `Data centres in ${esc(focus.region)}, on a globe`
                        : "Capacity by region, on a globe"}">
    <defs>
      <radialGradient id="globe-lit" cx="34%" cy="30%" r="78%">
        <stop offset="0%" stop-color="var(--globe-hi)"/>
        <stop offset="62%" stop-color="var(--globe-mid)"/>
        <stop offset="100%" stop-color="var(--globe-lo)"/>
      </radialGradient>
      <clipPath id="globe-clip"><circle cx="${cx}" cy="${cy}" r="${r.toFixed(1)}"/></clipPath>
    </defs>
    <circle cx="${cx}" cy="${cy}" r="${r.toFixed(1)}" fill="url(#globe-lit)"/>
    <g clip-path="url(#globe-clip)">
      <path d="${graticule(lon0, lat0, r, cx, cy)}" fill="none"
        stroke="var(--globe-grat)" stroke-width=".6"/>
      <path d="${globeRings().map((ring) => ringPath(ring, lon0, lat0, r, cx, cy)).join("")}"
        fill="var(--globe-land)" stroke="var(--globe-edge)" stroke-width=".5"/>
    </g>
    <circle cx="${cx}" cy="${cy}" r="${r.toFixed(1)}" fill="none"
      stroke="var(--globe-rim)" stroke-width="1"/>
    ${boundary}
    ${pts.filter((p) => p.front > 0).map((p) => `
      <circle class="mk" data-region="${esc(p.region)}"
        cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${radius(p.capacityUnits).toFixed(1)}"
        fill="${MAP_TONE_FILL[mapTone(p)]}" fill-opacity=".9" stroke="#fff"
        tabindex="0" role="button"
        aria-label="${esc(p.region)}, ${p.utilisation}% used${
          p.lat != null ? `, at ${p.lat.toFixed(2)}, ${p.lon.toFixed(2)}` : ""}">
      </circle>`).join("")}
    ${sitePts.filter((s) => s.front > 0).map((s) => `
      <line ${spoke(s, hit, radius(hit.capacityUnits))}
        stroke="var(--globe-rim)" stroke-width=".8" opacity=".4"/>
      <circle class="site-mk${s.st.overThreshold ? " full" : ""}"
        data-dc="${esc(s.st.datacentre)}" cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="6"
        fill="${s.st.overThreshold ? MAP_TONE_FILL.bad : MAP_TONE_FILL.good}"
        fill-opacity=".95" stroke="#fff" tabindex="0" role="button"
        aria-label="${esc(s.st.datacentre)}, ${s.st.utilisationPct}% of its own ${s.st.thresholdPct}% line, ${num(s.st.capacityUnits)} CU${
          s.st.lat != null ? `, at ${s.st.lat.toFixed(2)}, ${s.st.lon.toFixed(2)}` : ""}">
      </circle>
      <text x="${s.x.toFixed(1)}" y="${(s.y + 15).toFixed(1)}" text-anchor="middle"
        font-size="9" fill="var(--ink-2)" style="pointer-events:none">
        ${esc(s.st.datacentre.split("-").pop())}</text>`).join("")}
  </svg>`;
}

/* Turn the globe to a point, and wind in or out, over about half a second.

   Longitude is taken the short way round so a jump from Tokyo to Seattle
   spins across the Pacific rather than back over Europe. */
function spinTo(from, to, ms, onFrame) {
  let dLon = ((to.lon0 - from.lon0 + 540) % 360) - 180;
  const t0 = performance.now();
  function step(now) {
    const t = Math.min(1, (now - t0) / ms);
    const e = t < 0.5 ? 2 * t * t : 1 - ((-2 * t + 2) ** 2) / 2;   // ease in-out
    onFrame({
      lon0: from.lon0 + dLon * e,
      lat0: from.lat0 + (to.lat0 - from.lat0) * e,
      zoom: from.zoom + (to.zoom - from.zoom) * e,
    });
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
