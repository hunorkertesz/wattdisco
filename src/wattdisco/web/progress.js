// Progress across rides: fitness / fatigue / form, power curve, FTP history, power sources over weeks.
import { chipGroup, criticalPowerLine, el, renderStrip, zoomButton } from "/components.js";

const $ = (id) => document.getElementById(id);
renderStrip($("strip"), { active: "progress" });

const C = {
  cyan: "#3DD9FF", pink: "#FF3D81", yellow: "#FFC93C", teal: "#36E3C4", white: "#FFF6E5",
  muted: "#B6A9D4", line: "#2A1A44", lineStrong: "#3A2660",
};
const DAY = 86400e3;
const pref = (key, fallback) => { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } };
const keep = (key, value) => { try { localStorage.setItem(key, value); } catch { /* private window */ } };
const dayText = (d) => new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const signed = (v, digits = 1) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(digits);
const dur = (s) => (s < 60 ? `${s} s` : `${s / 60} min`);

function fit(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}
function font(ctx, size = 10) {
  ctx.font = `${size}px "Martian Mono", ui-monospace, monospace`;
  if ("letterSpacing" in ctx) ctx.letterSpacing = "0.5px";
}
function niceStep(range, lines) {
  for (const s of [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500]) if (range / s <= lines) return s;
  return 1000;
}
function glowLine(ctx, points, color, width = 2, dash = null) {
  if (points.length < 2) return;
  ctx.save();
  ctx.beginPath();
  points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  if (dash) ctx.setLineDash(dash);
  ctx.shadowColor = color;
  ctx.shadowBlur = dash ? 0 : 8;
  ctx.stroke();
  ctx.restore();
}
// y grid with labels on the left; returns the y mapping
function yAxis(ctx, { left, top, right, bottom, min, max, unit = "", lines = 4 }) {
  const step = niceStep(max - min || 1, lines);
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step || step;
  const y = (v) => bottom - ((v - lo) / (hi - lo || 1)) * (bottom - top);
  font(ctx);
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let v = lo; v <= hi + 1e-9; v += step) {
    ctx.strokeStyle = v === 0 ? C.lineStrong : C.line;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(left, Math.round(y(v)) + 0.5);
    ctx.lineTo(right, Math.round(y(v)) + 0.5);
    ctx.stroke();
    ctx.fillStyle = C.muted;
    ctx.fillText(`${Math.round(v * 10) / 10}${unit}`, left - 6, y(v));
  }
  return y;
}
// date labels along the bottom
function dateAxis(ctx, { x, from, to, bottom, maxLabels }) {
  const days = Math.max(1, Math.round((to - from) / DAY));
  const every = [1, 2, 7, 14, 28, 56, 91, 182, 365].find((d) => days / d <= maxLabels) ?? 365;
  font(ctx);
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillStyle = C.muted;
  for (let t = to; t >= from - 1; t -= every * DAY) {
    ctx.textAlign = t === to ? "right" : "center"; // the newest label ends at the right edge
    ctx.fillText(dayText(t), t === to ? x(t) + 8 : x(t), bottom + 6);
  }
}
// mouse over a chart: the nearest point's index
function onHover(canvas, xs, show) {
  canvas.onpointermove = (e) => {
    if (e.buttons) return; // dragging moves the chart
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left;
    let best = -1, dist = Infinity;
    xs().forEach((x, i) => { if (Math.abs(x - mx) < dist) { dist = Math.abs(x - mx); best = i; } });
    show(best >= 0 && dist < 40 ? best : null);
  };
  canvas.onpointerleave = () => { show(null); tip(canvas, null); };
}

// ---------- zoom, move and a readout on every chart ----------
// Each chart shows its range (the chips above, or 1 s–60 min for the power curve) unless the rider zooms in: wheel or
// pinch to zoom around the pointer, drag to move, double-click (or ↺) to see it all again. − and + do the same for
// touch screens. The nearest point's numbers show in a small box next to it.
const view = {}; // chart key → [from, to] while zoomed in (seconds on a log scale for the power curve)
const geo = new WeakMap(); // canvas → { left, right, base: () => [from, to], log, min }
const toLin = (v, log) => (log ? Math.log(v) : v);
const fromLin = (v, log) => (log ? Math.exp(v) : v);
function setView(key, canvas, next, redraw) {
  const g = geo.get(canvas);
  const [b0, b1] = g.base().map((v) => toLin(v, g.log));
  let [a, b] = next.map((v) => toLin(v, g.log));
  const minSpan = toLin(g.min, false) * (g.log ? 1 : 1);
  if (b - a < minSpan) { const mid = (a + b) / 2; a = mid - minSpan / 2; b = mid + minSpan / 2; }
  if (b - a >= b1 - b0) { delete view[key]; redraw(); return; }
  if (a < b0) { b += b0 - a; a = b0; }
  if (b > b1) { a -= b - b1; b = b1; }
  view[key] = [fromLin(a, g.log), fromLin(b, g.log)];
  redraw();
}
function domain(key, canvas, base, { log = false, min }) {
  const g = geo.get(canvas) ?? {};
  geo.set(canvas, { ...g, base, log, min });
  return view[key] ?? base();
}
function zoomBy(key, canvas, factor, at, redraw) {
  const g = geo.get(canvas);
  const [a, b] = (view[key] ?? g.base()).map((v) => toLin(v, g.log));
  const pivot = a + (at ?? 0.5) * (b - a);
  setView(key, canvas, [pivot - (pivot - a) * factor, pivot + (b - pivot) * factor].map((v) => fromLin(v, g.log)), redraw);
}
function interactive(key, canvas, redraw) {
  if (canvas.dataset.zoomable) return;
  canvas.dataset.zoomable = "1";
  const box = el("div", { class: "chart-box" });
  canvas.replaceWith(box);
  box.append(canvas,
    el("div", { class: "chart-zoom", role: "group", "aria-label": "Chart zoom" },
      el("button", { type: "button", "aria-label": "Zoom out", title: "Zoom out", text: "−", onclick: () => zoomBy(key, canvas, 1.6, null, redraw) }),
      el("button", { type: "button", "aria-label": "Zoom in", title: "Zoom in (or scroll / pinch on the chart)", text: "+", onclick: () => zoomBy(key, canvas, 0.6, null, redraw) }),
      el("button", { type: "button", "aria-label": "Show it all", title: "Show it all (or double-click the chart)", text: "↺", onclick: () => { delete view[key]; redraw(); } })),
    el("div", { class: "chart-tip", hidden: true }));
  const rel = (e) => {
    const r = canvas.getBoundingClientRect();
    const g = geo.get(canvas);
    return Math.min(1, Math.max(0, (e.clientX - r.left - g.left) / Math.max(1, g.right - g.left)));
  };
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    zoomBy(key, canvas, Math.exp(Math.max(-1, Math.min(1, e.deltaY / 200)) * 0.5), rel(e), redraw);
  }, { passive: false });
  let drag = null;
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button > 0) return;
    const g = geo.get(canvas);
    drag = { x: e.clientX, span: (view[key] ?? g.base()).map((v) => toLin(v, g.log)), moved: false };
    try { canvas.setPointerCapture(e.pointerId); } catch { /* not every pointer */ }
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const g = geo.get(canvas);
    const dx = e.clientX - drag.x;
    if (Math.abs(dx) > 3) drag.moved = true;
    if (!drag.moved) return;
    const [a, b] = drag.span;
    const shift = (-dx / Math.max(1, g.right - g.left)) * (b - a);
    setView(key, canvas, [a + shift, b + shift].map((v) => fromLin(v, g.log)), redraw);
  });
  const end = () => { if (drag?.moved) canvas.dataset.dragged = "1"; drag = null; };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("dblclick", () => { delete view[key]; redraw(); });
}
// the small box next to the nearest point
function tip(canvas, lines, px, py) {
  const t = canvas.parentElement?.querySelector(".chart-tip");
  if (!t) return;
  if (!lines) { t.hidden = true; return; }
  t.replaceChildren(...lines.map((l, i) => el(i ? "span" : "b", { text: l })));
  t.hidden = false;
  const w = canvas.clientWidth;
  t.style.left = `${Math.min(Math.max(4, px + 12), w - t.offsetWidth - 4)}px`;
  t.style.top = `${Math.max(2, py - t.offsetHeight - 10)}px`;
}
// clip what a chart draws to its plot area (a zoomed line runs past its edges)
function clip(ctx, left, top, right, bottom) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, top - 6, right - left, bottom - top + 12);
  ctx.clip();
}

// ---------- data ----------
const data = await (await fetch("/api/progress")).json();
const RANGES = [{ value: "42", label: "6 W" }, { value: "91", label: "3 M" }, { value: "365", label: "1 Y" }, { value: "all", label: "All" }];
let range = pref("wd.progress.range", "91");
const rangeGroup = chipGroup(RANGES, { value: range, label: "Time range",
  onChange: (v) => { range = v; keep("wd.progress.range", v); drawAll(); } });
$("range").replaceWith(rangeGroup);

const today = new Date(new Date().toDateString()).getTime();
function rangeFrom(first) {
  if (range === "all") return Math.min(first ?? today, today - 13 * DAY);
  return today - (Number(range) - 1) * DAY;
}
const at = (iso) => new Date(new Date(iso).toDateString()).getTime(); // local midnight

// ---------- fitness ----------
let fitHover = null;
function drawFitness() {
  const now = data.fitness.now;
  $("fit-now").textContent = now ? now.fitness.toFixed(0) : "--";
  $("fat-now").textContent = now ? now.fatigue.toFixed(0) : "--";
  $("form-now").textContent = now ? signed(now.form, 0) : "--";
  $("form-label").textContent = now ? now.label : "";
  $("fit-change").textContent = now ? `${signed(now.fitness_change_7d)} in 7 days` : "";

  const canvas = $("fit-chart");
  interactive("fit", canvas, drawFitness);
  const { ctx, w, h } = fit(canvas);
  const all = data.fitness.days.map((d) => ({ ...d, t: at(d.date) }));
  const [from, to] = domain("fit", canvas, () => [rangeFrom(all[0]?.t), today], { min: 7 * DAY });
  const days = all.filter((d) => d.t >= from - DAY && d.t <= to + DAY);
  const left = 40, right = w - 8, top = 8, bottom = h - 22;
  geo.get(canvas).left = left; geo.get(canvas).right = right;
  const x = (t) => left + ((t - from) / Math.max(DAY, to - from)) * (right - left);
  if (!days.length) {
    font(ctx, 11); ctx.fillStyle = C.muted; ctx.textAlign = "center";
    ctx.fillText("Ride to start your fitness line", w / 2, h / 2);
    return;
  }
  const vals = days.flatMap((d) => [d.fitness, d.fatigue, d.form]);
  const y = yAxis(ctx, { left, top, right, bottom, min: Math.min(0, ...vals), max: Math.max(10, ...vals) });
  dateAxis(ctx, { x, from, to, bottom, maxLabels: Math.max(2, Math.floor((right - left) / 70)) });
  clip(ctx, left, top, right, bottom);
  // daily load: faint bars along the bottom, on their own scale
  const maxLoad = Math.max(1, ...days.map((d) => d.load));
  const bw = Math.max(2, ((right - left) / Math.max(1, days.length)) * 0.6);
  ctx.fillStyle = "rgba(255, 246, 229, .16)";
  for (const d of days) if (d.load) {
    const bh = (d.load / maxLoad) * (bottom - top) * 0.35;
    ctx.fillRect(x(d.t) - bw / 2, bottom - bh, bw, bh);
  }
  // form as a soft area around zero
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(x(days[0].t), y(0));
  days.forEach((d) => ctx.lineTo(x(d.t), y(d.form)));
  ctx.lineTo(x(days.at(-1).t), y(0));
  ctx.fillStyle = "rgba(255, 201, 60, .14)";
  ctx.fill();
  ctx.restore();
  glowLine(ctx, days.map((d) => [x(d.t), y(d.form)]), C.yellow, 1.5);
  glowLine(ctx, days.map((d) => [x(d.t), y(d.fatigue)]), C.pink, 2);
  glowLine(ctx, days.map((d) => [x(d.t), y(d.fitness)]), C.cyan, 2.5);
  ctx.restore();
  if (fitHover != null && days[fitHover]) {
    const d = days[fitHover];
    ctx.strokeStyle = C.white; ctx.globalAlpha = .4;
    ctx.beginPath(); ctx.moveTo(x(d.t) + .5, top); ctx.lineTo(x(d.t) + .5, bottom); ctx.stroke();
    ctx.globalAlpha = 1;
    for (const [v, c] of [[d.fitness, C.cyan], [d.fatigue, C.pink], [d.form, C.yellow]]) {
      ctx.beginPath(); ctx.arc(x(d.t), y(v), 3.5, 0, 2 * Math.PI); ctx.fillStyle = c; ctx.fill();
    }
    $("fit-readout").textContent = `${dayText(d.t)} · load ${Math.round(d.load)} · fitness ${d.fitness.toFixed(1)} · fatigue ${d.fatigue.toFixed(1)} · form ${signed(d.form)}`;
    tip(canvas, [dayText(d.t), `Fitness ${d.fitness.toFixed(1)}`, `Fatigue ${d.fatigue.toFixed(1)}`, `Form ${signed(d.form)}`,
      d.load ? `Load that day ${Math.round(d.load)}` : "No ride that day"], x(d.t), y(Math.max(d.fitness, d.fatigue)));
  } else {
    $("fit-readout").textContent = "";
    tip(canvas, null);
  }
  onHover(canvas, () => days.map((d) => x(d.t)), (i) => { if (i !== fitHover) { fitHover = i; drawFitness(); } });
}

// ---------- power curve ----------
const CURVE_RANGES = [{ value: "all", label: "All" }, { value: "90", label: "90 d" }, { value: "28", label: "28 d" }];
let curveRange = pref("wd.progress.curve", "all");
const curveGroup = chipGroup(CURVE_RANGES, { value: curveRange, label: "Power curve period",
  onChange: (v) => { curveRange = v; keep("wd.progress.curve", v); drawCurve(); } });
$("curve-range").replaceWith(curveGroup);
const TICKS = [1, 5, 15, 30, 60, 120, 180, 300, 600, 1200, 1800, 3600];
const TICK_LABEL = { 1: "1s", 5: "5s", 15: "15s", 30: "30s", 60: "1m", 120: "2m", 180: "3m", 300: "5m", 600: "10m", 1200: "20m", 1800: "30m", 3600: "60m" };
let curveHover = null;
function drawCurve() {
  const points = data.curve[curveRange] ?? [];
  const allTime = data.curve.all ?? [];
  const bySec = Object.fromEntries(points.map((p) => [p.seconds, p]));
  const kg = data.weight_kg;
  $("efforts").replaceChildren(...[5, 60, 300, 1200].map((s) => {
    const p = bySec[s];
    const best = p ? el("b", { text: String(Math.round(p.watts)) }, el("small", { text: "W" })) : el("b", { text: "--" });
    const body = [el("span", { class: "mono", text: dur(s) }), best,
      el("small", { text: p ? `${(p.watts / kg).toFixed(1)} W/kg · ${dayText(p.date)}` : "no ride yet" })];
    return el("div", { class: "num", title: p ? `${p.name} · ${dayText(p.date)}` : "" },
      p ? el("a", { href: `/summary.html?ride=${encodeURIComponent(p.ride)}` }, body) : body);
  }));

  const canvas = $("curve-chart");
  interactive("curve", canvas, drawCurve);
  const { ctx, w, h } = fit(canvas);
  const left = 40, right = w - 10, top = 8, bottom = h - 22;
  const [s0, s1] = domain("curve", canvas, () => [1, 3600], { log: true, min: Math.log(4) });
  geo.get(canvas).left = left; geo.get(canvas).right = right;
  const lx = (s) => left + ((Math.log(s) - Math.log(s0)) / (Math.log(s1) - Math.log(s0))) * (right - left);
  const inView = (p) => p.seconds >= s0 * 0.98 && p.seconds <= s1 * 1.02;
  if (!allTime.length) {
    font(ctx, 11); ctx.fillStyle = C.muted; ctx.textAlign = "center";
    ctx.fillText("Your best efforts show up after your first ride", w / 2, h / 2);
    return;
  }
  const watts = (allTime.filter(inView).length ? allTime.filter(inView) : allTime).map((p) => p.watts);
  const y = yAxis(ctx, { left, top, right, bottom, min: Math.max(0, Math.min(...watts) - 30), max: Math.max(...watts) + 10, unit: "" });
  font(ctx);
  ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = C.muted;
  const wide = right - left > 520;
  const zoomed = !!view.curve;
  for (const s of TICKS) if (s >= s0 * 0.98 && s <= s1 * 1.02
    && (zoomed || [1, 5, 60, 300, 1200, 3600].includes(s) || (wide && ![180, 1800].includes(s)) || right - left > 900)) {
    ctx.fillText(TICK_LABEL[s], lx(s), bottom + 6);
  }
  clip(ctx, left, top, right, bottom);
  if (curveRange !== "all") glowLine(ctx, allTime.map((p) => [lx(p.seconds), y(p.watts)]), C.muted, 1.5, [4, 4]);
  glowLine(ctx, points.map((p) => [lx(p.seconds), y(p.watts)]), C.cyan, 2.5);
  for (const p of points) if ([5, 60, 300, 1200].includes(p.seconds)) {
    ctx.beginPath(); ctx.arc(lx(p.seconds), y(p.watts), 3.5, 0, 2 * Math.PI);
    ctx.fillStyle = C.pink; ctx.shadowColor = C.pink; ctx.shadowBlur = 8; ctx.fill(); ctx.shadowBlur = 0;
  }
  ctx.restore();
  const hp = curveHover != null ? points[curveHover] : null;
  if (hp) {
    ctx.beginPath(); ctx.arc(lx(hp.seconds), y(hp.watts), 5, 0, 2 * Math.PI);
    ctx.strokeStyle = C.white; ctx.lineWidth = 1.5; ctx.stroke();
    const ever = allTime.find((q) => q.seconds === hp.seconds);
    tip(canvas, [`${dur(hp.seconds)} · ${Math.round(hp.watts)} W`, kg ? `${(hp.watts / kg).toFixed(2)} W/kg` : "",
      `${hp.name} · ${dayText(hp.date)}`, curveRange !== "all" && ever ? `Best ever ${Math.round(ever.watts)} W` : "", "Click: open that ride"]
      .filter(Boolean), lx(hp.seconds), y(hp.watts));
  } else tip(canvas, null);
  canvas.style.cursor = hp ? "pointer" : "";
  canvas.onclick = () => { if (canvas.dataset.dragged) { delete canvas.dataset.dragged; return; } if (hp) location.href = `/summary.html?ride=${encodeURIComponent(hp.ride)}`; };
  onHover(canvas, () => points.map((p) => lx(p.seconds)), (i) => { if (i !== curveHover) { curveHover = i; drawCurve(); } });
}

// ---------- FTP history ----------
const SOURCE = { start: "start", ramp: "ramp test", "20min": "20 min test", best20: "best 20 min", manual: "by hand" };
function drawFtp() {
  const entries = data.ftp.map((e) => ({ ...e, t: new Date(e.date).getTime() }));
  $("ftp-list").replaceChildren(...entries.slice().reverse().map((e) => {
    const change = e.from == null ? null : e.ftp - e.from;
    return el("li", {},
      el("b", { text: `${e.ftp} W` }),
      el("span", { class: change == null ? "" : change > 0 ? "up" : "down", text: change == null ? "" : `${change > 0 ? "▲" : "▼"} ${Math.abs(change)} W` }),
      el("span", { class: `src-tag${["ramp", "20min"].includes(e.source) ? " src-tag--test" : e.source === "best20" ? " src-tag--best20" : ""}`, text: SOURCE[e.source] ?? e.source }),
      el("span", { class: "when" }, new Date(e.t).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }),
        e.ride ? el("span", {}, " · ", el("a", { href: `/summary.html?ride=${encodeURIComponent(e.ride)}`, text: "the ride ›" })) : null,
        e.rebuilt ? el("span", { title: "Worked out from your rides: the FTP each ride started with" }, " · from your rides") : null));
  }));

  const canvas = $("ftp-chart");
  interactive("ftp", canvas, drawFtp);
  const { ctx, w, h } = fit(canvas);
  if (!entries.length) return;
  const left = 40, right = w - 8, top = 10, bottom = h - 22;
  const [from, to] = domain("ftp", canvas, () => [Math.min(rangeFrom(at(entries[0].date)), today), today + DAY], { min: 7 * DAY });
  geo.get(canvas).left = left; geo.get(canvas).right = right;
  const x = (t) => left + ((t - from) / Math.max(DAY, to - from)) * (right - left);
  const shown = entries.filter((e) => e.t >= from && e.t <= to);
  const before = entries.filter((e) => e.t < from).at(-1);
  const vals = [...shown, ...(before ? [before] : [])].map((e) => e.ftp);
  const y = yAxis(ctx, { left, top, right, bottom, min: Math.min(...vals) - 10, max: Math.max(...vals) + 10, lines: 3 });
  dateAxis(ctx, { x, from, to: Math.min(to, today), bottom, maxLabels: Math.max(2, Math.floor((right - left) / 70)) });
  clip(ctx, left, top, right, bottom);
  const steps = [];
  let level = before ? before.ftp : shown[0]?.ftp;
  steps.push([x(from), y(level)]);
  for (const e of shown) { steps.push([x(e.t), y(level)]); level = e.ftp; steps.push([x(e.t), y(level)]); }
  steps.push([x(to), y(level)]);
  glowLine(ctx, steps, C.cyan, 2.5);
  for (const e of shown) if (e.from != null) {
    const color = ["ramp", "20min"].includes(e.source) ? C.yellow : e.source === "best20" ? C.teal : C.white;
    ctx.beginPath(); ctx.arc(x(e.t), y(e.ftp), e.t === ftpHover ? 6 : 4, 0, 2 * Math.PI);
    ctx.fillStyle = color; ctx.shadowColor = color; ctx.shadowBlur = 8; ctx.fill(); ctx.shadowBlur = 0;
  }
  ctx.restore();
  const e = shown.find((s2) => s2.t === ftpHover) ?? null; // the hovered change, by its date (entries are rebuilt each draw)
  if (e) {
    const change = e.from == null ? null : e.ftp - e.from;
    tip(canvas, [`${e.ftp} W`, change == null ? "Starting FTP" : `${change > 0 ? "+" : "−"}${Math.abs(change)} W · ${SOURCE[e.source] ?? e.source}`,
      new Date(e.t).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }), e.ride ? "Click: open that ride" : ""]
      .filter(Boolean), x(e.t), y(e.ftp));
  } else tip(canvas, null);
  canvas.style.cursor = e?.ride ? "pointer" : "";
  canvas.onclick = () => { if (canvas.dataset.dragged) { delete canvas.dataset.dragged; return; } if (e?.ride) location.href = `/summary.html?ride=${encodeURIComponent(e.ride)}`; };
  onHover(canvas, () => shown.map((s) => x(s.t)), (i) => { const n = i == null ? null : shown[i].t; if (n !== ftpHover) { ftpHover = n; drawFtp(); } });
}
let ftpHover = null;

// ---------- power sources across rides ----------
let pairIndex = 0;
let srcHover = null;
function drawSources() {
  const pairs = data.sensors;
  $("src-empty").hidden = pairs.length > 0;
  $("src-chart").hidden = !pairs.length;
  $("src-nums").hidden = !pairs.length;
  if (!pairs.length) { $("src-pair").replaceChildren(); return; }
  if (pairs.length > 1 && !$("src-pair").firstChild) {
    $("src-pair").replaceChildren(chipGroup(pairs.map((p, i) => ({ value: String(i), label: `${p.a_name} · ${p.b_name}` })),
      { value: "0", label: "Pair of power sources", onChange: (v) => { pairIndex = Number(v); srcHover = null; drawSources(); } }));
  }
  const p = pairs[pairIndex];
  if (pairs.length === 1) $("src-pair").replaceChildren(el("span", { class: "mono", text: `${p.a_name} − ${p.b_name}` }));
  const pct = (v) => (v == null ? "--" : `${signed(v, 1)} %`);
  $("src-nums").replaceChildren(
    el("div", { class: "num", title: `${p.a_name} minus ${p.b_name}, as a share of ${p.b_name}, averaged over the rides` },
      el("span", { class: "mono", text: "Average" }), el("b", { text: pct(p.mean_pct) }), el("small", { text: `${p.count} rides` })),
    el("div", { class: "num" }, el("span", { class: "mono", text: "Last ride" }), el("b", { text: pct(p.latest_pct) }),
      el("small", { text: dayText(p.rides.at(-1).date) })),
    el("div", { class: "num", title: "How the difference moves per week (least-squares line through the rides)" },
      el("span", { class: "mono", text: "Trend" }), el("b", { text: p.trend_pct_per_week == null ? "--" : `${signed(p.trend_pct_per_week, 2)} %` }),
      el("small", { text: "per week" })),
    el("div", { class: "num", title: `Above 0: ${p.b_name} reports later than ${p.a_name}` },
      el("span", { class: "mono", text: "Lag" }), el("b", { text: p.mean_lag_s == null ? "--" : `${signed(p.mean_lag_s, 2)} s` }),
      el("small", { text: "average" })));

  const canvas = $("src-chart");
  interactive("src", canvas, drawSources);
  const { ctx, w, h } = fit(canvas);
  const rides = p.rides.filter((r) => r.diff_pct != null).map((r) => ({ ...r, t: new Date(r.date).getTime() }));
  const left = 44, right = w - 10, top = 8, bottom = h - 22;
  const [from, to] = domain("src", canvas, () => [Math.min(rangeFrom(at(rides[0].date)), at(rides[0].date)), today + DAY], { min: 7 * DAY });
  geo.get(canvas).left = left; geo.get(canvas).right = right;
  const x = (t) => left + ((t - from) / Math.max(DAY, to - from)) * (right - left);
  const vals = (rides.some((r) => r.t >= from && r.t <= to) ? rides.filter((r) => r.t >= from && r.t <= to) : rides).map((r) => r.diff_pct);
  const y = yAxis(ctx, { left, top, right, bottom, min: Math.min(-3, ...vals) - 0.5, max: Math.max(3, ...vals) + 0.5, unit: " %" });
  dateAxis(ctx, { x, from, to: Math.min(to, today), bottom, maxLabels: Math.max(2, Math.floor((right - left) / 70)) });
  clip(ctx, left, top, right, bottom);
  // ±2 % agreement band, the same as on the live comparison
  ctx.fillStyle = "rgba(54, 227, 196, .10)";
  ctx.fillRect(left, y(2), right - left, y(-2) - y(2));
  ctx.strokeStyle = "rgba(54, 227, 196, .45)"; ctx.setLineDash([3, 4]);
  for (const v of [2, -2]) { ctx.beginPath(); ctx.moveTo(left, y(v)); ctx.lineTo(right, y(v)); ctx.stroke(); }
  ctx.setLineDash([]);
  if (p.mean_pct != null) glowLine(ctx, [[left, y(p.mean_pct)], [right, y(p.mean_pct)]], C.muted, 1, [6, 4]);
  const shown = rides.filter((r) => r.t >= from && r.t <= to);
  glowLine(ctx, shown.map((r) => [x(r.t), y(r.diff_pct)]), C.teal, 2);
  shown.forEach((r, i) => {
    ctx.beginPath(); ctx.arc(x(r.t), y(r.diff_pct), i === srcHover ? 5.5 : 4, 0, 2 * Math.PI);
    ctx.fillStyle = Math.abs(r.diff_pct) <= 2 ? C.teal : C.yellow;
    ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = 8; ctx.fill(); ctx.shadowBlur = 0;
  });
  ctx.restore();
  const hr = srcHover != null ? shown[srcHover] : null;
  if (hr) tip(canvas, [`${dayText(hr.t)} · ${hr.name}`, `${signed(hr.diff_w)} W · ${signed(hr.diff_pct, 2)} %`,
    `Lag ${hr.lag_s == null ? "--" : `${signed(hr.lag_s, 2)} s`}`, Math.abs(hr.diff_pct) <= 2 ? "Within ±2 %" : "Outside ±2 %", "Click: open that ride"],
    x(hr.t), y(hr.diff_pct));
  else tip(canvas, null);
  canvas.style.cursor = hr ? "pointer" : "";
  canvas.onclick = () => { if (canvas.dataset.dragged) { delete canvas.dataset.dragged; return; } if (hr) location.href = `/summary.html?ride=${encodeURIComponent(hr.ride)}`; };
  onHover(canvas, () => shown.map((r) => x(r.t)), (i) => { if (i !== srcHover) { srcHover = i; drawSources(); } });
}

if (data.cp) $("cp").replaceWith(criticalPowerLine(data.cp));

// ---------- best time up each climb (core/progress.py best_climbs) ----------
function drawClimbs() {
  const climbs = data.climbs ?? [];
  $("climbs-panel").hidden = !climbs.length;
  document.querySelector(".grid").classList.toggle("grid--climbs", climbs.length > 0);
  if (!climbs.length) return;
  const clock = (s) => (s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`
    : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);
  $("climbs-count").textContent = `${climbs.length} climb${climbs.length === 1 ? "" : "s"}`;
  $("climbs-list").replaceChildren(...climbs.map((c) => {
    const b = c.best;
    const tries = `${c.attempts} ride${c.attempts === 1 ? "" : "s"}${c.tops < c.attempts ? ` · ${c.tops} to the top` : ""}`;
    return el("li", {},
      el("span", { class: "climbs__name", text: c.name ?? c.workout_id, title: c.name ?? "" }),
      b ? el("a", { class: "climbs__time", href: `/summary.html?ride=${encodeURIComponent(b.id)}`, title: "Open that ride",
        text: `${clock(b.seconds)} ★` }) : el("span", { class: "climbs__time is-none", text: "not at the top yet" }),
      el("span", { class: "when" },
        b ? `${dayText(new Date(b.started).getTime())} · ${b.w_per_kg?.toFixed(2) ?? "--"} W/kg · VAM ${b.vam ?? "--"} m/h`
          + (b.difficulty < 1 ? ` · difficulty ${Math.round(b.difficulty * 100)} %` : "") : "",
        el("span", { class: "climbs__tries", text: b ? ` · ${tries}` : tries })));
  }));
}

function drawAll() { drawFitness(); drawCurve(); drawFtp(); drawSources(); }
drawClimbs();
drawAll();
let raf = 0;
const resized = new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(drawAll); });
resized.observe(document.querySelector(".grid"));

// Any panel can fill the whole window for a closer look (⤢, then "‹ Back" or Escape); the range chips above
// still change its chart.
for (const tile of document.querySelectorAll(".grid > .tile")) {
  const head = tile.querySelector(".tile__head");
  const tools = el("span", { class: "tile__tools" }); // its own controls and the button, right of the title
  tools.append(...[...head.children].filter((c) => !c.classList.contains("panel__title")),
    zoomButton(tile, { label: tile.querySelector(".panel__title")?.textContent.toLowerCase(), onChange: drawAll }));
  head.append(tools);
  resized.observe(tile);
}
