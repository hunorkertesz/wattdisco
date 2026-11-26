// Shared UI pieces for every page: logo, top strip, device chips, chip groups, formatting.

let uid = 0;

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value == null) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child != null) node.append(child);
  }
  return node;
}

// Disco ball with bolt. `detailed` is the big launch version with thin lines.
export function logoSvg(size = 34, { detailed = false } = {}) {
  const id = `wd-glow-${uid++}`;
  const ball = detailed
    ? `<g filter="url(#${id})" fill="none" stroke="#3DD9FF" stroke-width="2.5" stroke-linecap="round">
         <circle cx="0" cy="8" r="78"/><path d="M-78 8H78M-70 -24H70M-70 40H70M-48 -50H48M-48 66H48"/>
         <ellipse cx="0" cy="8" rx="30" ry="78"/><ellipse cx="0" cy="8" rx="58" ry="78"/><path d="M0 -70V-100"/></g>
       <path d="M12 -48L-26 10L-4 10L-14 62L30 -6L6 -6L22 -48Z" fill="#0A0612" stroke="#FFC93C"
         stroke-width="3.5" stroke-linejoin="round" filter="url(#${id})"/>`
    : `<g filter="url(#${id})" fill="none" stroke="#3DD9FF" stroke-width="9" stroke-linecap="round">
         <circle cx="0" cy="8" r="78"/><path d="M-78 8H78M-70 -24H70M-70 40H70"/>
         <ellipse cx="0" cy="8" rx="34" ry="78"/></g>
       <path d="M12 -48L-26 10L-4 10L-14 62L30 -6L6 -6L22 -48Z" fill="#070B0A" stroke="#FFC93C"
         stroke-width="10" stroke-linejoin="round" filter="url(#${id})"/>`;
  const blur = detailed ? 3 : 7;
  const wrap = document.createElement("span");
  wrap.innerHTML = `<svg width="${size}" height="${size}" viewBox="-110 -110 220 220" aria-hidden="true">
    <defs><filter id="${id}" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="${blur}" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
    ${ball}</svg>`;
  return wrap.firstElementChild;
}

export function logo({ href = "/" } = {}) {
  return el(
    "a",
    { class: "logo", href, "aria-label": "Watt Disco home" },
    logoSvg(34),
    el("span", { class: "logo__words" },
      el("span", { class: "logo__watt", text: "Watt" }),
      el("span", { class: "logo__disco", text: "DISCO" }),
    ),
  );
}

// Pages that exist so far. The others show as disabled tabs until their build step.
const TABS = [
  { id: "devices", label: "DEVICES", href: "/connect.html", ready: true },
  { id: "train", label: "TRAIN", href: "/train", ready: true }, // the ride if one is running, else the ready screen
  { id: "plan", label: "PLAN", href: "/plan.html", ready: true },
  { id: "workouts", label: "WORKOUTS", href: "/editor.html", ready: true },
  // only on its own screen: reached from the ride (dock, C) and from a ride's summary
  { id: "compare", label: "COMPARE", href: "/compare.html", ready: true, onlyActive: true },
  { id: "zones", label: "ZONES", href: "/zones.html", ready: true },
  { id: "progress", label: "PROGRESS", href: "/progress.html", ready: true },
  { id: "history", label: "HISTORY", href: "/history.html", ready: true },
];

// Fills a <header class="strip"> with logo, tabs and an empty device chip area.
export function renderStrip(header, { active }) {
  const tabs = el("nav", { class: "strip__tabs", "aria-label": "Main" },
    TABS.filter((t) => !t.onlyActive || t.id === active).map((t) =>
      t.ready
        ? el("a", { class: `tab${t.id === active ? " is-active" : ""}`, href: t.href,
            "aria-current": t.id === active ? "page" : null, text: t.label })
        : el("span", { class: "tab", "aria-disabled": "true", title: "Coming in a later build step", text: t.label }),
    ),
  );
  const devices = el("div", { class: "strip__devices", "aria-live": "polite" });
  header.replaceChildren(logo(), tabs, devices);
  return { devices };
}

// One chip per sensor in use: green ok, amber no signal, red lost. Found-only devices are left out.
export function renderDeviceChips(container, devices) {
  container.replaceChildren(
    ...devices.filter((d) => d.state !== "found" && d.state !== "disconnected").map((d) => {
      let kind = "ok";
      let status = "";
      if (d.state === "lost") { kind = "lost"; status = "LOST"; }
      else if (d.state !== "connected") { kind = ""; status = d.state.toUpperCase(); }
      else if (d.no_data) { kind = "warn"; status = "NO SIGNAL"; }
      const name = d.name.toUpperCase();
      return el("span", { class: `dev-chip${kind ? ` dev-chip--${kind}` : ""}`, title: status ? `${d.name}: ${status}` : d.name },
        el("span", { class: "dot" }),
        el("span", { class: "dev-chip__name", text: name }),
        status ? el("span", { text: `· ${status}` }) : null,
      );
    }),
  );
  // Names go when they do not fit; the dots (and any problem) stay, the name is in the tooltip.
  // (Measured from the chips, because right-aligned overflow does not show in scrollWidth.)
  container.classList.remove("is-compact");
  const gap = parseFloat(getComputedStyle(container).columnGap) || 0;
  const chips = [...container.children];
  const needed = chips.reduce((sum, c) => sum + c.offsetWidth, 0) + gap * Math.max(0, chips.length - 1);
  if (needed > container.clientWidth) container.classList.add("is-compact");
}

// Group of pill buttons where exactly one is pressed (e.g. RAW / 3 S / 10 S).
export function chipGroup(options, { value, onChange, label, size = "" }) {
  const group = el("div", { class: "chip-group", role: "group", "aria-label": label });
  const buttons = options.map((opt) =>
    el("button", { class: `chip${size ? ` chip--${size}` : ""}`, type: "button", "data-value": opt.value,
      onclick: () => { set(opt.value); onChange?.(opt.value); }, text: opt.label }),
  );
  function set(v) {
    for (const b of buttons) b.setAttribute("aria-pressed", String(b.dataset.value === v));
  }
  group.append(...buttons);
  set(value);
  group.set = set;
  return group;
}

// ---------- workout phases ----------

export const PHASE_LABEL = {
  warmup: "WARM-UP",
  endurance: "ENDURANCE",
  tempo: "TEMPO",
  sweetspot: "SWEET SPOT",
  threshold: "THRESHOLD",
  vo2: "VO2",
  sprint: "SPRINT",
  recovery: "RECOVERY",
  cooldown: "COOL-DOWN",
  test: "FTP TEST",
  climb: "CLIMB",
};

// A climb's gradient bands: the app's own neon, cool to hot as the road gets steeper (teal, cyan, pink,
// magenta, then white-hot), apart from the power zone colours: zone colours always mean power.
export const GRADE_BANDS = [
  { upTo: 0, label: "≤ 0 %", color: "#2A9D8F" },
  { upTo: 3, label: "0–3 %", color: "#36E3C4" },
  { upTo: 6, label: "3–6 %", color: "#3DD9FF" },
  { upTo: 9, label: "6–9 %", color: "#FF3D81" },
  { upTo: 12, label: "9–12 %", color: "#FF2DD4" },
  { upTo: Infinity, label: "12 %+", color: "#FFF6E5" },
];
export function gradeColor(grade) {
  return GRADE_BANDS.find((b) => grade <= b.upTo).color;
}

// Effort zones by power as a fraction of FTP: blue (very easy) to purple (VO2 max).
// The boundaries come from the server settings (config.py); colours live in theme.css
// as --zone-1 … --zone-6. These defaults only cover the moment before the server answers.
let zoneTops = [0.55, 0.75, 0.87, 0.94, 1.05];

export function setZoneTops(tops) {
  if (Array.isArray(tops) && tops.length) zoneTops = tops;
}

export function getZoneTops() {
  return zoneTops;
}

export function zoneOf(watts, ftp) {
  const pct = watts / ftp;
  const i = zoneTops.findIndex((top) => pct <= top + 1e-9);
  return i === -1 ? zoneTops.length + 1 : i + 1;
}

const zoneColors = {};
export function zoneColor(n) {
  if (!(n in zoneColors)) {
    zoneColors[n] = getComputedStyle(document.documentElement).getPropertyValue(`--zone-${n}`).trim() || "#8AA398";
  }
  return zoneColors[n];
}

export function effortColor(watts, ftp) {
  return zoneColor(zoneOf(watts, ftp));
}

// Options for a workout picker, grouped by category (Endurance, Tempo, … My workouts).
// Lengths a workout list can be narrowed to (minutes, upper bound inclusive).
export const LENGTHS = [
  { value: "all", label: "ALL" },
  { value: "short", label: "≤ 45 MIN", test: (s) => s <= 45 * 60 },
  { value: "mid", label: "46–75", test: (s) => s > 45 * 60 && s <= 75 * 60 },
  { value: "long", label: "75+", test: (s) => s > 75 * 60 },
];

/** Picker options: the plan's next session (`suggested`) first, then ★ favourites, then by category. `length` narrows
 *  by duration, `type` to one category; `keep` always stays. */
export function workoutOptions(workouts, categories, { favourites = [], length = "all", type = "all", keep = null, suggested = null } = {}) {
  const fav = new Set(favourites);
  const next = workouts.find((w) => w.id === suggested); // the plan's next session, first and whatever the filters say
  const fits = LENGTHS.find((l) => l.value === length)?.test ?? (() => true);
  const ofType = (w) => type === "all" || (w.category ?? "custom") === type;
  const shown = workouts.filter((w) => w.id === keep || (fits(w.seconds ?? 0) && ofType(w)));
  const option = (w, group) => ({ value: w.id, label: `${w.name} · ${Math.round((w.seconds ?? 0) / 60)} min`, group });
  const rest = shown.filter((w) => w !== next);
  return [
    ...(next ? [option(next, "Plan · next session")] : []),
    ...rest.filter((w) => fav.has(w.id)).map((w) => option(w, "★ Favourites")),
    ...categories.flatMap((c) => rest.filter((w) => !fav.has(w.id) && (w.category ?? "custom") === c.id).map((w) => option(w, c.label))),
  ];
}

// ---------- neon dropdown ----------

let selectSeq = 0;

/**
 * A dropdown in the app's neon style (native <select> menus cannot be styled on macOS).
 * options: [{value, label, group?, color?}]. With `placeholder` and no value it works as a menu.
 * Keyboard: arrows, Home/End, type a letter to jump, Enter/Space to choose, Escape to close.
 */
export function neonSelect({ label, options = [], value = null, placeholder = "", onChange, compact = false }) {
  const id = `nsel-${++selectSeq}`;
  const menu = Boolean(placeholder) && value === null; // acts on a pick, keeps showing its label
  const button = el("button", { class: "nsel__button", type: "button",
    "aria-haspopup": "listbox", "aria-expanded": "false", "aria-controls": `${id}-list`, "aria-label": label });
  const text = el("span", { class: "nsel__text" });
  button.append(text, el("span", { class: "nsel__chevron", "aria-hidden": "true" }));
  const list = el("div", { class: "nsel__list", id: `${id}-list`, role: "listbox", "aria-label": label, tabindex: "-1", hidden: true });
  const root = el("div", { class: `nsel${compact ? " nsel--compact" : ""}` }, button, list);
  let current = value;
  let items = [];

  function build() {
    list.replaceChildren();
    items = [];
    let group = null;
    options.forEach((o, i) => {
      if (o.group && o.group !== group) {
        group = o.group;
        list.append(el("div", { class: "nsel__group mono", role: "presentation", text: o.group }));
      }
      const opt = el("div", { class: "nsel__option", role: "option", id: `${id}-o${i}`, "data-value": o.value },
        o.color ? el("i", { class: "nsel__dot", style: `--c:${o.color}` }) : null,
        el("span", { text: o.label }));
      opt.addEventListener("click", () => choose(o.value));
      opt.addEventListener("pointermove", () => focus(items.indexOf(opt)));
      list.append(opt);
      items.push(opt);
    });
    sync();
  }

  function sync() {
    const o = options.find((x) => x.value === current);
    text.textContent = o ? o.label : placeholder;
    for (const item of items) item.setAttribute("aria-selected", String(item.dataset.value === String(current)));
  }

  let active = -1;
  function focus(i) {
    if (!items.length) return;
    active = Math.max(0, Math.min(items.length - 1, i));
    items.forEach((item, k) => item.classList.toggle("is-active", k === active));
    list.setAttribute("aria-activedescendant", items[active].id);
    items[active].scrollIntoView({ block: "nearest" });
  }

  function open() {
    if (!items.length) return;
    list.hidden = false;
    button.setAttribute("aria-expanded", "true");
    // open upwards when there is no room below
    const r = button.getBoundingClientRect();
    root.classList.toggle("nsel--up", innerHeight - r.bottom < Math.min(320, list.scrollHeight + 16) && r.top > innerHeight - r.bottom);
    focus(Math.max(0, items.findIndex((it) => it.dataset.value === String(current))));
    list.focus();
    addEventListener("pointerdown", outside, true);
  }

  function close(refocus = true) {
    list.hidden = true;
    button.setAttribute("aria-expanded", "false");
    removeEventListener("pointerdown", outside, true);
    if (refocus) button.focus();
  }

  function outside(e) {
    if (!root.contains(e.target)) close(false);
  }

  function choose(v) {
    const changed = v !== current;
    if (!menu) current = v;
    close();
    sync();
    if (changed || menu) onChange?.(v);
  }

  button.addEventListener("click", () => (list.hidden ? open() : close()));
  button.addEventListener("keydown", (e) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); open(); }
  });
  let typed = "";
  let typedAt = 0;
  list.addEventListener("keydown", (e) => {
    const keys = { ArrowDown: active + 1, ArrowUp: active - 1, Home: 0, End: items.length - 1, PageDown: active + 8, PageUp: active - 8 };
    if (e.key in keys) { e.preventDefault(); focus(keys[e.key]); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (active >= 0) choose(options[active].value); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "Tab") close(false);
    else if (e.key.length === 1) {
      typed = performance.now() - typedAt < 700 ? typed + e.key.toLowerCase() : e.key.toLowerCase();
      typedAt = performance.now();
      const hit = options.findIndex((o) => o.label.toLowerCase().startsWith(typed));
      if (hit >= 0) focus(hit);
    }
    e.stopPropagation(); // keep page shortcuts (Space = pause) out of the open list
  });

  root.set = (v) => { current = v; sync(); };
  root.setOptions = (next) => {
    const signature = JSON.stringify(next);
    if (signature === root.dataset.signature) return;
    root.dataset.signature = signature;
    options = next;
    build();
  };
  root.get = () => current;
  root.button = button;
  root.setOptions(options);
  return root;
}

export function phaseWatts(phase) {
  return phase.watts_end == null ? `${phase.watts} W` : `${phase.watts}–${phase.watts_end} W`;
}

// An "i" button that shows and hides an explanation (how a number is calculated).
// `panel` is the element to show; it gets hidden at first. Returns the button.
export function infoButton(panel, label) {
  panel.hidden = true;
  panel.id ||= `info-${++uid}`;
  const button = el("button", { class: "info-btn", type: "button", "aria-expanded": "false", "aria-controls": panel.id,
    "aria-label": label, title: label, text: "i" });
  button.addEventListener("click", () => {
    const open = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", String(open));
    panel.hidden = !open;
    panel.dispatchEvent(new CustomEvent("info-toggle", { detail: { open } }));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) { panel.hidden = true; button.setAttribute("aria-expanded", "false"); }
  });
  return button;
}

// Critical power next to the FTP (Progress and Zones): CP, W′, a ramp test suggestion and an info
// button that explains the calculation. `cp` is what /api/critical-power returns.
export function criticalPowerLine(cp) {
  const LABEL = { 180: "3 min", 300: "5 min", 600: "10 min", 1200: "20 min" };
  const when = (iso) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  const used = cp.points.map((p) => `${LABEL[p.seconds]} ${Math.round(p.watts)} W (${when(p.date)})`).join(" · ");
  const panel = el("div", { class: "info-text cp__info" },
    el("p", {}, el("b", { text: "Critical power (CP)" }), " is the highest power you can keep up for a long time. ",
      el("b", { text: "W′" }), " (W prime) is the extra work you can do above it before you run out, like a battery that refills below CP."),
    el("p", {}, el("b", { text: "How: " }), `your best 3, 5, 10 and 20 minutes of the last ${cp.days} days (real rides only). `
      + "The work of each effort (power × time) lies close to a straight line: work = CP × time + W′. "
      + "A least-squares line through those points gives CP (its slope) and W′ (where it starts)."),
    el("p", {}, `CP usually sits about 5 % above FTP. When it is 8 % or more above your FTP, your FTP is probably out of date `
      + "and a ramp test is suggested. It only works when those bests were all-out efforts. ERG intervals at a set power are not: "
      + "then the points don't line up (no CP), or CP lands well under your FTP (shown with a ?), which says little about your FTP. "
      + "A 3 min and a 12–20 min all-out effort in free ride give the best fit."),
    el("p", { class: "cp__used", text: used ? `Used: ${used}${cp.miss_pct != null ? ` · every point within ${cp.miss_pct} % of the line` : ""}` : "No 3–20 min bests yet." }));
  const head = cp.status === "ok"
    ? [el("b", { text: `CP ${cp.cp} W` }), el("span", { text: `W′ ${cp.w_prime_kj} kJ · ${Math.round(cp.ratio * 100)} % of FTP` })]
    : cp.status === "below_ftp"
      ? [el("b", { class: "cp__weak", text: `CP ${cp.cp} W?` }), el("span", { class: "cp__why", text: cp.message })]
      : [el("b", { text: "CP --" }), el("span", { class: "cp__why", text: cp.message })];
  const line = el("div", { class: "cp" },
    el("div", { class: "cp__row" }, head[0], infoButton(panel, "How critical power is calculated"), el("span", { class: "cp__text" }, head.slice(1))),
    cp.suggest_test ? el("div", { class: "cp__suggest" }, `Your ${cp.message}. `,
      el("a", { href: "/ready.html?workout=ftp-ramp-test", text: "Ride a ramp test ›" })) : null,
    panel);
  return line;
}

// ---------- formatting ----------

export function fmtClock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${String(m).padStart(2, "0")}:${sec}`;
}

// Battery level of a device: a small battery with the %, warning colours when low.
export const LOW_BATTERY = 20;
export function batteryBadge(pct) {
  if (pct == null) return el("span", { class: "batt batt--none" });
  const level = pct <= LOW_BATTERY ? " batt--low" : pct <= 40 ? " batt--mid" : "";
  return el("span", { class: `batt${level}`, title: `Battery ${pct} %`, "aria-label": `Battery ${pct} %` },
    el("span", { class: "batt__cell", "aria-hidden": "true" }, el("i", { style: `width: ${Math.max(6, pct)}%` })),
    el("span", { class: "batt__pct", text: `${pct}%` }));
}

// Signal strength as 0–4 bars from RSSI (dBm).
export function signalBars(rssi) {
  const level = rssi == null ? 0 : rssi > -60 ? 4 : rssi > -70 ? 3 : rssi > -80 ? 2 : 1;
  const wrap = document.createElement("span");
  wrap.innerHTML = `<svg width="22" height="16" viewBox="0 0 22 16" role="img" aria-label="Signal ${level} of 4">${
    [0, 1, 2, 3].map((i) => `<rect x="${i * 6}" y="${12 - i * 4}" width="4" height="${4 + i * 4}" fill="${i < level ? "#FFF6E5" : "#3A2660"}"/>`).join("")
  }</svg>`;
  return wrap.firstElementChild;
}

export const ROLE_LABEL = { power: "Power", cadence: "Cadence", control: "Control", heart_rate: "Heart rate", temperature: "Temperature" };

export function fmtNum(v, digits = 0) {
  return v == null ? "--" : Number(v).toFixed(digits);
}

// Downloads (.fit, comparison CSV). In the app window a download link would open the
// file in place with no way back, so Python shows a Save dialog and writes the file.
// In a browser the link downloads as usual.
export function toast(text, { warn = false, action = null, ms = 4000 } = {}) {
  let box = document.querySelector(".toast");
  if (!box) box = document.body.appendChild(el("div", { class: "toast", role: "status", "aria-live": "polite" }));
  box.replaceChildren(text);
  if (action) {  // e.g. Undo: one button in the toast, which then closes
    box.append(" ", el("button", { class: "toast__action", type: "button", text: action.label,
      onclick: () => { box.classList.remove("is-on"); action.onClick(); } }));
  }
  box.classList.toggle("is-warn", warn);
  box.classList.add("is-on");
  clearTimeout(box._timer);
  box._timer = setTimeout(() => box.classList.remove("is-on"), ms);
}

document.addEventListener("click", async (e) => {
  const link = e.target.closest?.("a[download]");
  const api = window.pywebview?.api;
  if (!link || !api?.save_file) return;
  e.preventDefault();
  const url = new URL(link.href, location.href);
  const result = await api.save_file(url.pathname + url.search);
  if (result?.ok) toast(`Saved · ${result.path}`);
  else if (!result?.cancelled) toast(`Could not save · ${result?.error ?? "unknown error"}`, { warn: true });
});

// In a browser (wattdisco serve) the screen wake lock keeps the display on while a ride runs; the app window
// does the same through the system (awake.py). The browser drops the lock when the page is hidden: take it back.
let wake = null;
let wantWake = false;
export function holdScreen(on) {
  wantWake = !!on;
  if (!("wakeLock" in navigator)) return;
  if (wantWake && !wake && document.visibilityState === "visible") {
    wake = "pending";
    navigator.wakeLock.request("screen").then((lock) => {
      wake = lock;
      lock.addEventListener("release", () => { wake = null; });
      if (!wantWake) lock.release().catch(() => {});
    }, () => { wake = null; });
  } else if (!wantWake && wake && wake !== "pending") {
    wake.release().catch(() => {});
    wake = null;
  }
}
document.addEventListener("visibilitychange", () => holdScreen(wantWake));

/**
 * What to do about a device that is not working: one plain sentence, or "" when all is well.
 * `connectingFor` is how long (ms) it has been connecting; `missing` is a last-used device the scan did not find.
 */
export function deviceHelp(d, { connectingFor = 0, missing = false } = {}) {
  const caps = d.capabilities ?? [];
  const trainer = caps.includes("control");
  const strap = caps.includes("heart_rate") && !caps.includes("power");
  const meter = caps.includes("power") && !trainer;
  const wake = strap ? "Wet the strap's sensor pads and put it on: it only wakes on skin."
    : meter ? "Pedal a few strokes: power meters sleep when the cranks don't turn."
    : trainer ? "Check the trainer has power and pedal a few strokes."
    : "Wake it up and keep it within a few metres.";
  if (missing) return `Not found. ${wake}`;
  if (d.state === "lost") return `Connection lost, reconnecting. ${wake}`;
  if (d.state === "connecting" && connectingFor > 15000) {
    return trainer
      ? "Taking long: smart trainers take only a few Bluetooth connections. Close other training apps and disconnect it on your phone, or connect the power meter and strap first."
      : "Taking long: close other apps or phones that may be connected to it, then try again.";
  }
  if (d.state === "connected" && d.no_data) {
    return strap ? "No heart rate coming in. Wet the sensor pads and wear the strap snug, just under the chest."
      : `No data coming in. ${wake}`;
  }
  return "";
}

// How hard a ride felt (1–10) as a colour: easy blue/green up to all-out purple, like the power zones.
export function rpeColor(rpe) {
  return zoneColor(rpe <= 2 ? 1 : rpe <= 4 ? 2 : rpe <= 5 ? 3 : rpe <= 6 ? 4 : rpe <= 8 ? 5 : 6);
}

/**
 * A short explanation shown the first time a view opens (remembered per browser), and again on demand.
 * items: [[term, what it means], …]. Works with touch: no hover needed. Returns { open, maybeOpen }.
 */
export function helpCard(key, { title, items, note = "" }) {
  const seen = () => { try { return localStorage.getItem(`wd.help.${key}`) === "1"; } catch { return true; } };
  let card = null;
  const close = () => {
    card?.remove();
    card = null;
    try { localStorage.setItem(`wd.help.${key}`, "1"); } catch { /* shows again next time */ }
    removeEventListener("keydown", onKey, true);
  };
  const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } };
  const open = () => {
    if (card) return;
    card = el("div", { class: "help-card", role: "dialog", "aria-modal": "false", "aria-label": title },
      el("div", { class: "help-card__head" }, el("span", { class: "mono", text: title }),
        el("button", { class: "btn btn--sm btn--primary", type: "button", text: "Got it", onclick: close })),
      el("dl", { class: "help-card__list" }, ...items.flatMap(([term, text]) => [el("dt", { text: term }), el("dd", { text })])),
      note ? el("p", { class: "help-card__note", text: note }) : null);
    document.body.append(card);
    addEventListener("keydown", onKey, true);
    card.querySelector("button").focus();
  };
  return { open, maybeOpen: () => { if (!seen()) open(); } };
}

// ---------- enlarge a panel ----------
// Any panel can fill the whole window for a closer look: ⤢ in its corner enlarges it, "‹ Back" (or Escape)
// puts it back. The enlarged panel covers the page in place, so it works in any layout; `onChange` redraws
// what the page draws itself (canvas charts), after the panel has its new size.

let zoomed = null;
let zoomKeys = false;

function setZoom(panel) {
  const before = zoomed;
  before?.classList.remove("is-zoomed");
  zoomed = panel;
  panel?.classList.add("is-zoomed");
  document.body.classList.toggle("has-zoom", !!panel);
  for (const p of [before, panel]) p?._zoom?.show();
  requestAnimationFrame(() => { for (const p of new Set([before, panel])) p?._zoom?.onChange?.(); });
  (panel ?? before)?._zoom?.button.focus({ preventScroll: true });
}

/** The ⤢ / "‹ Back" button for one panel; the page puts it where it fits (usually the panel's top right). */
export function zoomButton(panel, { label = "this panel", onChange } = {}) {
  const button = el("button", { class: "btn btn--sm btn--icon zoom-btn", type: "button",
    onclick: () => setZoom(zoomed === panel ? null : panel), onpointerenter: () => show(), onfocus: () => show() });
  const show = () => {
    const on = zoomed === panel;
    button.textContent = on ? "‹ Back" : "⤢";
    button.classList.toggle("btn--icon", !on);
    const name = (typeof label === "function" ? label() : label) || "this panel";
    button.title = on ? "Back (Esc)" : `Show ${name} on the whole window`;
    button.setAttribute("aria-label", on ? "Back to all panels" : `Enlarge ${name}`);
    button.setAttribute("aria-expanded", String(on));
  };
  panel._zoom = { button, show, onChange };
  show();
  if (!zoomKeys) {
    zoomKeys = true;
    addEventListener("keydown", (e) => {
      if (e.key === "Escape" && zoomed && !e.defaultPrevented) { e.preventDefault(); setZoom(null); }
    });
  }
  return button;
}

// "Copy diagnostics": the app's version, devices and the end of its log (/api/diagnostics), shown before it is
// copied, so the rider sees what they paste into a bug report. Nothing is sent anywhere.
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* the app window may not allow it */ }
  const area = el("textarea", { class: "visually-hidden" });
  area.value = text;
  document.body.append(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  area.remove();
  return ok;
}

export function diagnosticsButton() {
  let card = null;
  const close = () => { card?.remove(); card = null; removeEventListener("keydown", onKey, true); };
  const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } };
  const open = async () => {
    if (card) return;
    const text = await fetch("/api/diagnostics").then((r) => r.json()).then((d) => d.text)
      .catch(() => "The app didn't answer, so no diagnostics could be read.");
    const area = el("textarea", { class: "diag__text", readonly: "", spellcheck: "false", "aria-label": "Diagnostics" });
    area.value = text;
    const status = el("span", { class: "mono diag__status", role: "status" });
    const copy = el("button", { class: "btn btn--sm btn--primary", type: "button", text: "Copy" });
    copy.onclick = async () => {
      const ok = await copyText(text);
      if (!ok) { area.focus(); area.select(); }
      status.textContent = ok ? "Copied · paste it into your bug report" : "Selected · press Ctrl+C or ⌘C to copy";
    };
    card = el("div", { class: "help-card diag", role: "dialog", "aria-modal": "false", "aria-label": "Diagnostics" },
      el("div", { class: "help-card__head" }, el("span", { class: "mono", text: "Diagnostics" }),
        el("div", { class: "diag__actions" }, copy, el("button", { class: "btn btn--sm", type: "button", text: "Close", onclick: close }))),
      el("p", { class: "help-card__note", text: "App and system versions, your devices and the end of the app's log. "
        + "No passwords, sign-ins or ride data. Read it before you send it; nothing leaves your computer unless you paste it somewhere." }),
      area, status);
    document.body.append(card);
    addEventListener("keydown", onKey, true);
    copy.focus();
  };
  return el("button", { class: "btn", type: "button", text: "Copy diagnostics", onclick: open,
    title: "Versions, devices and the app's log, to paste into a bug report" });
}
