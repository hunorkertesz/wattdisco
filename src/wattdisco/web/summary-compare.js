// Comparing a ride with earlier ones: the numbers both sides know, the choice of what to compare with,
// and the small charts (sparklines, power curves, an overlay of two rides).
import { fmtClock } from "/components.js";
import { distance, distanceUnit } from "/units.js";

const DAY = 86400e3;
const dur = (s) => (s == null ? "--" : s >= 3600 ? fmtClock(s) : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);
const ef = (wp, hr) => (wp && hr ? wp / hr : null);

// Every number that can be compared, read from a full summary (`s`) or from a history line (`r`).
export const METRICS = [
  { key: "duration", label: "Duration", unit: "", fmt: dur, s: (s) => s.duration, r: (r) => r.duration },
  { key: "avg_power", label: "Avg power", unit: "W", digits: 0, s: (s) => s.basic.avg_power, r: (r) => r.avg_power },
  { key: "wp", label: "Weighted power", unit: "W", digits: 0, s: (s) => s.nerd.wp, r: (r) => r.wp },
  { key: "intensity", label: "Intensity", unit: "", digits: 2, s: (s) => s.nerd.intensity, r: (r) => r.intensity },
  { key: "load", label: "Training load", unit: "", digits: 0, s: (s) => s.nerd.load, r: (r) => r.load },
  { key: "vi", label: "Variability", unit: "", digits: 2, s: (s) => s.nerd.vi, r: (r) => r.vi },
  { key: "on_target", label: "On target", unit: "%", digits: 0, s: (s) => s.nerd.on_target_pct, r: (r) => r.on_target_pct },
  { key: "avg_hr", label: "Avg HR", unit: "bpm", digits: 0, s: (s) => s.basic.avg_hr, r: (r) => r.avg_hr },
  { key: "max_hr", label: "Max HR", unit: "bpm", digits: 0, s: (s) => s.basic.max_hr, r: (r) => r.max_hr },
  { key: "hr_drift", label: "HR drift", unit: "%", digits: 1, s: (s) => s.nerd.hr_drift_pct, r: (r) => r.hr_drift_pct },
  { key: "ef", label: "Watts per beat", unit: "W/bpm", digits: 2, better: "up",
    title: "Weighted power ÷ average heart rate: more watts for the same heart rate means fitter",
    s: (s) => ef(s.nerd.wp, s.basic.avg_hr), r: (r) => ef(r.wp, r.avg_hr) },
  { key: "aet", label: "Aerobic thr. (α1)", unit: "bpm", digits: 0, s: (s) => s.dfa?.threshold?.hr, r: (r) => r.aet_hr },
  { key: "avg_cadence", label: "Avg cadence", unit: "rpm", digits: 0, s: (s) => s.basic.avg_cadence, r: (r) => r.avg_cadence },
  { key: "balance", label: "Left share", unit: "%", digits: 1, s: (s) => s.balance?.left, r: (r) => r.balance_left },
  { key: "distance", label: "Distance (est.)", get unit() { return distanceUnit(); }, digits: 1,
    s: (s) => distance(s.basic.distance_km), r: (r) => distance(r.distance_km) },
  { key: "work", label: "Work", unit: "kJ", digits: 0, s: (s) => s.basic.work_kj, r: (r) => r.work_kj },
];
export const METRIC = Object.fromEntries(METRICS.map((m) => [m.key, m]));

export function fmtMetric(m, v, withUnit = true) {
  if (v == null || Number.isNaN(v)) return "--";
  const text = m.fmt ? m.fmt(v) : Number(v).toFixed(m.digits ?? 0);
  return withUnit && m.unit ? `${text} ${m.unit}` : text;
}

export function metricsOfSummary(s) {
  return Object.fromEntries(METRICS.map((m) => [m.key, m.s(s) ?? null]));
}
export function metricsOfRow(r) {
  return Object.fromEntries(METRICS.map((m) => [m.key, m.r(r) ?? null]));
}

// The change from `other` to `mine`, as "▲ 6 W" / "▼ 0.02" / "= same".
export function delta(m, mine, other) {
  if (mine == null || other == null) return null;
  const d = mine - other;
  const digits = m.key === "duration" ? 0 : m.digits ?? 0;
  const zero = Math.abs(d) < 0.5 * 10 ** -digits;
  const text = zero ? "same" : m.key === "duration"
    ? `${d > 0 ? "▲" : "▼"} ${dur(Math.abs(d))}`
    : `${d > 0 ? "▲" : "▼"} ${Math.abs(d).toFixed(digits)}${m.unit ? ` ${m.unit}` : ""}`;
  const tone = zero || !m.better ? "" : (d > 0) === (m.better === "up") ? "good" : "bad";
  return { text, tone, d };
}

// ---------- what to compare with ----------
// the same workout: by its id, or by name for rides recorded before rides kept the id (" · 45 min" = fitted length)
const baseName = (n) => (n ?? "").replace(/ · \d+ min$/, "");
const sameWorkout = (a, b) => (a.workout_id && b.workout_id)
  ? a.workout_id.replace(/--\d+m$/, "") === b.workout_id.replace(/--\d+m$/, "")
  : baseName(a.name) === baseName(b.name);

// Choices for the picker: the last ride of the same workout, the ride before, the 28-day average, any earlier ride.
export function comparisonChoices(summary, rows) {
  const started = new Date(summary.started).getTime();
  const earlier = rows.filter((r) => r.id !== summary.id && new Date(r.started).getTime() < started);
  const when = (r) => new Date(r.started).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  const choices = [];
  const same = earlier.find((r) => sameWorkout(r, summary));
  if (same) choices.push({ value: `ride:${same.id}`, label: `Last time · ${when(same)}`, row: same, same: true });
  if (earlier[0] && earlier[0] !== same) choices.push({ value: `ride:${earlier[0].id}`, label: `Previous ride · ${when(earlier[0])}`, row: earlier[0] });
  const month = earlier.filter((r) => started - new Date(r.started).getTime() <= 28 * DAY);
  if (month.length >= 2) choices.push({ value: "avg28", label: `28-day average · ${month.length} rides`, rows: month });
  for (const r of earlier.slice(0, 40)) {
    if (!choices.some((c) => c.row === r)) choices.push({ value: `ride:${r.id}`, label: `${when(r)} · ${r.name}`, row: r });
  }
  return choices;
}

export function averageMetrics(rows) {
  const out = {};
  for (const m of METRICS) {
    const vals = rows.map((r) => m.r(r)).filter((v) => v != null);
    out[m.key] = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  }
  return out;
}

// The same metric over recent rides, oldest first, with this ride last (for a sparkline).
export function trendOf(key, summary, rows, n = 10) {
  const m = METRIC[key];
  const started = new Date(summary.started).getTime();
  const before = rows.filter((r) => r.id !== summary.id && new Date(r.started).getTime() < started).slice(0, n - 1).reverse();
  return [...before.map((r) => m.r(r)), m.s(summary)];
}

// ---------- charts ----------
function fit(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}
const C = { cyan: "#3DD9FF", pink: "#FF3D81", hr: "#FF6B7A", white: "#FFF6E5", muted: "#A99BC9", line: "#2A1A44", yellow: "#FFC93C" };
function line(ctx, pts, color, width = 1.6, dash = null, glow = 0) {
  const p = pts.filter((q) => q && q[1] != null && !Number.isNaN(q[1]));
  if (p.length < 2) return;
  ctx.save();
  ctx.beginPath();
  p.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = "round";
  if (dash) ctx.setLineDash(dash);
  if (glow) { ctx.shadowColor = color; ctx.shadowBlur = glow; }
  ctx.stroke();
  ctx.restore();
}
function font(ctx, size = 10) { ctx.font = `${size}px "Martian Mono", ui-monospace, monospace`; }

export function drawSparkline(canvas, values, color = C.cyan) {
  const { ctx, w, h } = fit(canvas);
  const vals = values.map((v) => (v == null ? null : Number(v)));
  const known = vals.filter((v) => v != null);
  if (known.length < 2) return;
  const lo = Math.min(...known), hi = Math.max(...known);
  const x = (i) => 3 + (i / Math.max(1, vals.length - 1)) * (w - 6);
  const y = (v) => h - 3 - ((v - lo) / (hi - lo || 1)) * (h - 6);
  line(ctx, vals.map((v, i) => (v == null ? null : [x(i), y(v)])), color, 1.4, null, 4);
  const last = vals.length - 1;
  if (vals[last] != null) {
    ctx.beginPath(); ctx.arc(x(last), y(vals[last]), 2.6, 0, 2 * Math.PI);
    ctx.fillStyle = C.white; ctx.shadowColor = color; ctx.shadowBlur = 6; ctx.fill();
  }
}

// Best efforts of two rides (or a ride and an average) on one log-time axis.
const CURVE = [1, 5, 15, 30, 60, 120, 180, 300, 600, 1200, 1800, 3600];
const CURVE_LABEL = { 1: "1s", 5: "5s", 15: "15s", 60: "1m", 300: "5m", 1200: "20m", 3600: "60m" };
export function drawCurves(canvas, mine, other) {
  const { ctx, w, h } = fit(canvas);
  const left = 38, right = w - 8, top = 8, bottom = h - 18;
  const pts = (b) => CURVE.filter((s) => b?.[String(s)] != null).map((s) => [s, b[String(s)]]);
  const a = pts(mine), o = pts(other);
  const all = [...a, ...o].map(([, v]) => v);
  if (!all.length) return;
  const lo = Math.max(0, Math.floor((Math.min(...all) - 20) / 50) * 50), hi = Math.ceil((Math.max(...all) + 10) / 50) * 50;
  const x = (s) => left + (Math.log(s) / Math.log(3600)) * (right - left);
  const y = (v) => bottom - ((v - lo) / (hi - lo || 1)) * (bottom - top);
  font(ctx);
  ctx.fillStyle = C.muted; ctx.textAlign = "right"; ctx.textBaseline = "middle";
  const step = (hi - lo) / 4;
  for (let i = 0; i <= 4; i++) {
    const v = lo + i * step;
    ctx.strokeStyle = C.line; ctx.beginPath(); ctx.moveTo(left, Math.round(y(v)) + 0.5); ctx.lineTo(right, Math.round(y(v)) + 0.5); ctx.stroke();
    ctx.fillText(String(Math.round(v)), left - 5, y(v));
  }
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (const [s, l] of Object.entries(CURVE_LABEL)) ctx.fillText(l, x(Number(s)), bottom + 4);
  // this ride's curve over a cyan glow that fades to the axis
  if (a.length > 1) {
    const g = ctx.createLinearGradient(0, top, 0, bottom);
    g.addColorStop(0, "rgba(61, 217, 255, .28)"); g.addColorStop(1, "rgba(61, 217, 255, 0)");
    ctx.beginPath();
    a.forEach(([s, v], i) => (i ? ctx.lineTo(x(s), y(v)) : ctx.moveTo(x(s), y(v))));
    ctx.lineTo(x(a.at(-1)[0]), bottom); ctx.lineTo(x(a[0][0]), bottom); ctx.closePath();
    ctx.fillStyle = g; ctx.fill();
  }
  line(ctx, o.map(([s, v]) => [x(s), y(v)]), C.pink, 1.6, [5, 4], 4);
  line(ctx, a.map(([s, v]) => [x(s), y(v)]), C.cyan, 2.4, null, 8);
}

// Two rides on the workout clock: power (30 s average) and heart rate, this ride bright, the other dashed.
function rolling(series, n = 30) {
  const out = [];
  let sum = 0, count = 0;
  const q = [];
  for (const v of series) {
    q.push(v);
    if (v != null) { sum += v; count++; }
    if (q.length > n) { const old = q.shift(); if (old != null) { sum -= old; count--; } }
    out.push(count ? sum / count : null);
  }
  return out;
}
export function drawOverlay(canvas, mine, other) {
  const { ctx, w, h } = fit(canvas);
  const left = 38, right = w - 38, top = 8, bottom = h - 18;
  const series = (s) => {
    const t = s.series.elapsed.map((e, i) => e ?? i);
    return { t, power: rolling(s.series.power), hr: rolling(s.series.hr, 10) };
  };
  const a = series(mine), b = other ? series(other) : null;
  const end = Math.max(a.t.at(-1) ?? 0, b?.t.at(-1) ?? 0, 60);
  const pw = [...a.power, ...(b?.power ?? [])].filter((v) => v != null);
  const hrs = [...a.hr, ...(b?.hr ?? [])].filter((v) => v != null);
  const pHi = Math.ceil((Math.max(100, ...pw) + 20) / 50) * 50;
  const hLo = Math.floor((Math.min(...hrs, 200) - 10) / 10) * 10, hHi = Math.ceil((Math.max(...hrs, 60) + 5) / 10) * 10;
  const x = (t) => left + (t / end) * (right - left);
  const yp = (v) => bottom - (v / pHi) * (bottom - top);
  const yh = (v) => bottom - ((v - hLo) / (hHi - hLo || 1)) * (bottom - top);
  font(ctx);
  ctx.fillStyle = C.muted; ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i++) {
    const v = (pHi * i) / 4;
    ctx.strokeStyle = C.line; ctx.beginPath(); ctx.moveTo(left, Math.round(yp(v)) + 0.5); ctx.lineTo(right, Math.round(yp(v)) + 0.5); ctx.stroke();
    ctx.textAlign = "right"; ctx.fillStyle = C.cyan; ctx.fillText(String(Math.round(v)), left - 5, yp(v));
    ctx.textAlign = "left"; ctx.fillStyle = C.hr; ctx.fillText(String(Math.round(hLo + ((hHi - hLo) * i) / 4)), right + 5, yp(v));
  }
  ctx.fillStyle = C.muted; ctx.textAlign = "center"; ctx.textBaseline = "top";
  const stepMin = end > 3600 ? 20 : 10;
  for (let m = 0; m * 60 <= end; m += stepMin) ctx.fillText(`${m}`, x(m * 60), bottom + 4);
  if (b) {
    line(ctx, b.t.map((t, i) => [x(t), b.hr[i] == null ? null : yh(b.hr[i])]), "rgba(255, 107, 122, .55)", 1.4, [4, 4]);
    line(ctx, b.t.map((t, i) => [x(t), b.power[i] == null ? null : yp(b.power[i])]), "rgba(255, 201, 60, .85)", 1.6, [5, 4]);
  }
  const fill = a.t.map((t, i) => (a.power[i] == null ? null : [x(t), yp(a.power[i])])).filter(Boolean);
  if (fill.length > 1) {
    const g = ctx.createLinearGradient(0, top, 0, bottom);
    g.addColorStop(0, "rgba(61, 217, 255, .22)"); g.addColorStop(1, "rgba(61, 217, 255, 0)");
    ctx.beginPath();
    fill.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
    ctx.lineTo(fill.at(-1)[0], bottom); ctx.lineTo(fill[0][0], bottom); ctx.closePath();
    ctx.fillStyle = g; ctx.fill();
  }
  line(ctx, a.t.map((t, i) => [x(t), a.hr[i] == null ? null : yh(a.hr[i])]), C.hr, 1.8, null, 4);
  line(ctx, a.t.map((t, i) => [x(t), a.power[i] == null ? null : yp(a.power[i])]), C.cyan, 2.2, null, 6);
}
