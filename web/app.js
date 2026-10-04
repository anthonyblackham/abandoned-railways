import { Railway, localClock, formatTime } from "./engine.js";

const params = new URLSearchParams(location.search);
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
const $ = id => document.getElementById(id);
const el = (tag, props = {}, html = "") => Object.assign(document.createElement(tag), props, html ? { innerHTML: html } : {});

// ---- data ------------------------------------------------------------------
// Every line loads up front (a few dozen lines at ~25 KB each is fine) and
// gets its own engine. Each runs its featured timetable unless a line view
// picks another.

const index = await (await fetch("data/index.json")).json();
const lines = await Promise.all(index.map(async entry => {
  const engine = new Railway(await (await fetch(`data/${entry.id}.json`)).json());
  return { id: entry.id, entry, engine, data: engine.data, era: entry.featured || engine.data.eras[0].id, current: [] };
}));
const byId = Object.fromEntries(lines.map(l => [l.id, l]));
let selected = byId[params.get("railway")] ?? null;   // null = the whole network
if (selected && params.has("era") && selected.data.eras.some(e => e.id === params.get("era"))) {
  selected.era = params.get("era");
}

// One shared clock on local time. All lines so far are in the same zone.
const tz = lines[0].data.timezone;
const clock = { live: true, speed: 1, ...localClock(tz) };
if (params.has("t")) {
  const [h, m = 0] = params.get("t").split(":").map(Number);
  Object.assign(clock, { live: false, seconds: h * 3600 + m * 60 });
}
const zoneName = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "longGeneric" })
  .formatToParts().find(p => p.type === "timeZoneName").value;
const years = d => d.opened || d.closed ? `${d.opened.slice(0, 4)}–${d.closed.slice(0, 4)}` : "";

// ---- clock controls --------------------------------------------------------

$("scrub").oninput = () => { clock.live = false; clock.seconds = +$("scrub").value; syncButtons(); };
$("live").onclick = () => { clock.live = true; clock.speed = 1; syncButtons(); };
for (const b of document.querySelectorAll("[data-speed]")) {
  b.onclick = () => { clock.live = false; clock.speed = +b.dataset.speed; syncButtons(); };
}
function syncButtons() {
  $("live").classList.toggle("on", clock.live);
  for (const b of document.querySelectorAll("[data-speed]")) {
    b.classList.toggle("on", !clock.live && +b.dataset.speed === clock.speed);
  }
  $("mode").textContent = clock.live ? "now" : `replay ${clock.speed}×`;
}
syncButtons();

// ---- map -------------------------------------------------------------------

// The basemap is our Pioneer-style map; ?basemap=<name> picks another style
// from styles/, or ?basemap=openfreemap OpenFreeMap's plain one.
// Our styles keep their sprite and fonts next to the site, and MapLibre wants
// those as absolute URLs, so they're resolved against this page first.
const BASEMAP = params.get("basemap") ?? "pioneer";
const OWN_STYLE = BASEMAP !== "openfreemap";
async function basemapStyle() {
  if (!OWN_STYLE) return `https://tiles.openfreemap.org/styles/${dark ? "dark" : "positron"}`;
  const style = await (await fetch(`styles/${BASEMAP}.json`)).json();
  const absolute = path => new URL(path, location.href).href.replace(/%7B/g, "{").replace(/%7D/g, "}");
  if (style.sprite && !/^https?:/.test(style.sprite)) style.sprite = absolute(style.sprite);
  if (style.glyphs && !/^https?:/.test(style.glyphs)) style.glyphs = absolute(style.glyphs);
  return style;
}

function boundsOf(ls) {
  const b = [Infinity, Infinity, -Infinity, -Infinity];
  for (const l of ls) {
    const [w, s, e, n] = l.data.bbox;
    b[0] = Math.min(b[0], w); b[1] = Math.min(b[1], s); b[2] = Math.max(b[2], e); b[3] = Math.max(b[3], n);
  }
  return [[b[0], b[1]], [b[2], b[3]]];
}

const map = new maplibregl.Map({
  container: "map",
  style: await basemapStyle(),
  bounds: boundsOf(selected ? [selected] : lines),
  fitBoundsOptions: { padding: 40 },
  hash: true,   // #zoom/lat/lng in the address bar, so a view can be shared
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");

// Our own styles carry their fonts (basemap/make_glyphs.py); OpenFreeMap's carry Noto Sans.
const FONT = OWN_STYLE ? ["Old Standard TT Regular"] : ["Noto Sans Regular"];
const FONT_BOLD = OWN_STYLE ? ["Old Standard TT Bold"] : ["Noto Sans Bold"];
// Every historic line is drawn alike, as a railway with cross-ties, so many
// lines read as one network rather than a rainbow. Each line's own colour
// marks its trains.
const HISTORIC = "#8b2e1f";
// Overlay colours follow the basemap, not the page: custom styles are light.
const darkMap = dark && !OWN_STYLE;
const ink = darkMap ? "#ece4d6" : "#2a2520";
const paper = darkMap ? "#1d1b18" : "#f6f1e6";
const MILEPOST = "#2f5d45";   // dark green: readable on parchment, distinct from stations

// A filled diamond with an outline, as image data for map.addImage.
function diamond(size, fill, outline) {
  const s = size * 2, c = document.createElement("canvas");
  c.width = c.height = s;
  const g = c.getContext("2d");
  g.beginPath();
  g.moveTo(s / 2, 2); g.lineTo(s - 2, s / 2); g.lineTo(s / 2, s - 2); g.lineTo(2, s / 2); g.closePath();
  g.fillStyle = fill; g.fill();
  g.lineWidth = 2.5; g.strokeStyle = outline; g.stroke();
  return g.getImageData(0, 0, s, s);
}

const fc = features => ({ type: "FeatureCollection", features });
const point = (coords, properties = {}) => ({ type: "Feature", properties, geometry: { type: "Point", coordinates: coords } });
// In a line view, everything belonging to other lines fades back.
const focus = (on, off) => selected ? ["case", ["==", ["get", "line"], selected.id], on, off] : on;

map.on("load", () => {
  // Overlays such as traced plats sit under everything the railways draw.
  for (const l of lines) {
    for (const o of l.data.overlays ?? []) {
      const id = `${l.id}:${o.id}`;
      map.addSource(id, { type: "raster", tiles: [o.tiles], tileSize: 256, maxzoom: o.maxzoom ?? 18 });
      map.addLayer({ id, type: "raster", source: id, layout: { visibility: "none" }, paint: { "raster-opacity": 0.85 } });
    }
  }

  map.addSource("tracks", { type: "geojson", data: fc(lines.map(l => ({
    type: "Feature", properties: { line: l.id }, geometry: { type: "LineString", coordinates: l.data.track } }))) });
  map.addLayer({ id: "track-casing", type: "line", source: "tracks", layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": paper, "line-opacity": 0.8, "line-width": ["interpolate", ["linear"], ["zoom"], 9, 5, 15, 16] } });
  map.addLayer({ id: "track-ties", type: "line", source: "tracks", minzoom: 11,
    paint: { "line-color": HISTORIC, "line-width": ["interpolate", ["linear"], ["zoom"], 11, 6, 15, 13],
      "line-dasharray": [0.18, 1.6] } });
  map.addLayer({ id: "track", type: "line", source: "tracks", layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": HISTORIC, "line-width": ["interpolate", ["linear"], ["zoom"], 9, 2, 15, 3.5] } });
  // A wide invisible line makes the tracks easy to click.
  map.addLayer({ id: "track-hit", type: "line", source: "tracks", paint: { "line-color": "#000", "line-opacity": 0, "line-width": 16 } });

  map.addSource("mileposts", { type: "geojson", data: fc([]) });
  map.addLayer({ id: "milepost-links", type: "line", source: "mileposts", filter: ["==", ["geometry-type"], "LineString"],
    layout: { visibility: "none" }, paint: { "line-color": MILEPOST, "line-width": 1.5, "line-dasharray": [2, 2] } });
  // Mileposts are diamonds, the plats' own milepost symbol, drawn here so any basemap works.
  map.addImage("milepost-diamond", diamond(14, MILEPOST, paper), { pixelRatio: 2 });
  map.addLayer({ id: "mileposts", type: "symbol", source: "mileposts", filter: ["==", ["geometry-type"], "Point"],
    layout: { visibility: "none", "icon-image": "milepost-diamond", "icon-allow-overlap": true } });
  map.addLayer({ id: "milepost-labels", type: "symbol", source: "mileposts", minzoom: 10,
    filter: ["==", ["geometry-type"], "Point"],
    layout: { visibility: "none", "text-field": ["concat", "MP ", ["get", "mile"]], "text-font": FONT_BOLD, "text-size": 12,
      // Whichever side of the marker is free, so labels clear nearby station names.
      "text-variable-anchor": ["bottom", "top", "right", "left"], "text-radial-offset": 0.7 },
    paint: { "text-color": MILEPOST, "text-halo-color": paper, "text-halo-width": 2 } });

  map.addSource("stations", { type: "geojson", data: fc([]) });
  // Zoomed out, only regular stops show; flag stops and stations without
  // passenger trains join from zoom 11.
  map.addLayer({ id: "stations", type: "circle", source: "stations", minzoom: 8,
    filter: ["any", ["get", "regular"], [">=", ["zoom"], 11]],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, ["case", ["get", "regular"], 3, 2], 12, ["case", ["get", "regular"], 5.5, 3.5]],
      "circle-color": paper,
      "circle-stroke-color": ["case", ["get", "served"], ink, "#8f877c"],
      "circle-stroke-width": ["case", ["get", "regular"], 2, 1.5],
    } });
  map.addLayer({ id: "station-labels", type: "symbol", source: "stations",
    minzoom: 10,
    filter: ["any", ["get", "regular"], [">=", ["zoom"], 12]],
    layout: { "text-field": ["get", "name"], "text-font": ["case", ["get", "regular"], ["literal", FONT_BOLD], ["literal", FONT]],
      "text-size": ["case", ["get", "regular"], 13, 11.5], "text-anchor": "left", "text-offset": [0.8, 0],
      "text-optional": true },
    paint: { "text-color": ["case", ["get", "served"], ink, "#7a7268"], "text-halo-color": paper, "text-halo-width": 1.5 } });

  map.addSource("trains", { type: "geojson", data: fc([]) });
  map.addLayer({ id: "trains", type: "circle", source: "trains",
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 7, 15, 12], "circle-color": ["get", "colour"],
      "circle-stroke-color": "#fff", "circle-stroke-width": 2.5 } });
  // Train numbers fit on the dot; longer labels (e.g. departure times) stay in the panel and popup.
  map.addLayer({ id: "train-labels", type: "symbol", source: "trains",
    filter: ["<=", ["length", ["get", "number"]], 3],
    layout: { "text-field": ["get", "number"], "text-font": FONT_BOLD, "text-size": 11, "text-allow-overlap": true,
      "text-ignore-placement": true },
    paint: { "text-color": "#fff" } });

  wirePopups();
  show(selected, { move: false });
  requestAnimationFrame(frame);
});

// ---- network and line views ------------------------------------------------

function show(line, { move = true } = {}) {
  selected = line;
  const q = new URLSearchParams(location.search);
  if (line) q.set("railway", line.id); else q.delete("railway");
  q.delete("era");
  history.replaceState(null, "", `?${q}${location.hash}`);

  $("network-view").hidden = !!line;
  $("line-view").hidden = !line;
  $("back").hidden = !line;
  $("era-pick").hidden = !line || line.data.eras.length < 2;
  $("feed").hidden = $("feed-sep").hidden = !line;
  if (line) {
    document.title = `${line.data.name} · Abandoned Railways`;
    $("name").textContent = line.data.name;
    $("years").textContent = [years(line.data), zoneName].filter(Boolean).join(" · ");
    $("era").replaceChildren(...line.data.eras.map(e => new Option(`${e.id}: ${e.label}`, e.id)));
    $("era").value = line.era;
    buildLayerToggles(line);
  } else {
    document.title = "Abandoned Railways";
    $("name").textContent = "Abandoned Railways";
    $("years").textContent = `${lines.length} lines · ${zoneName}`;
    $("source").textContent = "";
    hideLineLayers();
  }

  // Fade the other lines in a line view.
  for (const [layer, prop] of [["track", "line-opacity"], ["track-ties", "line-opacity"],
                               ["stations", "circle-opacity"], ["stations", "circle-stroke-opacity"],
                               ["trains", "circle-opacity"], ["trains", "circle-stroke-opacity"]]) {
    map.setPaintProperty(layer, prop, focus(1, 0.3));
  }
  map.setPaintProperty("station-labels", "text-opacity", focus(1, 0.35));
  map.setPaintProperty("train-labels", "text-opacity", focus(1, 0.35));

  drawStations();
  if (move) map.fitBounds(boundsOf(line ? [line] : lines), { padding: 40, duration: 800 });
  lastPanelSecond = -1;
}

$("back").onclick = () => show(null);
$("era").onchange = () => { selected.era = $("era").value; drawStations(); buildLayerToggles(selected); lastPanelSecond = -1; };

function hideLineLayers() {
  $("layers").replaceChildren();
  for (const l of lines) for (const o of l.data.overlays ?? []) map.setLayoutProperty(`${l.id}:${o.id}`, "visibility", "none");
  for (const id of ["mileposts", "milepost-links", "milepost-labels"]) map.setLayoutProperty(id, "visibility", "none");
}

// Layers belong to the open line: its overlays, and the mileposts of its timetable.
function buildLayerToggles(line) {
  hideLineLayers();
  const era = line.engine.era(line.era);
  const groups = (line.data.overlays ?? []).map(o => ({ label: o.label, layers: [`${line.id}:${o.id}`] }));
  if (era.mileposts?.length) {
    groups.push({ label: "Mileposts", layers: ["mileposts", "milepost-links", "milepost-labels"] });
  }
  for (const g of groups) {
    const box = el("input", { type: "checkbox" });
    box.onchange = () => g.layers.forEach(id => map.setLayoutProperty(id, "visibility", box.checked ? "visible" : "none"));
    const label = el("label");
    label.append(box, " ", g.label);
    $("layers").append(label);
  }
  if (!groups.length) $("layers").append(el("p", { className: "muted", textContent: "No extra layers for this line." }));

  // Whole-mile markers along the track (MP 0, MP 1, ...).
  map.getSource("mileposts").setData(fc((era.mileposts ?? []).map(p => point([p.lon, p.lat], { mile: String(p.mile) }))));
  $("source").textContent = `Timetable: ${era.source}`;
  $("feed").href = era.feed;
}

// Every station on each line. Ones the current timetable doesn't serve
// (freight-only stops, or stops from another era) are drawn muted.
function drawStations() {
  const features = [];
  for (const l of lines) {
    const served = new Map();   // station index -> regular stop on any train?
    for (const trip of l.engine.era(l.era).trips) {
      for (const [i, , flag] of trip.stops) served.set(i, (served.get(i) ?? false) || flag === 0);
    }
    l.engine.stations.forEach((s, i) => {
      features.push(point([s.lon, s.lat], { line: l.id, index: i, name: s.name, served: served.has(i),
        regular: served.get(i) ?? false, plat: s.plat ?? "", mp: s.mp ?? "", note: s.note ?? "" }));
    });
  }
  map.getSource("stations").setData(fc(features));
}

// ---- animation -------------------------------------------------------------

let lastFrame = performance.now(), lastPanelSecond = -1;

function frame(now) {
  const dt = (now - lastFrame) / 1000;
  lastFrame = now;
  if (clock.live) Object.assign(clock, localClock(tz));
  else clock.seconds = (clock.seconds + dt * clock.speed) % 86400;

  const trains = [];
  for (const l of lines) {
    l.current = l.engine.trainsAt(l.era, clock.seconds, clock.weekday);
    for (const p of l.current) {
      trains.push(point(p.lngLat, { line: l.id, number: p.trip.number, colour: l.data.colour }));
    }
  }
  map.getSource("trains").setData(fc(trains));

  const whole = Math.floor(clock.seconds);
  if (whole !== lastPanelSecond) {
    lastPanelSecond = whole;
    drawPanel();
  }
  requestAnimationFrame(frame);
}

const inText = wait => {
  const mins = Math.round(wait / 60);
  return mins < 60 ? `in ${mins} min` : `in ${Math.floor(mins / 60)} h ${mins % 60} min`;
};
const badge = (number, colour) => `<span class="num" style="background:${colour}">${number}</span>`;
const empty = text => el("li", { className: "empty", textContent: text });

// The panel redraws every second, but a list is only rebuilt when its
// content changes; rebuilding under the pointer would swallow clicks.
function setList(ul, items) {
  const sig = items.map(i => i.outerHTML).join("");
  if (ul.dataset.sig === sig) return;
  ul.dataset.sig = sig;
  ul.replaceChildren(...items);
}

function drawPanel() {
  $("time").textContent = formatTime(clock.seconds, true);
  if (document.activeElement !== $("scrub")) $("scrub").value = Math.floor(clock.seconds);
  if (selected) drawLinePanel(selected); else drawNetworkPanel();
}

function drawNetworkPanel() {
  setList($("lines"), lines.map(l => {
    const n = l.current.length;
    const next = l.engine.departures(l.era, clock.seconds, 1)[0];
    const status = n ? `${n} train${n > 1 ? "s" : ""} running`
      : next ? `next train ${formatTime(next.start)}` : "no trains in this timetable";
    const li = el("li", {}, `<span class="swatch" style="background:${l.data.colour}"></span>${l.data.name}` +
      `<span class="sub">${[years(l.data), status].filter(Boolean).join(" · ")}</span>`);
    li.onclick = () => show(l);
    return li;
  }));

  const deps = lines.flatMap(l => l.engine.departures(l.era, clock.seconds, 5).map(d => ({ ...d, line: l })))
    .sort((a, b) => a.wait - b.wait).slice(0, 6);
  setList($("network-upcoming"), (deps.length ? deps.map(d => {
    const li = el("li", {}, `${badge(d.trip.number, d.line.data.colour)}${formatTime(d.start)} ${d.from.name}` +
      `<span class="sub">to ${d.trip.headsign} · ${d.line.data.name} · ${inText(d.wait)}</span>`);
    li.onclick = () => map.flyTo({ center: [d.from.lon, d.from.lat], zoom: 13 });
    return li;
  }) : [empty("No departures.")]));
}

function drawLinePanel(l) {
  const colour = l.data.colour;
  setList($("running"), (l.current.length ? l.current.map(p => {
    const li = el("li", {}, `${badge(p.trip.number, colour)}to ${p.trip.headsign}` +
      (p.waiting ? `<span class="sub">waiting at ${p.previous.station.name} until ${formatTime(p.previous.time)}</span>`
        : `<span class="sub">next ${p.next.station.name} ${formatTime(p.next.time)}</span>`));
    // Look the train up when clicked: this item may be older than its position.
    li.onclick = () => map.flyTo({ center: (l.current.find(c => c.trip.number === p.trip.number) ?? p).lngLat, zoom: 14 });
    return li;
  }) : [empty("No trains running right now.")]));

  setList($("upcoming"), l.engine.departures(l.era, clock.seconds, 4).map(d => {
    const li = el("li", {}, `${badge(d.trip.number, colour)}${formatTime(d.start)} ${d.from.name}` +
      `<span class="sub">to ${d.trip.headsign} · ${inText(d.wait)}</span>`);
    li.onclick = () => map.flyTo({ center: [d.from.lon, d.from.lat], zoom: 13 });
    return li;
  }));
}

// ---- popups ----------------------------------------------------------------

function wirePopups() {
  const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "280px" });

  map.on("click", "stations", e => {
    const f = e.features[0];
    const l = byId[f.properties.line];
    const calls = l.engine.callsAt(l.era, f.properties.index);
    const nextIdx = calls.findIndex(c => c.time >= clock.seconds);
    if (!f.properties.served) {
      popup.setLngLat(f.geometry.coordinates).setHTML(
        `<div class="popup"><h3>${f.properties.name}</h3><p class="muted">${l.data.name}</p>` +
        `<p>${f.properties.note || "No passenger trains in this timetable."}</p></div>`).addTo(map);
      return;
    }
    const rows = calls.map((c, i) =>
      `<tr class="${i === nextIdx ? "next" : ""}"><td>${formatTime(c.time)}</td><td>No. ${c.trip.number}</td>` +
      `<td>to ${c.trip.headsign}</td><td class="est">${c.kind === "e" ? "est." : c.kind === "r" ? "recon." : ""}</td></tr>`).join("");
    popup.setLngLat(f.geometry.coordinates).setHTML(
      `<div class="popup"><h3>${f.properties.name}</h3>` +
      `<p class="muted">${l.data.name} · ${f.properties.regular ? "regular stop" : "flag stop"}` +
      `${f.properties.plat ? ` · plat Sta ${f.properties.plat}` : ""}${f.properties.mp ? ` · MP ${f.properties.mp}` : ""}</p>` +
      `<table>${rows}</table></div>`).addTo(map);
  });

  map.on("click", "trains", e => {
    const f = e.features[0];
    const l = byId[f.properties.line];
    const p = l.current.find(c => c.trip.number === f.properties.number);
    if (!p) return;
    popup.setLngLat(p.lngLat).setHTML(
      `<div class="popup"><h3>No. ${p.trip.number} to ${p.trip.headsign}</h3><p class="muted">${l.data.name}</p>` +
      (p.waiting ? `<p>Waiting at ${p.previous.station.name} until ${formatTime(p.previous.time)}</p>`
        : `<p>Left ${p.previous.station.name} ${formatTime(p.previous.time)}</p>` +
          `<p>Next ${p.next.station.name} ${formatTime(p.next.time)}</p>`) + `</div>`).addTo(map);
  });

  // Clicking a line (not one of its trains or stations) opens that line.
  map.on("click", "track-hit", e => {
    if (map.queryRenderedFeatures(e.point, { layers: ["stations", "trains"] }).length) return;
    const l = byId[e.features[0].properties.line];
    if (l && l !== selected) show(l);
  });

  for (const layer of ["stations", "trains", "track-hit"]) {
    map.on("mouseenter", layer, () => map.getCanvas().style.cursor = "pointer");
    map.on("mouseleave", layer, () => map.getCanvas().style.cursor = "");
  }
}
