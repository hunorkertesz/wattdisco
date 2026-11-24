// Canvas drawing: the workout chart and the live traces under the big numbers.

import { PHASE_LABEL, effortColor, fmtClock, getZoneTops, gradeColor, phaseWatts, zoneColor, zoneOf } from "./components.js";

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function hexToRgba(hex, alpha) {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// Mix a colour towards white: the bright core of a neon tube.
function lighten(hex, f) {
  const n = parseInt(hex.replace("#", ""), 16);
  const mix = (c) => Math.round(c + (255 - c) * f);
  return `rgb(${mix((n >> 16) & 255)}, ${mix((n >> 8) & 255)}, ${mix(n & 255)})`;
}

// Stroke the current path as a neon tube: coloured glow, then a light core.
function neonStroke(ctx, color) {
  ctx.save();
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.shadowColor = color;
  ctx.shadowBlur = 14;
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.strokeStyle = lighten(color, 0.55);
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.restore();
}

// Match the canvas backing store to its CSS size and the screen's pixel ratio.
function fit(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function font(ctx, size, spacing = 0) {
  ctx.font = `${size}px "Martian Mono", ui-monospace, monospace`;
  if ("letterSpacing" in ctx) ctx.letterSpacing = `${spacing}px`;
}

function niceStep(range, maxLines) {
  for (const step of [50, 100, 200, 250, 500]) if (range / step <= maxLines) return step;
  return 1000;
}

const TIME_STEPS = [30, 60, 120, 300, 600, 900, 1800, 3600];
const FILL_TOP_ALPHA = 0.6; // block fill is strongest at the target line…
const FILL_BOTTOM_ALPHA = 0.14; // …and fades towards the axis

// Split one phase into pieces that each sit in a single zone. A flat phase is one
// piece; a ramp is cut wherever its target crosses a zone boundary.
function zoneSegments(s0, s1, w0, w1, ftp) {
  const cuts = [s0, s1];
  if (w0 !== w1) {
    for (const top of getZoneTops()) {
      const edge = top * ftp;
      if (edge > Math.min(w0, w1) && edge < Math.max(w0, w1)) cuts.push(s0 + ((edge - w0) / (w1 - w0)) * (s1 - s0));
    }
  }
  cuts.sort((a, b) => a - b);
  const wAt = (s) => w0 + ((w1 - w0) * (s - s0)) / (s1 - s0);
  return cuts.slice(1).map((b, i) => {
    const a = cuts[i];
    return { a, b, wa: wAt(a), wb: wAt(b), zone: zoneOf((wAt(a) + wAt(b)) / 2, ftp) };
  });
}
const HR_MIN = 60;
const HR_MAX = 200;
export const NOW_AT = 0.4; // NOW line sits at 40 % of the window while riding

/**
 * Draw the workout chart.
 * span: visible seconds, or null to fit the whole workout.
 * Returns the x position of the NOW line in CSS pixels.
 */
export function drawWorkoutChart(canvas, { workout, history, t, span, ftp }) {
  const { ctx, w, h } = fit(canvas);
  if (!workout) return 0;
  const textColor = css("--clean-text");
  const muted = css("--clean-muted");
  const line = css("--clean-line");
  const hrColor = css("--hr");
  const powerColor = css("--power-line"); // not white: the neon block edges have white cores

  const top = 34;
  const bottom = h - 34;
  const plotH = Math.max(10, bottom - top);
  const total = workout.seconds;
  const visible = span == null ? total : span;
  const start = span == null ? 0 : Math.max(0, t - NOW_AT * span);
  const x = (s) => ((s - start) / visible) * w;

  const maxTarget = Math.max(...workout.phases.map((p) => Math.max(p.watts, p.watts_end ?? 0)));
  const step = niceStep(maxTarget * 1.4, 5);
  const pMax = Math.ceil((maxTarget * 1.4) / step) * step;
  const yP = (watts) => bottom - (Math.max(0, watts) / pMax) * plotH;
  const yH = (bpm) => bottom - ((Math.min(HR_MAX, Math.max(HR_MIN, bpm)) - HR_MIN) / (HR_MAX - HR_MIN)) * plotH;

  // grid and power axis
  ctx.strokeStyle = line;
  ctx.lineWidth = 1;
  font(ctx, 12);
  ctx.fillStyle = muted;
  ctx.textAlign = "left";
  for (let p = 0; p <= pMax; p += step) {
    const y = Math.round(yP(p)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
    if (p > 0) ctx.fillText(p === pMax ? `${p} W` : String(p), 6, y + 4);
  }

  // heart rate axis
  ctx.fillStyle = hrColor;
  ctx.textAlign = "right";
  for (let b = HR_MIN; b <= HR_MAX; b += 40) {
    ctx.fillText(b + 40 > HR_MAX ? `${b} bpm` : String(b), w - 6, yH(b) + 4);
  }

  // phases: zone-coloured blocks with neon edges
  ftp = ftp ?? workout.ftp;
  const gap = 1.5; // keeps the edges of neighbouring blocks apart
  let s0 = 0;
  for (const phase of workout.phases) {
    const s1 = s0 + phase.seconds;
    const w0 = phase.watts;
    const w1 = phase.watts_end ?? phase.watts;
    if (s1 >= start && s0 <= start + visible) {
      const segs = zoneSegments(s0, s1, w0, w1, ftp);
      const xa = (s) => (s === s0 ? x(s) + gap : s === s1 ? x(s) - gap : x(s));
      for (const seg of segs) {
        const color = zoneColor(seg.zone);
        const fill = ctx.createLinearGradient(0, Math.min(yP(seg.wa), yP(seg.wb)), 0, bottom);
        fill.addColorStop(0, hexToRgba(color, FILL_TOP_ALPHA));
        fill.addColorStop(1, hexToRgba(color, FILL_BOTTOM_ALPHA));
        ctx.beginPath();
        ctx.moveTo(xa(seg.a), yP(seg.wa));
        ctx.lineTo(xa(seg.b), yP(seg.wb));
        ctx.lineTo(xa(seg.b), bottom);
        ctx.lineTo(xa(seg.a), bottom);
        ctx.closePath();
        ctx.fillStyle = fill;
        ctx.fill();
      }
      // neon outline: left side, top edge piece by piece, right side
      const first = segs[0];
      const last = segs[segs.length - 1];
      ctx.beginPath();
      ctx.moveTo(xa(s0), bottom);
      ctx.lineTo(xa(s0), yP(w0));
      neonStroke(ctx, zoneColor(first.zone));
      for (const seg of segs) {
        ctx.beginPath();
        ctx.moveTo(xa(seg.a), yP(seg.wa));
        ctx.lineTo(xa(seg.b), yP(seg.wb));
        neonStroke(ctx, zoneColor(seg.zone));
      }
      ctx.beginPath();
      ctx.moveTo(xa(s1), yP(w1));
      ctx.lineTo(xa(s1), bottom);
      neonStroke(ctx, zoneColor(last.zone));

      const label = `Z${zoneOf(Math.max(w0, w1), ftp)} · ${PHASE_LABEL[phase.kind] ?? phase.kind.toUpperCase()} · ${phaseWatts(phase)}`;
      font(ctx, 13, 1.5);
      const lx = Math.max(x(s0) + 12, 52); // clear of the power axis labels
      if (Math.min(x(s1), w - 64) - lx > ctx.measureText(label).width + 8) {
        ctx.fillStyle = effortColor(Math.max(w0, w1), ftp);
        ctx.textAlign = "left";
        ctx.fillText(label, lx, Math.min(yP(w0), yP(w1)) - 14);
      }
    }
    s0 = s1;
  }

  // recorded heart rate, then power on top
  const series = (idx, y, color, width) => {
    ctx.beginPath();
    let pen = false;
    for (const point of history) {
      const [ts] = point;
      const v = point[idx];
      if (ts < start - 1 || ts > t + 1) continue;
      if (v == null) { pen = false; continue; }
      if (pen) ctx.lineTo(x(ts), y(v));
      else { ctx.moveTo(x(ts), y(v)); pen = true; }
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.stroke();
  };
  series(2, yH, hrColor, 2.5);
  series(1, yP, hexToRgba(css("--clean-bg"), 0.9), 7); // dark outline lifts the line off the blocks
  series(1, yP, powerColor, 2.5);

  // NOW line and latest values
  const nx = x(t);
  ctx.beginPath();
  ctx.moveTo(Math.round(nx) + 0.5, 20);
  ctx.lineTo(Math.round(nx) + 0.5, bottom);
  ctx.strokeStyle = hexToRgba(textColor, 0.6);
  ctx.lineWidth = 1;
  ctx.stroke();
  font(ctx, 12, 1.5);
  ctx.fillStyle = textColor;
  ctx.textAlign = "center";
  ctx.fillText("NOW", nx, 12);
  const last = history[history.length - 1];
  if (last) {
    if (last[1] != null) {
      ctx.beginPath(); ctx.arc(nx, yP(last[1]), 5.5, 0, 2 * Math.PI); ctx.fillStyle = powerColor; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = css("--clean-bg"); ctx.stroke();
    }
    if (last[2] != null) {
      ctx.beginPath(); ctx.arc(nx, yH(last[2]), 5, 0, 2 * Math.PI); ctx.fillStyle = hrColor; ctx.fill();
    }
  }

  // time axis
  const tStep = TIME_STEPS.find((s) => visible / s <= 6) ?? 3600;
  font(ctx, 12);
  ctx.fillStyle = muted;
  for (let s = Math.ceil(start / tStep) * tStep; s <= start + visible; s += tStep) {
    const lx = x(s);
    ctx.textAlign = lx < 30 ? "left" : lx > w - 30 ? "right" : "center";
    ctx.fillText(fmtClock(s), lx, h - 10);
  }
  return nx;
}

const RING_MS = 900; // how long one heartbeat ring takes to grow and fade

function randomNormal() {
  return Math.sqrt(-2 * Math.log(1 - Math.random())) * Math.cos(2 * Math.PI * Math.random());
}

/**
 * Scrolling trace under a big number, drawn like a monitor: the "paper" moves at a
 * constant speed, so a higher rate shows as tighter beats.
 *   kind "pulse": heartbeat rings at `rate` bpm (a dot that swells and sends out a fading
 *                 ring on each beat, with a little beat-to-beat variation); not an ECG
 *   kind "wave": one hump per pedal stroke at `rate` rpm
 *   kind "line": recent values pushed with push(value)
 */
export class Trace {
  constructor(canvas, { color, kind, speed, onBeat }) {
    this.canvas = canvas;
    this.color = color;
    this.kind = kind;
    this.speed = speed ?? 60; // px per second
    this.onBeat = onBeat;
    this.rate = null;
    this.items = []; // line: {at, v}
    this.beats = []; // pulse: beat times (ms)
    this.nextBeat = null;
    this.phase = 0; // wave: crank revolutions so far
    this.phaseLog = []; // wave: [time, phase] samples to draw from
    this.lastFrame = performance.now();
    requestAnimationFrame((now) => this.frame(now));
  }

  setRate(perMinute) {
    this.rate = perMinute > 0 ? perMinute : null;
  }

  push(value) {
    this.items.push({ at: performance.now(), v: value });
  }

  frame(now) {
    const dt = Math.min(1000, now - this.lastFrame); // background tabs get few frames; keep the rhythm right
    this.lastFrame = now;
    const visibleMs = ((this.canvas.clientWidth || 300) / this.speed) * 1000 + 1000;
    if (this.kind === "pulse") {
      if (!this.rate) {
        this.nextBeat = null;
      } else {
        if (this.nextBeat === null) this.nextBeat = now + 150;
        while (now >= this.nextBeat) {
          this.beats.push(this.nextBeat);
          this.onBeat?.();
          const rr = (60000 / this.rate) * (1 + 0.03 * randomNormal()); // heart rate variability
          this.nextBeat += Math.max(250, rr);
        }
      }
      this.beats = this.beats.filter((b) => now - b < RING_MS);
    } else if (this.kind === "wave") {
      if (this.rate) this.phase += (dt / 60000) * this.rate;
      this.phaseLog.push([now, this.phase]);
      while (this.phaseLog.length > 2 && now - this.phaseLog[1][0] > visibleMs) this.phaseLog.shift();
    }
    this.draw(now);
    requestAnimationFrame((t) => this.frame(t));
  }

  draw(now) {
    const { ctx, w, h } = fit(this.canvas);
    if (!w || !h) return;
    if (this.kind === "pulse") return this.drawPulse(ctx, w, h, now);
    const grad = ctx.createLinearGradient(0, 0, w, 0);
    grad.addColorStop(0, hexToRgba(this.color, 0.15));
    grad.addColorStop(1, hexToRgba(this.color, 1));
    ctx.strokeStyle = grad;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.beginPath();
    const head = this.kind === "line" ? this.drawLine(ctx, w, h, now) : this.drawSignal(ctx, w, h, now);
    ctx.stroke();
    if (head) {
      ctx.beginPath();
      ctx.arc(w - 3, head.y, head.r, 0, 2 * Math.PI);
      ctx.fillStyle = this.color;
      ctx.fill();
    }
  }

  // Heartbeat rings: a faint line, a dot in the middle that swells on each beat, and a
  // ring per beat that grows and fades. Deliberately not a heart monitor trace.
  drawPulse(ctx, w, h, now) {
    const cx = w / 2;
    const cy = h / 2;
    ctx.strokeStyle = hexToRgba(this.color, 0.18);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, cy);
    ctx.lineTo(w, cy);
    ctx.stroke();
    for (const b of this.beats) {
      const age = (now - b) / RING_MS;
      if (age < 0 || age > 1) continue;
      const rx = 6 + age * w * 0.42;
      ctx.strokeStyle = hexToRgba(this.color, (1 - age) * 0.8);
      ctx.lineWidth = 2 * (1 - age) + 0.5;
      ctx.beginPath();
      ctx.ellipse(cx, cy, rx, Math.max(1, Math.min(rx * 0.35, cy - 2)), 0, 0, 2 * Math.PI);
      ctx.stroke();
    }
    if (!this.rate) return;
    const last = this.beats.length ? this.beats[this.beats.length - 1] : -Infinity;
    const swell = 1 + 0.5 * Math.exp(-(now - last) / 140);
    ctx.beginPath();
    ctx.arc(cx, cy, Math.min(5 * swell, cy), 0, 2 * Math.PI);
    ctx.fillStyle = this.color;
    ctx.fill();
  }

  // Pedal wave: one value per pixel column, from the time that column shows.
  drawSignal(ctx, w, h, now) {
    const base = h * 0.88;
    const amp = h * 0.76;
    const timeAt = (x) => now - ((w - x) / this.speed) * 1000;
    let log = 0;
    const phaseAt = (t) => {
      const L = this.phaseLog;
      if (!L.length) return 0;
      while (log < L.length - 2 && L[log + 1][0] < t) log++;
      const [t0, p0] = L[log];
      const [t1, p1] = L[Math.min(log + 1, L.length - 1)];
      return t1 === t0 || t <= t0 ? p0 : p0 + ((p1 - p0) * Math.min(1, (t - t0) / (t1 - t0)));
    };
    const value = (t) => Math.abs(Math.sin(Math.PI * phaseAt(t))); // one hump per pedal stroke
    let y = base;
    for (let x = 0; x <= w; x++) {
      y = base - amp * value(timeAt(x));
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    return this.rate ? { y, r: 3 } : null;
  }

  drawLine(ctx, w, h, now) {
    const xOf = (at) => w - ((now - at) / 1000) * this.speed;
    this.items = this.items.filter((it) => xOf(it.at) > -40);
    const pts = this.items.filter((it) => it.v != null);
    if (pts.length < 2) {
      ctx.moveTo(0, h * 0.6);
      ctx.lineTo(w, h * 0.6);
      return null;
    }
    let lo = Math.min(...pts.map((p) => p.v));
    let hi = Math.max(...pts.map((p) => p.v));
    if (hi - lo < 40) { const mid = (hi + lo) / 2; lo = mid - 20; hi = mid + 20; }
    const y = (v) => h - 4 - ((v - lo) / (hi - lo)) * (h - 8);
    pts.forEach((p, i) => (i ? ctx.lineTo(xOf(p.at), y(p.v)) : ctx.moveTo(xOf(p.at), y(p.v))));
    const lastV = pts[pts.length - 1].v;
    ctx.lineTo(w, y(lastV));
    return this.items[this.items.length - 1].v == null ? null : { y: y(lastV), r: 3 };
  }
}

// ---------- ride summary (neon screens) ----------

function minuteLabels(ctx, w, h, seconds, x) {
  const step = TIME_STEPS.find((s) => seconds / s <= 7) ?? 3600;
  font(ctx, 12);
  ctx.fillStyle = css("--muted");
  for (let s = 0; s <= seconds; s += step) {
    const lx = x(s);
    ctx.textAlign = lx < 20 ? "left" : lx > w - 40 ? "right" : "center";
    ctx.fillText(s + step > seconds ? `${Math.round(s / 60)} min` : String(Math.round(s / 60)), lx, h - 6);
  }
}

function glowLine(ctx, points, color, width) {
  ctx.save();
  ctx.beginPath();
  let pen = false;
  for (const p of points) {
    if (p == null) { pen = false; continue; }
    if (pen) ctx.lineTo(p[0], p[1]);
    else { ctx.moveTo(p[0], p[1]); pen = true; }
  }
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.shadowColor = typeof color === "string" ? color : "rgba(255, 246, 229, .45)"; // a gradient glows warm white
  ctx.shadowBlur = 6;
  ctx.stroke();
  ctx.restore();
}

/** A vertical gradient that paints a power line in the zone colour of each height (hard steps at the zone tops). */
function zoneStroke(ctx, yP, ftp, pMax) {
  const g = ctx.createLinearGradient(0, yP(0), 0, yP(pMax));
  const tops = getZoneTops();
  let lo = 0;
  tops.forEach((top, i) => {
    const at = Math.min(1, (top * ftp) / pMax);
    g.addColorStop(lo, zoneColor(i + 1));
    g.addColorStop(at, zoneColor(i + 1));
    lo = at;
  });
  g.addColorStop(lo, zoneColor(tops.length + 1));
  g.addColorStop(1, zoneColor(tops.length + 1));
  return g;
}

/** Whole ride: workout blocks in zone colours, power (warm white) and heart rate (pink). */
export function drawSummaryChart(canvas, summary) {
  const { ctx, w, h } = fit(canvas);
  const { workout, series } = summary;
  const ftp = summary.ftp;
  const top = 12;
  const bottom = h - 26;
  const plotH = bottom - top;
  const last = series.elapsed.length ? series.elapsed[series.elapsed.length - 1] : 0;
  const seconds = Math.max(60, last);
  const x = (s) => (s / seconds) * w;

  const maxTarget = workout.climb ? 0 : Math.max(...workout.phases.map((p) => Math.max(p.watts, p.watts_end ?? 0)));
  const maxPower = Math.max(maxTarget, ...series.power.filter((p) => p != null));
  const pMax = Math.ceil((maxPower * 1.15) / 50) * 50;
  const yP = (watts) => bottom - (Math.max(0, watts) / pMax) * plotH;
  const hrs = series.hr.filter((v) => v != null);
  const hrLo = Math.min(80, ...hrs) - 10;
  const hrHi = Math.max(180, ...hrs) + 5;
  const yH = (bpm) => bottom - ((bpm - hrLo) / (hrHi - hrLo)) * plotH;

  ctx.strokeStyle = css("--line");
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = Math.round(top + (plotH * i) / 4) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }

  // a climb: the road ridden, by time, in the colour of its gradient (it has no target power to draw)
  if (workout.climb && series.altitude) {
    const alts = series.altitude.filter((a) => a != null);
    const aLo = Math.min(...alts);
    const aHi = Math.max(aLo + 20, ...alts);
    const yA = (a) => bottom - ((a - aLo) / (aHi - aLo)) * plotH * 0.75;
    for (let i = 1; i < series.elapsed.length; i++) {
      const [e0, e1, a0, a1] = [series.elapsed[i - 1], series.elapsed[i], series.altitude[i - 1], series.altitude[i]];
      if (e0 == null || e1 == null || a0 == null || a1 == null) continue;
      ctx.beginPath();
      ctx.moveTo(x(e0), yA(a0)); ctx.lineTo(x(e1) + 0.5, yA(a1)); ctx.lineTo(x(e1) + 0.5, bottom); ctx.lineTo(x(e0), bottom); ctx.closePath();
      ctx.fillStyle = hexToRgba(gradeColor(series.grade?.[i] ?? 0), 0.22);
      ctx.fill();
    }
  }

  // workout blocks up to where the ride ended
  let s0 = 0;
  for (const phase of workout.climb ? [] : workout.phases) {
    const s1 = Math.min(s0 + phase.seconds, seconds);
    if (s0 >= seconds) break;
    const w0 = phase.watts;
    const wEnd = phase.watts_end ?? phase.watts;
    const w1 = w0 + ((wEnd - w0) * (s1 - s0)) / phase.seconds;
    for (const seg of zoneSegments(s0, s1, w0, w1, ftp)) {
      const color = zoneColor(seg.zone);
      ctx.beginPath();
      ctx.moveTo(x(seg.a), yP(seg.wa));
      ctx.lineTo(x(seg.b), yP(seg.wb));
      ctx.lineTo(x(seg.b), bottom);
      ctx.lineTo(x(seg.a), bottom);
      ctx.closePath();
      ctx.fillStyle = hexToRgba(color, 0.16);
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(x(seg.a), yP(seg.wa));
      ctx.lineTo(x(seg.b), yP(seg.wb));
      ctx.save(); ctx.globalAlpha = 0.6; neonStroke(ctx, color); ctx.restore();
    }
    s0 += phase.seconds;
  }

  const pts = (values, y) => series.elapsed.map((e, i) => (values[i] == null || e == null ? null : [x(e), y(values[i])]));
  glowLine(ctx, pts(series.hr, yH), css("--pink"), 2.5);
  // power in the zone colour of each moment, on a dark outline so it stays apart from the blocks
  const power = pts(series.power, yP);
  glowLine(ctx, power, "rgba(10, 6, 18, .85)", 3.6);
  glowLine(ctx, power, ftp ? zoneStroke(ctx, yP, ftp, pMax) : css("--white"), 1.8);
  minuteLabels(ctx, w, h, seconds, x);
}

/** Power minus target over the ride, with the ±5 % band. */
export function drawTargetDiffChart(canvas, summary) {
  const { ctx, w, h } = fit(canvas);
  const { series } = summary;
  const top = 8;
  const bottom = h - 26;
  const mid = (top + bottom) / 2;
  const last = series.elapsed.length ? series.elapsed[series.elapsed.length - 1] : 0;
  const seconds = Math.max(60, last);
  const x = (s) => (s / seconds) * w;

  // 10 s averages keep the line readable over an hour
  const diffs = series.power.map((p, i) => (p == null || !series.target[i] ? null : p - series.target[i]));
  const smooth = diffs.map((_, i) => {
    const win = diffs.slice(Math.max(0, i - 9), i + 1).filter((d) => d != null);
    return diffs[i] == null || !win.length ? null : win.reduce((a, b) => a + b, 0) / win.length;
  });
  const span = Math.max(20, Math.ceil(Math.max(...smooth.filter((d) => d != null).map(Math.abs), 0) / 10) * 10);
  const y = (d) => mid - (Math.max(-span, Math.min(span, d)) / span) * (mid - top);

  ctx.strokeStyle = css("--line");
  ctx.lineWidth = 1;
  for (const v of [-span, -span / 2, span / 2, span]) {
    ctx.beginPath(); ctx.moveTo(0, Math.round(y(v)) + 0.5); ctx.lineTo(w, Math.round(y(v)) + 0.5); ctx.stroke();
  }

  // ±5 % band follows the target
  const teal = css("--teal");
  ctx.beginPath();
  const band = series.elapsed.map((e, i) => (series.target[i] ? [x(e), 0.05 * series.target[i]] : null)).filter(Boolean);
  band.forEach(([bx, b], i) => (i ? ctx.lineTo(bx, y(b)) : ctx.moveTo(bx, y(b))));
  for (let i = band.length - 1; i >= 0; i--) ctx.lineTo(band[i][0], y(-band[i][1]));
  ctx.closePath();
  ctx.fillStyle = hexToRgba(teal, 0.12);
  ctx.fill();

  ctx.setLineDash([4, 4]);
  ctx.strokeStyle = "#6B5A8C";
  ctx.beginPath(); ctx.moveTo(0, Math.round(mid) + 0.5); ctx.lineTo(w, Math.round(mid) + 0.5); ctx.stroke();
  ctx.setLineDash([]);

  glowLine(ctx, series.elapsed.map((e, i) => (smooth[i] == null ? null : [x(e), y(smooth[i])])), css("--white"), 2);

  font(ctx, 12);
  ctx.textAlign = "left";
  ctx.fillStyle = css("--muted");
  ctx.fillText(`+${span} W`, 6, top + 10);
  ctx.fillText("0", 6, mid - 4);
  ctx.fillText(`−${span} W`, 6, bottom - 4);
  ctx.textAlign = "right";
  ctx.fillStyle = teal;
  ctx.fillText("±5 % BAND", w - 6, mid - 8);
  minuteLabels(ctx, w, h, seconds, x);
}

// ---------- workout preview (ready screen, editor) ----------

/**
 * The whole workout as zone-coloured blocks with neon edges and a dashed FTP line.
 * `selected`: index of a phase to highlight. Returns [{i, x0, x1}] for hit testing.
 */
export function drawWorkoutPreview(canvas, workout, ftp, { selected = null } = {}) {
  const { ctx, w, h } = fit(canvas);
  if (!workout || !workout.phases.length) return [];
  const top = 26; // room for the phase power labels
  const bottom = h - 24;
  const plotH = bottom - top;
  const total = workout.phases.reduce((a, p) => a + p.seconds, 0);
  const x = (s) => (s / total) * w;
  const maxW = Math.max(ftp, ...workout.phases.map((p) => Math.max(p.watts, p.watts_end ?? 0)));
  const pMax = Math.ceil((maxW * 1.15) / 150) * 150; // grid lines in thirds land on round watts
  const yP = (watts) => bottom - (Math.max(0, watts) / pMax) * plotH;

  ctx.strokeStyle = css("--line");
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const y = Math.round(top + (plotH * i) / 3) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }

  const hits = [];
  const labels = []; // power over each phase, drawn last so nothing covers them
  let s0 = 0;
  workout.phases.forEach((phase, i) => {
    const s1 = s0 + phase.seconds;
    const w0 = phase.watts;
    const w1 = phase.watts_end ?? phase.watts;
    const gap = 1.5;
    const xa = (s) => (s === s0 ? x(s) + gap : s === s1 ? x(s) - gap : x(s));
    const segs = zoneSegments(s0, s1, w0, w1, ftp);
    const isSel = i === selected;
    for (const seg of segs) {
      const color = zoneColor(seg.zone);
      const fill = ctx.createLinearGradient(0, Math.min(yP(seg.wa), yP(seg.wb)), 0, bottom);
      fill.addColorStop(0, hexToRgba(color, isSel ? 0.7 : 0.4));
      fill.addColorStop(1, hexToRgba(color, isSel ? 0.25 : 0.08));
      ctx.beginPath();
      ctx.moveTo(xa(seg.a), yP(seg.wa)); ctx.lineTo(xa(seg.b), yP(seg.wb));
      ctx.lineTo(xa(seg.b), bottom); ctx.lineTo(xa(seg.a), bottom); ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();
      ctx.beginPath(); ctx.moveTo(xa(seg.a), yP(seg.wa)); ctx.lineTo(xa(seg.b), yP(seg.wb));
      neonStroke(ctx, color);
    }
    ctx.beginPath(); ctx.moveTo(xa(s0), bottom); ctx.lineTo(xa(s0), yP(w0)); neonStroke(ctx, zoneColor(segs[0].zone));
    ctx.beginPath(); ctx.moveTo(xa(s1), yP(w1)); ctx.lineTo(xa(s1), bottom); neonStroke(ctx, zoneColor(segs[segs.length - 1].zone));
    if (isSel) {
      ctx.save();
      ctx.strokeStyle = css("--white");
      ctx.setLineDash([3, 4]);
      ctx.strokeRect(x(s0) + 0.5, top - 6, x(s1) - x(s0) - 1, bottom - top + 6);
      ctx.restore();
    }
    hits.push({ i, x0: x(s0), x1: x(s1) });
    labels.push({ i, x: (x(s0) + x(s1)) / 2, width: x(s1) - x(s0), y: yP(Math.max(w0, w1)),
      // the zone in words where the block has room for it; a narrow block keeps the watts only
      full: `Z${segs[segs.length - 1].zone} ${w1 === w0 ? `${w0} W` : `${w0}–${w1} W`}`,
      text: w1 === w0 ? `${w0} W` : `${w0}–${w1} W`, color: zoneColor(segs[segs.length - 1].zone) });
    s0 = s1;
  });

  // FTP reference line
  const yF = Math.round(yP(ftp)) + 0.5;
  ctx.save();
  ctx.setLineDash([5, 5]);
  ctx.strokeStyle = hexToRgba(css("--yellow"), 0.7);
  ctx.beginPath(); ctx.moveTo(0, yF); ctx.lineTo(w, yF); ctx.stroke();
  ctx.restore();
  font(ctx, 12);
  ctx.fillStyle = css("--yellow");
  ctx.textAlign = "right";
  ctx.fillText(`FTP ${ftp} W`, w - 4, yF - 6);

  // power axis on the grid lines
  ctx.fillStyle = css("--muted");
  ctx.textAlign = "left";
  for (let i = 0; i < 3; i++) ctx.fillText(`${Math.round(pMax * (1 - i / 3))} W`, 4, Math.round(top + (plotH * i) / 3) - 4);

  // each phase's power above it: the selected one first, then those that fit their block,
  // then narrow ones wherever they do not run into a label already drawn
  font(ctx, 13);
  ctx.textAlign = "center";
  const drawn = [];
  const box = (l, text = l.text) => {
    const tw = ctx.measureText(text).width;
    const cx = Math.min(w - tw / 2 - 2, Math.max(tw / 2 + 2, l.x));
    const ly = Math.max(14, l.y - 8);
    return { l, text, tw, cx, ly, x0: cx - tw / 2 - 3, x1: cx + tw / 2 + 3, y0: ly - 13, y1: ly + 4 };
  };
  const clear = (b) => !drawn.some((d) => b.x0 < d.x1 && b.x1 > d.x0 && b.y0 < d.y1 && b.y1 > d.y0);
  const order = [...labels].sort((a, b) => (b.i === selected) - (a.i === selected));
  for (const pass of ["selected", "fits", "rest"]) {
    for (const l of order) {
      if (drawn.some((d) => d.l === l)) continue;
      const long = box(l, l.full ?? l.text);
      const b = long.tw + 8 <= l.width && clear(long) ? long : box(l);
      if (pass === "selected" && l.i !== selected) continue;
      if (pass === "fits" && (b.tw + 8 > l.width || !clear(b))) continue;
      if (pass === "rest" && !clear(b)) continue;
      if (l.i === selected) { // dark backing so it reads over neighbours
        ctx.fillStyle = hexToRgba(css("--bg"), 0.85);
        ctx.fillRect(b.x0 - 1, b.y0, b.x1 - b.x0 + 2, b.y1 - b.y0);
      }
      ctx.fillStyle = l.i === selected ? css("--white") : l.color;
      ctx.fillText(b.text, b.cx, b.ly);
      drawn.push(b);
    }
  }

  minuteLabels(ctx, w, h, total, x);
  return hits;
}

// ---------- sensor comparison ----------

/** One line per source over the same seconds. `window` is the seconds the width stands for. */
export function drawCompareChart(canvas, series, colors, window = 120) {
  const { ctx, w, h } = fit(canvas);
  const values = series.values.flat().filter((v) => v != null);
  if (!values.length) return;
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  const pad = Math.max(5, (hi - lo) * 0.15);
  lo = Math.floor((lo - pad) / 5) * 5;
  hi = Math.ceil((hi + pad) / 5) * 5;
  const top = 8;
  const bottom = h - 8;
  const y = (v) => bottom - ((v - lo) / (hi - lo)) * (bottom - top);
  const n = Math.max(2, window);
  const x = (i, len) => w - 12 - ((len - 1 - i) / (n - 1)) * (w - 12);

  ctx.strokeStyle = css("--line");
  ctx.lineWidth = 1;
  font(ctx, 12);
  ctx.fillStyle = css("--muted");
  ctx.textAlign = "left";
  for (const v of [hi, (hi + lo) / 2, lo]) {
    const yy = Math.round(y(v)) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(w, yy); ctx.stroke();
    ctx.fillText(String(Math.round(v)), 6, v === lo ? yy - 4 : yy + 14);
  }
  // A last, so the reference source is drawn on top
  for (let s = series.values.length - 1; s >= 0; s--) {
    const vals = series.values[s];
    const width = vals.length > 600 ? 1.5 : 2.5;
    glowLine(ctx, vals.map((v, i) => (v == null ? null : [x(i, vals.length), y(v)])), colors[s], width);
    const last = vals.length - 1;
    if (vals[last] != null && vals.length <= 600) {
      ctx.beginPath(); ctx.arc(x(last, vals.length), y(vals[last]), 4.5, 0, 2 * Math.PI);
      ctx.fillStyle = colors[s]; ctx.fill();
    }
  }
}

/** A − B (and A − C) over the same seconds, around a dashed zero line, in B's (C's) colour. */
export function drawDiffStrip(canvas, series, colors, window = 120, labels = ["−2:00", "NOW"]) {
  const { ctx, w, h } = fit(canvas);
  const [ref, ...others] = series.values;
  const diffs = others.map((vals) => ref.map((a, i) => (a == null || vals[i] == null ? null : a - vals[i])));
  const present = diffs.flat().filter((d) => d != null);
  const span = Math.max(4, ...present.map(Math.abs)) * 1.2;
  const mid = (h - 16) / 2;
  const y = (d) => mid - (d / span) * (mid - 4);
  const n = Math.max(2, window);
  const x = (i, len) => w - 12 - ((len - 1 - i) / (n - 1)) * (w - 12);
  ctx.save();
  ctx.setLineDash([4, 4]);
  ctx.strokeStyle = "#6B5A8C";
  ctx.beginPath(); ctx.moveTo(0, Math.round(mid) + 0.5); ctx.lineTo(w, Math.round(mid) + 0.5); ctx.stroke();
  ctx.restore();
  diffs.forEach((d, s) => {
    glowLine(ctx, d.map((v, i) => (v == null ? null : [x(i, d.length), y(v)])), others.length > 1 ? colors[s + 1] : css("--white"), 1.5);
  });
  font(ctx, 12);
  ctx.fillStyle = css("--muted");
  ctx.textAlign = "left";
  ctx.fillText(labels[0], 6, h - 2);
  ctx.textAlign = "right";
  ctx.fillText(labels[1], w - 6, h - 2);
}

// ---------- big numbers ----------

/** Track around the target: the ±5 % band, the target tick and a dot for the rider's power. */
export function drawTargetBar(canvas, power, target, ftp) {
  const { ctx, w, h } = fit(canvas);
  const mid = h / 2;
  ctx.fillStyle = css("--clean-line");
  ctx.beginPath(); ctx.roundRect(0, mid - 3, w, 6, 3); ctx.fill();
  if (!target) return;
  const span = target * 0.25; // the track shows target ± 25 %
  const x = (watts) => Math.max(7, Math.min(w - 7, w / 2 + ((watts - target) / span) * (w / 2)));
  const color = zoneColor(zoneOf(target, ftp));
  const band = target * 0.05;
  ctx.fillStyle = hexToRgba(color, 0.3);
  ctx.beginPath(); ctx.roundRect(x(target - band), mid - 9, x(target + band) - x(target - band), 18, 3); ctx.fill();
  ctx.fillStyle = color;
  ctx.fillRect(Math.round(w / 2) - 2, mid - 11, 4, 22);
  if (power != null) {
    ctx.beginPath(); ctx.arc(x(power), mid, 7, 0, 2 * Math.PI);
    ctx.fillStyle = zoneColor(zoneOf(power, ftp)); ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = css("--clean-bg"); ctx.stroke();
  }
}

/** Whole workout as a thin strip: done part bright, the rest dim, NOW marker. */
export function drawProgressStrip(canvas, workout, ftp, elapsed) {
  const { ctx, w, h } = fit(canvas);
  if (!workout?.phases.length) return;
  const total = workout.phases.reduce((a, p) => a + p.seconds, 0);
  const maxW = Math.max(...workout.phases.map((p) => Math.max(p.watts, p.watts_end ?? 0)));
  const bottom = h - 4;
  const y = (watts) => bottom - (watts / (maxW * 1.1)) * (bottom - 4);
  const x = (s) => (s / total) * w;
  let s0 = 0;
  for (const phase of workout.phases) {
    const s1 = s0 + phase.seconds;
    for (const seg of zoneSegments(s0, s1, phase.watts, phase.watts_end ?? phase.watts, ftp)) {
      const color = zoneColor(seg.zone);
      for (const [from, to, alpha] of [[seg.a, Math.min(seg.b, elapsed), 0.75], [Math.max(seg.a, elapsed), seg.b, 0.25]]) {
        if (to <= from) continue;
        const wa = seg.wa + ((seg.wb - seg.wa) * (from - seg.a)) / (seg.b - seg.a);
        const wb = seg.wa + ((seg.wb - seg.wa) * (to - seg.a)) / (seg.b - seg.a);
        ctx.beginPath();
        ctx.moveTo(x(from), y(wa)); ctx.lineTo(x(to), y(wb)); ctx.lineTo(x(to), bottom); ctx.lineTo(x(from), bottom); ctx.closePath();
        ctx.fillStyle = hexToRgba(color, alpha);
        ctx.fill();
      }
    }
    s0 = s1;
  }
  const nx = Math.round(x(Math.min(elapsed, total))) + 0.5;
  ctx.strokeStyle = css("--clean-text");
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(nx, 0); ctx.lineTo(nx, h); ctx.stroke();
}

// A climb: height over distance, each stretch filled in its gradient's colour, the rider as a dot.
// `profile` is [[metres, height, gradient %], ...]; `distance` the rider's position (null before the start).
// `window` (metres) shows that much of the road around the rider, mostly ahead; null shows the whole climb.
export function drawClimbProfile(canvas, profile, { distance = null, imperial = false, window = null } = {}) {
  const { ctx, w, h } = fit(canvas);
  if (!profile || profile.length < 2) return;
  const top = 18;
  const bottom = h - 22;
  const length = profile[profile.length - 1][0];
  const span = window && window < length ? window : length;
  const from = span === length ? 0 : Math.min(length - span, Math.max(0, (distance ?? 0) - span * 0.25));
  const to = from + span;
  const heightAt = (d) => {
    let i = profile.findIndex((p) => p[0] >= d);
    if (i <= 0) return profile[Math.max(0, i)][1];
    const [d0, a0] = profile[i - 1];
    const [d1, a1] = profile[i];
    return a0 + ((a1 - a0) * (d - d0)) / Math.max(1, d1 - d0);
  };
  const seen = profile.filter((p) => p[0] >= from && p[0] <= to).map((p) => p[1]).concat([heightAt(from), heightAt(to)]);
  const lo = Math.min(...seen);
  const hi = Math.max(...seen);
  const rise = Math.max(span === length ? 20 : 8, hi - lo);
  const x = (d) => ((d - from) / span) * w;
  const y = (a) => bottom - ((a - lo) / (rise * 1.12)) * (bottom - top);

  ctx.strokeStyle = css("--line");
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const yy = Math.round(top + ((bottom - top) * i) / 3) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(w, yy); ctx.stroke();
  }

  for (let i = 1; i < profile.length; i++) {
    const [p0, p1] = [profile[i - 1], profile[i]];
    if (p1[0] < from || p0[0] > to) continue;
    const d0 = Math.max(from, p0[0]);
    const d1 = Math.min(to, p1[0]);
    const a0 = heightAt(d0);
    const a1 = heightAt(d1);
    const color = gradeColor((p0[2] + p1[2]) / 2);
    const ridden = distance != null && d1 <= distance;
    const fill = ctx.createLinearGradient(0, Math.min(y(a0), y(a1)), 0, bottom);
    fill.addColorStop(0, hexToRgba(color, ridden ? 0.75 : 0.5));
    fill.addColorStop(1, hexToRgba(color, ridden ? 0.2 : 0.06));
    ctx.beginPath();
    ctx.moveTo(x(d0), y(a0)); ctx.lineTo(x(d1), y(a1)); ctx.lineTo(x(d1), bottom); ctx.lineTo(x(d0), bottom); ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.beginPath(); ctx.moveTo(x(d0), y(a0)); ctx.lineTo(x(d1), y(a1));
    neonStroke(ctx, color);
  }

  // distance marks along the bottom, the height shown on the left
  font(ctx, 10, 1);
  ctx.fillStyle = css("--muted");
  ctx.textBaseline = "alphabetic";
  const unit = imperial ? 1609.344 : 1000;
  const shownUnits = span / unit;
  const step = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10].find((st) => shownUnits / st <= Math.max(2, Math.floor(w / 90))) ?? 20;
  for (let k = Math.ceil(from / unit / step - 1e-9); k * step * unit <= to + 1e-6; k++) {
    const xx = x(k * step * unit);
    ctx.textAlign = xx < 30 ? "left" : xx > w - 30 ? "right" : "center";
    ctx.fillText(`${+(k * step).toFixed(2)}${k === 0 ? (imperial ? " MI" : " KM") : ""}`, xx, h - 6);
  }
  ctx.textAlign = "left";
  const feet = (m) => (imperial ? `${Math.round(m * 3.28084)} FT` : `${Math.round(m)} M`);
  ctx.fillText(span === length ? `+${feet(hi - lo)}` : `+${feet(hi - lo)} IN VIEW`, 4, top - 4);

  if (distance != null) {
    const d = Math.min(length, Math.max(0, distance));
    const a = heightAt(d);
    ctx.save();
    ctx.strokeStyle = css("--white");
    ctx.setLineDash([3, 4]);
    ctx.beginPath(); ctx.moveTo(x(d) + 0.5, y(a)); ctx.lineTo(x(d) + 0.5, bottom); ctx.stroke();
    ctx.restore();
    ctx.save();
    ctx.shadowColor = css("--white");
    ctx.shadowBlur = 12;
    ctx.fillStyle = css("--white");
    ctx.beginPath(); ctx.arc(x(d), y(a), 6, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }
}
