"use strict";

const energyRails = [
  { key: "pv", name: "光伏输入", color: "solar" },
  { key: "battery", name: "蓄电池", color: "battery" },
  { key: "load", name: "负载输出", color: "load" },
];
const energyUnits = { v: "V", a: "A", w: "W" };

// Each RX frame is a snapshot. Missing channels and error frames create gaps.
function parseEnergy(row) {
  const point = { id: row.id, time: Date.parse(row.at), values: {}, cells: [], alarm: "" };
  let data;
  try { data = JSON.parse(row.message); } catch { return point; }
  if (data?.bb !== 1 || data.part !== "power" || data.status !== "sample") return point;
  const value = data.value;
  if (!value || typeof value !== "object" || Array.isArray(value)) return point;
  for (const { key } of energyRails) {
    for (const unit of Object.keys(energyUnits)) {
      const k = key + "_" + unit;
      if (Number.isFinite(value[k])) point.values[k] = value[k];
    }
  }
  for (const k of ["soc_pct", "soh_pct", "battery_c", "controller_c"]) {
    if (Number.isFinite(value[k]) && (!k.endsWith("_pct") || (value[k] >= 0 && value[k] <= 100)))
      point.values[k] = value[k];
  }
  if (Array.isArray(value.cells_v) && value.cells_v.length <= 64)
    point.cells = value.cells_v.map(v => Number.isFinite(v) && v >= 0 ? v : null);
  if (typeof value.alarm === "string") point.alarm = value.alarm.slice(0, 160);
  return point;
}
if (typeof module !== "undefined") module.exports = { parseEnergy };
