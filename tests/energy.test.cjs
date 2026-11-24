const { test } = require("node:test");
const assert = require("node:assert/strict");
const { parseEnergy } = require("../web/energy.js");
const frame = (value, status = "sample") => parseEnergy({
  id: 12, at: "2026-01-01T00:00:00Z", message: JSON.stringify({ bb: 1, part: "power", status, value }),
});

test("power snapshots preserve zero and signed current, omit unknowns and keep cell positions", () => {
  const p = frame({ battery_a: -0.2, load_w: 0, pv_v: "12", soc_pct: 101, soh_pct: 0, cells_v: [3.31, null, 3.30] });
  assert.deepEqual(p.values, { battery_a: -0.2, load_w: 0, soh_pct: 0 });
  assert.deepEqual(p.cells, [3.31, null, 3.30]);
  assert.deepEqual(frame({}, "error").values, {});
  assert.deepEqual(frame({ cells_v: Array(65).fill(3.4) }).cells, []);
  assert.deepEqual(frame({ battery_w: 2 }, "unavailable").values, {});
});

const { createRuntimeMonitor } = require("../web/runtime.js");
test("runtime counters respect connection boundaries, repeated refreshes, missing RSSI and stale samples", () => {
  const monitor = createRuntimeMonitor();
  const state = {connected:true,run:1,connection_id:1,rx:0,tx:0,components:[
    {part:"wifi",status:"有读数",value:"associated | RSSI -62 dBm"},
    {part:"sram",status:"有读数",value:"internal free 2048 bytes"},
    {part:"psram",status:"上报超时",value:"total 8192, free 512 bytes"},
  ]};
  monitor.update(state,0,0);monitor.update(state,300,300);
  monitor.update({...state,rx:100},500,500);
  assert.equal(monitor.samples().length,2);
  const last=monitor.samples().at(-1).values;
  assert.deepEqual([last.rx,last.tx,last.rssi,last.sram,last.psram],[200,0,-62,2,null]);
  monitor.update({...state,connection_id:2},1000,1000);
  assert.equal(monitor.samples().length,1);
  assert.equal(monitor.samples()[0].values.rx,undefined);
  monitor.update(null,1200,1200);assert.deepEqual(monitor.samples(),[]);
});
