// Units for speed, distance and weight. Everything is stored metric; only the display changes.
// Power is always in watts, heart rate in bpm.

export const KG_PER_LB = 0.45359237;
export const KM_PER_MI = 1.609344;

let current = "metric";
export const setUnits = (u) => { current = u === "imperial" ? "imperial" : "metric"; };
export const units = () => current;
export const imperial = () => current === "imperial";

export const speedUnit = () => (imperial() ? "mph" : "km/h");
export const distanceUnit = () => (imperial() ? "mi" : "km");
export const weightUnit = () => (imperial() ? "lb" : "kg");

/** km/h or km → the rider's units (numbers, or null). */
export const speed = (kmh) => (kmh == null ? null : imperial() ? kmh / KM_PER_MI : kmh);
export const distance = (km) => (km == null ? null : imperial() ? km / KM_PER_MI : km);
export const weight = (kg) => (kg == null ? null : imperial() ? kg / KG_PER_LB : kg);

// Every page that imports this module waits for the rider's choice once (a local request).
try { setUnits((await (await fetch("/api/settings")).json()).units); } catch { /* metric */ }

/** A live metric tile (speed, distance) in the rider's units: {value, unit}. */
export function metricInUnits(key, value, unit) {
  if (key === "speed") return { value: speed(value), unit: speedUnit() };
  if (key === "distance") return { value: distance(value), unit: distanceUnit() };
  return { value, unit };
}
