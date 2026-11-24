"use strict";

function runtimeValues(state) {
  const parts = new Map((state?.components || []).map(p => [p.part, p]));
  function match(part, pattern) {
    const row = parts.get(part);
    if (!state?.connected || row?.status !== "有读数") return null;
    return row.value.match(pattern);
  }
  const cpu = match("mcu", /\bCPU (\d+) MHz \| uptime (\d+) ms$/);
  const sram = match("sram", /^internal free (\d+) bytes$/);
  const psram = match("psram", /^total (\d+), free (\d+) bytes$/);
  const wifi = match("wifi", /^associated \| RSSI (-?\d+) dBm$/);
  return {
    cpu: cpu ? Number(cpu[1]) : null, uptime: cpu ? Number(cpu[2]) : null,
    sram: sram ? Number(sram[1])/1024 : null,
    psram: psram ? Number(psram[2])/1024 : null, totalPsram: psram ? Number(psram[1])/1024 : null,
    rssi: wifi ? Number(wifi[1]) : null,
  };
}

// Sample host counters once per distinct poll; changing tabs cannot add a sample.
function createRuntimeMonitor() {
  let session = "", lastState = null, baseline = null, history = [];
  function update(state, tick = performance.now(), wall = Date.now()) {
    const key = state?.connected ? state.run + "/" + state.connection_id : "";
    if (key !== session) { session=key; baseline=null; history=[]; }
    if (!key || state === lastState) return;
    lastState=state;
    const span = baseline ? tick - baseline.tick : 0;
    const values=runtimeValues(state);
    if (baseline && span > 0 && span < 5000 && state.rx >= baseline.rx && state.tx >= baseline.tx) {
      values.rx = (state.rx-baseline.rx)*1000/span;
      values.tx = (state.tx-baseline.tx)*1000/span;
    }
    baseline={ tick, rx:state.rx, tx:state.tx };
    history.push({ time:wall, values });
    if (history.length > 200) history.shift();
  }
  return { update, samples: () => history };
}
if (typeof module !== "undefined") module.exports = {runtimeValues,createRuntimeMonitor};
