const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const panel = vm.createContext({});
vm.runInContext(
  readFileSync(path.join(__dirname, "../web/motion.js"), "utf8"),
  panel,
);
const sample = "a[g] 0.000 0.500 0.866 | gyro[dps] 0.00 0.00 -0.04";
const time = Date.UTC(2026, 10, 24);
const state = {
  connected: true,
  components: [
    {
      part: "imu",
      value: sample,
      status: "有读数",
      log_id: 17,
      at: new Date(time).toISOString(),
    },
  ],
};
function read(changes) {
  return panel.motionReading(
    { ...state, components: [{ ...state.components[0], ...changes }] },
    time,
  );
}
function near(actual, target, tolerance = 1e-6) {
  assert.ok(
    Math.abs(actual - target) < tolerance,
    "expected " + target + ", got " + actual,
  );
}

test("probe format yields six named unit-bearing channels", () => {
  const rows = panel.motionChannels(sample);
  assert.equal(rows.length, 6);
  assert.equal(rows[0][0], "AX (g)");
  assert.equal(rows[2][1], 0.866);
  assert.equal(rows[5][1], -0.04);
  for (const text of [
    "ax=0 ay=0 az=1",
    sample + " #debug",
    sample.replace("g]", "mg]"),
    sample.replace("0.500", "NaN"),
  ])
    assert.equal(panel.motionChannels(text).length, 0);
});

test("tilt and sensor signs use the gravity vector", () => {
  const angles = panel.motionAngles(read({}).gravity);
  near(angles.roll, 30, 0.01);
  near(angles.pitch, 0);
  near(angles.tilt, 30, 0.01);
  near(angles.bubbleY, -1);
  const vertical = panel.motionAngles([1, 0, 0]);
  near(vertical.pitch, -90);
  assert.equal(vertical.roll, null);
  const inverted = panel.motionAngles([0, 0, -1]);
  near(inverted.tilt, 180);
  assert.equal(inverted.showBubble, false);
});

test("user zero is a reference plane, including the +/-90 degree pitch poles", () => {
  const a = Math.sqrt(0.5);
  for (const v of [
    [1, 0, 0],
    [-1, 0, 0],
    [0, 0, -1],
    [0, a, a],
    [a, 0, a],
  ]) {
    const rel = panel.motionAngles(v, v);
    near(rel.tilt, 0);
    near(rel.roll, 0);
    near(rel.pitch, 0);
  }
  near(panel.motionAngles([0, 0, 1], [0, a, a]).roll, -45);
});

test("dynamic, stale, missing, disconnected and unparseable samples yield no angles", () => {
  assert.ok(read({}).gravity);
  for (const change of [
    { value: sample.replace("0.866", "1.45") },
    { value: sample.replace("-0.04", "9.25") },
    { value: sample.replace("0.866", "0.0").replace("0.500", "0.0") },
    { value: "1,2,3,4,5,6" },
    { at: new Date(time - 8001).toISOString() },
    { at: "invalid" },
    { status: "上报超时" },
    { log_id: null },
  ])
    assert.equal(read(change).gravity, undefined);
  for (const s of [
    null,
    { ...state, connected: false },
    { ...state, components: [] },
  ])
    assert.equal(panel.motionReading(s, time).gravity, undefined);
});

test("challenge only counts fresh consecutive stable samples and completes three targets", () => {
  const game = panel.startMotionChallenge({ logId: 10, at: time });
  const update = (id, roll, pitch, elapsed = id * 100) =>
    panel.advanceMotionChallenge(
      game,
      { logId: id, at: time + elapsed },
      roll == null ? null : { roll, pitch },
    );
  update(10, 9, 0);
  assert.equal(game.streak, 0);
  update(11, 9, 0);
  update(11, 9, 0);
  assert.equal(game.streak, 1);
  update(12, null, null);
  assert.equal(game.streak, 0);
  update(13, 8, 0);
  update(14, 5, 0);
  assert.equal(game.streak, 0);
  update(15, 8, 0);
  update(16, 9, 0, 12000);
  assert.equal(game.stage, 0);
  update(17, 9, 0, 13000);
  assert.equal(game.stage, 1);
  update(18, 0, -9, 14000);
  update(19, 0, -9, 15000);
  assert.equal(game.stage, 2);
  update(20, -6, 6, 16000);
  update(21, -6, 6, 17000);
  assert.equal(game.stage, 3);
  update(22, -6, 6, 18000);
  assert.equal(game.stage, 3);
});
