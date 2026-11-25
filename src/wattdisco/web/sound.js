// Sound cues, made in the browser with Web Audio: plain tones, no sound files, so nothing to license.
// On or off is one of the app's settings, so it outlives the window (and any browser used with `serve`). Silent until the rider has clicked, tapped or pressed a key in this
// window, so a page nobody is using never makes a sound (some embedded browsers would play without it);
// until then `blocked()` is true and the page can say so. A ride replayed faster than real time is silent.

import { el } from "/components.js";

let ctx = null;
let on = null; // null until the settings are known: silent
let known = null; // the settings last seen
let speed = 1; // the ride clock; above 1 is a sped-up simulation
let gone = false; // the page is closing
let touched = false;
try { touched = sessionStorage.getItem("wd.touched") === "1"; } catch { /* this page only */ }
const listeners = new Set();

export const soundOn = () => on === true;

/** The server's settings and ride speed, from the settings or the live store. */
export function syncSound(settings, rideSpeed = 1) {
  speed = rideSpeed ?? 1;
  if (!settings || settings === known) return; // only a new message from the server, never undo a click still on its way
  known = settings;
  if (typeof settings.sound === "boolean" && settings.sound !== on) apply(settings.sound);
}

function apply(value) {
  on = value;
  if (!on) hush();
  for (const fn of listeners) fn();
}

export function setSound(value) {
  apply(value);
  if (on) audio();
  return fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sound: value }) }).catch(() => {});
}

/** Stop whatever is sounding, at once. */
function hush() {
  ctx?.close?.().catch(() => {});
  ctx = null;
}

function audio() {
  if (gone || !touched) return null;
  if (!ctx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    try { ctx = new Ctx(); } catch { return null; }
    ctx.addEventListener?.("statechange", () => { for (const fn of listeners) fn(); });
  }
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  return ctx;
}

/** Start audio on the first click, tap or key (and on this page at once, when that came on an earlier one). */
export function unlockSound(onChange = () => {}) {
  listeners.add(onChange);
  audio();
  const unlock = (e) => {
    if (!e.isTrusted) return;
    touched = true;
    try { sessionStorage.setItem("wd.touched", "1"); } catch { /* this page only */ }
    if (on !== false) audio();
    onChange();
  };
  for (const type of ["pointerdown", "keydown", "touchend"]) addEventListener(type, unlock, { capture: true, passive: true });
  // closing or leaving the page: nothing may sound on; coming back with the back button starts fresh
  addEventListener("pagehide", () => { gone = true; hush(); });
  addEventListener("pageshow", () => { gone = false; });
}

export const blocked = () => soundOn() && speed === 1 && (!ctx || ctx.state !== "running");
const audible = () => soundOn() && speed === 1;

const SPEAKER = "M3 8v4h3l4 3.5v-11L6 8H3z";
const WAVES = "M13 7.2a4 4 0 0 1 0 5.6M15.4 4.8a7.4 7.4 0 0 1 0 10.4";
const CROSS = "M13.5 7.5l5 5M18.5 7.5l-5 5";

/** The speaker at the end of the top strip: mutes and unmutes the cues, also with M. */
export function addSoundButton(header) {
  const button = el("button", { class: "sound-btn", type: "button", onclick: () => setSound(!soundOn()) });
  const show = () => {
    const muted = !soundOn();
    button.innerHTML = `<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"
      stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${SPEAKER}"/><path d="${muted ? CROSS : WAVES}"/></svg>`;
    button.classList.toggle("is-muted", muted);
    button.setAttribute("aria-pressed", String(!muted));
    button.setAttribute("aria-label", "Sound cues");
    button.title = muted ? "Sound is off · turn on (M)" : "Sound is on · mute (M)";
    button.hidden = on === null;
  };
  listeners.add(show);
  show();
  header.append(button);
  addEventListener("keydown", (e) => {
    if (e.key.toLowerCase() !== "m" || e.repeat || e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented || on === null) return;
    if (e.target.closest?.("input, textarea, select, .nsel")) return;
    e.preventDefault();
    setSound(!soundOn());
  });
  return button;
}

function tone(freq, ms, { gain = 0.22, at = 0, when = null, type = "sine" } = {}) {
  const a = audio();
  if (!a || a.state !== "running") return null;
  const t0 = (when ?? a.currentTime) + at / 1000;
  const osc = a.createOscillator();
  const env = a.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  // short attack and release so the tone never clicks
  env.gain.setValueAtTime(0.0001, t0);
  env.gain.exponentialRampToValueAtTime(gain, t0 + 0.012);
  env.gain.setValueAtTime(gain, t0 + ms / 1000 - 0.04);
  env.gain.exponentialRampToValueAtTime(0.0001, t0 + ms / 1000);
  osc.connect(env).connect(a.destination);
  osc.start(t0);
  osc.stop(t0 + ms / 1000 + 0.02);
  return osc;
}

/** The cues: a short tick for each of the last seconds, a higher tone when the next phase starts. */
const TICK = [880, 110, {}];
const GO = [1320, 380, { gain: 0.26 }];
const DONE = [[660, 180, {}], [880, 180, { at: 200 }], [1320, 450, { at: 400 }]];
export const cue = {
  tick: () => audible() && tone(...TICK),
  go: () => audible() && tone(...GO),
  done: () => { if (audible()) for (const t of DONE) tone(...t); },
};

const COUNT_IN = 3; // ticks in the last seconds of a phase
const AHEAD = COUNT_IN + 1.5; // seconds before a phase ends that its cues are lined up
const LATE = 0.15; // a cue lined up for longer ago than this has sounded

/**
 * Cues for a live ride, fed with the live store on every message (workout, big numbers and compare screens):
 * a tick in each of the last 3 s of a phase, a tone as the next one starts, three notes when the workout ends.
 * Nothing while paused, and nothing for what had already happened when the page opened.
 *
 * The cues are lined up on the audio clock a few seconds ahead, as soon as the phase's end is near, rather
 * than played as each message comes in: a window that is not in front (another app beside it) may get its
 * messages late or in bursts, but its audio still plays on time. The end is taken from the message that came
 * soonest (the earliest end any message points to), and the line-up is dropped on a pause, a skip or a stop.
 */
export function rideCues() {
  let heard = { index: null, done: null, ride: null };
  let plan = null; // { ride, index, end (audio time), last (no next phase: the end notes), nodes }

  const cancel = () => {
    for (const n of plan?.nodes ?? []) { try { n.stop(); n.disconnect(); } catch { /* already over, or audio closed */ } }
    plan = null;
  };
  const now = () => ctx?.currentTime ?? 0;
  const ended = () => plan && plan.end <= now() + LATE; // the phase change has sounded (or is sounding)

  function lineUp(ride, w) {
    const a = audible() ? audio() : null;
    if (!a || a.state !== "running") { cancel(); return; }
    const t = a.currentTime;
    const end = t + w.seconds_left;
    const last = !w.next;
    const before = plan && plan.ride === ride && plan.index === w.index && plan.last === last ? plan : null;
    if (before && (before.end <= end + 0.08 || before.end <= t + LATE)) return; // as lined up, or already sounded
    const sounded = (k) => before && before.end - k <= t; // that tick has played: never twice
    for (const n of plan?.nodes ?? []) { try { n.stop(); n.disconnect(); } catch { /* already over */ } }
    const nodes = [];
    if (!last) {
      for (let k = COUNT_IN; k >= 1; k--) {
        if (end - k > t - 0.05 && !sounded(k)) nodes.push(tone(TICK[0], TICK[1], { when: Math.max(end - k, t) }));
      }
      nodes.push(tone(GO[0], GO[1], { ...GO[2], when: end }));
    } else {
      for (const [f, ms, o] of DONE) nodes.push(tone(f, ms, { ...o, when: end }));
    }
    plan = { ride, index: w.index, end, last, nodes: nodes.filter(Boolean) };
  }

  return (store) => {
    syncSound(store.settings, store.speed);
    const s = store.state;
    const w = s?.workout;
    if (!w) return;
    const ride = s.ride?.id ?? null;
    if (!s.ride?.active || s.ride.paused) {
      cancel();
      heard = { ...heard, index: w.index, ride };
      return;
    }
    if (heard.ride !== ride) { cancel(); heard = { index: w.index, done: w.done, ride }; }
    if (w.done) {
      if (!heard.done && !(plan?.last && ended())) cue.done();
      if (!(plan?.last && ended())) cancel();
      plan = null;
      heard.done = true;
      return;
    }
    heard.done = false;
    if (w.index !== heard.index) {
      // the next phase began: its tone was lined up (and has sounded), or it came early (a skip), or never lined up
      const sounded = plan && plan.index === heard.index && ended();
      if (!sounded) { cancel(); if (heard.index != null) cue.go(); }
      plan = null;
      heard = { ...heard, index: w.index };
    }
    const left = w.seconds_left ?? 99;
    const timed = !store.workout?.climb; // a climb's end is an estimate from speed: its notes come as the top is reached
    if (timed && left > 0 && left <= AHEAD) lineUp(ride, w);
    else if (plan) cancel(); // the phase got longer again (or a new one): nothing lined up for now
  };
}
