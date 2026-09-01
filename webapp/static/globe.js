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
  // Sites sit on a small ring of true longitude and latitude around the
  // region, so they turn with the globe like anything else on the surface.
  const ringDeg = 4.2;
  const sitePts = sites.map((st, i) => {
    const a = sites.length === 1 ? -Math.PI / 2
      : (i / sites.length) * Math.PI * 2 - Math.PI / 2;
    const lat = hit.lat + Math.sin(a) * ringDeg;
    const lon = hit.lon + Math.cos(a) * ringDeg / Math.max(0.25, Math.cos(hit.lat * RAD));
    return { st, ...project(lon, lat, lon0, lat0, r, cx, cy) };
  });

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
    ${pts.filter((p) => p.front > 0).map((p) => `
      <circle class="mk" data-region="${esc(p.region)}"
        cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${radius(p.capacityUnits).toFixed(1)}"
        fill="${MAP_TONE_FILL[mapTone(p)]}" fill-opacity=".9" stroke="#fff"
        tabindex="0" role="button"
        aria-label="${esc(p.region)}, ${p.utilisation}% used">
        <title>${esc(p.region)} — ${p.utilisation}% used</title>
      </circle>`).join("")}
    ${sitePts.filter((s) => s.front > 0).map((s) => `
      <line x1="${hit.x.toFixed(1)}" y1="${hit.y.toFixed(1)}"
        x2="${s.x.toFixed(1)}" y2="${s.y.toFixed(1)}"
        stroke="var(--globe-rim)" stroke-width=".8" opacity=".55"/>
      <circle class="site-mk${s.st.overThreshold ? " full" : ""}"
        data-dc="${esc(s.st.datacentre)}" cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="6"
        fill="${s.st.overThreshold ? MAP_TONE_FILL.bad : MAP_TONE_FILL.good}"
        fill-opacity=".95" stroke="#fff" tabindex="0" role="button"
        aria-label="${esc(s.st.datacentre)}, ${s.st.utilisationPct}% of its own ${s.st.thresholdPct}% line">
        <title>${esc(s.st.datacentre)} — ${s.st.utilisationPct}% of its own ${s.st.thresholdPct}% line, ${num(s.st.capacityUnits)} CU</title>
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
