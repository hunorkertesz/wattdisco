// Ride controls that stay out of the way: docks that appear on hover, plus keyboard shortcuts.
//
// Each dock has a hover zone where it appears (top or bottom of the chart). It shows while
// the pointer is over that zone or the dock, or a dock button has keyboard focus, and fades shortly
// after the pointer leaves, so the riding view shows only data. On touch screens a tap in
// the zone shows it for a few seconds. A thin glowing handle in each zone is the hint.
// Shortcuts work without showing anything. The app gets "<dock id>-open" while a dock shows.

const LEAVE_MS = 700; // pointer left: fade after this
const TOUCH_MS = 4000; // tapped open on a touch screen: fade after this

export function setupDock({ app, docks, keys = {} }) {
  const entries = docks.map(({ dock, zone, pinned = () => false }) => ({ dock, zone, pinned, timer: null }));
  // keyboard focus keeps a dock open; a button that was clicked keeps focus too, but must not
  const held = (e) => e.pinned() || e.dock.matches(":hover") || e.zone.matches(":hover") || !!e.dock.querySelector(":focus-visible");

  function open(e) {
    clearTimeout(e.timer);
    e.dock.classList.add("is-open");
    app.classList.add(`${e.dock.id}-open`);
  }
  function close(e, force = false) {
    clearTimeout(e.timer);
    if (!force && held(e)) return;
    e.dock.classList.remove("is-open");
    app.classList.remove(`${e.dock.id}-open`);
  }
  const later = (e, ms) => { clearTimeout(e.timer); e.timer = setTimeout(() => close(e), ms); };

  for (const e of entries) {
    for (const node of [e.zone, e.dock]) {
      node.addEventListener("pointerenter", (ev) => { if (ev.pointerType === "mouse" || ev.pointerType === "pen") open(e); });
      node.addEventListener("pointerleave", (ev) => { if (ev.pointerType !== "touch") later(e, LEAVE_MS); });
    }
    e.zone.addEventListener("pointerdown", (ev) => { if (ev.pointerType === "touch") { open(e); later(e, TOUCH_MS); } });
    e.zone.querySelector("button")?.addEventListener("click", () => { open(e); later(e, TOUCH_MS); });
    e.dock.addEventListener("focusin", () => { if (e.dock.querySelector(":focus-visible")) open(e); });
    e.dock.addEventListener("focusout", () => later(e, LEAVE_MS));
  }

  addEventListener("keydown", (ev) => {
    if (ev.metaKey || ev.ctrlKey || ev.altKey || ev.defaultPrevented) return;
    if (ev.target.closest?.("input, textarea, select")) return;
    const key = ev.key === " " ? "space" : ev.key.toLowerCase();
    if (key === "escape") {
      document.activeElement?.blur?.();
      for (const e of entries) close(e, !e.pinned());
      return;
    }
    const action = keys[key];
    if (action) {
      if (key === "space" && ev.target.closest?.("button, a")) return; // Space on a focused button presses it
      ev.preventDefault();
      action();
    }
  });

  // show the pinned docks now (e.g. End ride once the workout is over); they stay while pinned
  return { show: () => { for (const e of entries) if (e.pinned()) open(e); } };
}

// A button that needs a second tap within 3 s before it acts (End ride).
export function confirmTwice(button, { confirm, busy, action }) {
  let armed = null;
  const html = button.innerHTML; // keeps a shortcut hint like <kbd>S</kbd>
  const reset = () => { button.innerHTML = html; };
  button.addEventListener("click", async () => {
    if (!armed) {
      button.textContent = confirm;
      armed = setTimeout(() => { armed = null; reset(); }, 3000);
      return;
    }
    clearTimeout(armed);
    armed = null;
    button.disabled = true;
    button.textContent = busy;
    try {
      await action();
    } finally {
      button.disabled = false;
      reset();
    }
  });
}

// A button that acts only when held (End ride): it fills up over `ms` while pressed, and letting go early does
// nothing, so a gloved tap or a brush against the screen never ends a ride. From the keyboard (Enter, Space) it asks
// for a second press within 3 s instead, as before.
export function holdToConfirm(button, { hold = "Hold to end", confirm, busy, action, ms = 1200 }) {
  const html = button.innerHTML;
  let timer = null;
  let armed = null;
  let done = false;
  button.classList.add("hold-btn");
  button.title = `${hold} (keyboard: press twice)`;
  const reset = () => { button.innerHTML = html; button.style.setProperty("--hold", "0"); button.classList.remove("is-holding"); };
  const run = async () => {
    done = true;
    button.disabled = true;
    button.textContent = busy;
    try { await action(); } finally { button.disabled = false; done = false; reset(); }
  };
  const start = (e) => {
    if (e.button > 0 || done) return;
    e.preventDefault();
    try { button.setPointerCapture(e.pointerId); } catch { /* some pointers cannot be captured */ }
    button.classList.add("is-holding");
    button.textContent = hold;
    const t0 = performance.now();
    const tick = () => {
      const p = Math.min(1, (performance.now() - t0) / ms);
      button.style.setProperty("--hold", String(p));
      if (p >= 1) { timer = null; run(); return; }
      timer = requestAnimationFrame(tick);
    };
    timer = requestAnimationFrame(tick);
  };
  const stop = () => { if (timer) { cancelAnimationFrame(timer); timer = null; reset(); } };
  button.addEventListener("pointerdown", start);
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) button.addEventListener(type, stop);
  button.addEventListener("click", (e) => {
    if (e.detail !== 0 || done) return; // a pointer: the hold decides
    if (!armed) {
      button.textContent = confirm;
      armed = setTimeout(() => { armed = null; reset(); }, 3000);
      return;
    }
    clearTimeout(armed);
    armed = null;
    run();
  });
}

// End the ride and open its summary.
export async function endRide() {
  const r = await fetch("/api/ride/stop", { method: "POST" });
  if (r.ok) location.href = `/summary.html?ride=${(await r.json()).id}`;
}
