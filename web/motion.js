"use strict";

// BoardBenchProbe/telemetry.h emits acceleration in g and angular speed in °/s.
const motionDecimal = "([+-]?\\d+(?:\\.\\d+)?)";
const motionTriple = Array(3).fill(motionDecimal).join("\\s+");
const motionFormat = new RegExp(
  "^a\\[g\\]\\s+" +
    motionTriple +
    "\\s*\\|\\s*gyro\\[dps\\]\\s+" +
    motionTriple +
    "$",
);
const motionLabels = [
  "AX (g)",
  "AY (g)",
  "AZ (g)",
  "GX (°/s)",
  "GY (°/s)",
  "GZ (°/s)",
];

function motionChannels(value) {
  if (typeof value !== "string" || value.length > 240) return [];
  const match = motionFormat.exec(value.trim());
  if (!match) return [];
  const numbers = match.slice(1).map(Number);
  return numbers.every(Number.isFinite)
    ? numbers.map((v, i) => [motionLabels[i], v])
    : [];
}

// Express gravity in the plane chosen as zero. The X axis is the projection of
// sensor +X onto that plane. If it is vertical, +Y chooses the reference axis.
function motionAngles(gravity, zero) {
  const [x, y, z] = gravity;
  let px = x,
    py = y,
    pz = z;
  if (zero) {
    const [nx, ny, nz] = zero;
    const side = Math.hypot(ny, nz);
    if (side > 1e-8) {
      px = side * x - (nx * (ny * y + nz * z)) / side;
      py = (nz * y - ny * z) / side;
    } else {
      px = -Math.sign(nx) * z;
      py = y;
    }
    pz = nx * x + ny * y + nz * z;
  }
  const deg = 180 / Math.PI;
  const radial = Math.hypot(px, py);
  const tilt = Math.atan2(radial, pz) * deg;
  const radius = Math.min(tilt / 15, 1);
  return {
    roll: Math.hypot(py, pz) < 1e-7 ? null : Math.atan2(py, pz) * deg,
    pitch: Math.atan2(-px, Math.hypot(py, pz)) * deg,
    tilt,
    // An exactly inverted board has no unique downhill direction.
    showBubble: radial > 1e-7 || pz >= 0,
    bubbleX: radial > 1e-7 ? (radius * px) / radial : 0,
    bubbleY: radial > 1e-7 ? (-radius * py) / radial : 0,
  };
}

function motionReading(state, now = Date.now()) {
  if (!state) return { reason: "连接本地服务后查看姿态" };
  if (!state.connected) return { reason: "连接设备后查看姿态" };
  const row = state.components.find((item) => item.part === "imu");
  if (!row || !row.log_id) return { reason: "等待固件上报六轴读数" };
  if (row.status !== "有读数") return { reason: "六轴传感器 · " + row.status };
  const at = Date.parse(row.at);
  const age = now - at;
  if (!Number.isFinite(age) || age < -1000 || age > 8000)
    return { reason: "六轴读数已过期" };
  const values = motionChannels(row.value).map(([, v]) => v);
  if (!values.length) return { reason: "需要带 g 和 °/s 单位的六轴数据" };
  const [ax, ay, az, gx, gy, gz] = values;
  const g = Math.hypot(ax, ay, az);
  const speed = Math.hypot(gx, gy, gz);
  const sample = { g, speed, logId: row.log_id, at };
  if (Math.abs(g - 1) > 0.12 || speed > 8)
    return { ...sample, reason: "运动中 · 请把板子放稳" };
  return { ...sample, gravity: [ax / g, ay / g, az / g] };
}

const motionGoals = [
  { roll: 9, pitch: 0, direction: "上方" },
  { roll: 0, pitch: -9, direction: "右侧" },
  { roll: -6, pitch: 6, direction: "左下方" },
];

function startMotionChallenge(reading) {
  return { stage: 0, streak: 0, lastId: reading.logId, lastAt: reading.at };
}

function advanceMotionChallenge(game, reading, angles) {
  const goal = motionGoals[game.stage];
  if (!goal) return;
  if (!angles) {
    game.streak = 0;
    return;
  }
  if (reading.logId <= game.lastId) return;
  if (reading.at - game.lastAt > 8000 || reading.at < game.lastAt)
    game.streak = 0;
  game.lastId = reading.logId;
  game.lastAt = reading.at;
  if (
    angles.roll === null ||
    Math.abs(angles.roll - goal.roll) > 2 ||
    Math.abs(angles.pitch - goal.pitch) > 2
  ) {
    game.streak = 0;
    return;
  }
  if (++game.streak >= 2) {
    game.stage++;
    game.streak = 0;
  }
}

function renderMotionChallenge(host, game, reading) {
  const goal = game && motionGoals[game.stage];
  const finished = game && !goal;
  const play = host.querySelector(".motion-play");
  play.textContent = finished ? "再玩一次" : goal ? "结束挑战" : "开始挑战";
  play.disabled = !goal && !reading.gravity;
  const marker = host.querySelector(".motion-goal");
  marker.hidden = !goal;
  const note = host.querySelector(".motion-game-note");
  note.hidden = !game;
  let text = "";
  if (goal) {
    const pitch = (goal.pitch * Math.PI) / 180;
    const roll = (goal.roll * Math.PI) / 180;
    const coords = motionAngles([
      -Math.sin(pitch),
      Math.cos(pitch) * Math.sin(roll),
      Math.cos(pitch) * Math.cos(roll),
    ]);
    marker.style.left = 50 + coords.bubbleX * 42 + "%";
    marker.style.top = 50 + coords.bubbleY * 42 + "%";
    text =
      "第 " +
      (game.stage + 1) +
      " / 3 关 · 气泡移向" +
      goal.direction +
      "金色圈。横滚 " +
      goal.roll +
      "°，俯仰 " +
      goal.pitch +
      "°，两轴各允许 ±2°。已保持 " +
      game.streak +
      " / 2 次采样。";
  } else if (finished) {
    text = "三关完成 · 三个目标均已收到连续两次达标采样。";
  }
  if (note.textContent !== text) note.textContent = text;
}

const motionView = (() => {
  let host, current, connectionKey, zero, game;

  function show(state) {
    current = state;
    const nextKey = state?.connected
      ? JSON.stringify([
          state.run,
          state.connection_id,
          state.transport,
          state.port,
        ])
      : null;
    if (nextKey !== connectionKey) {
      zero = null;
      game = null;
    }
    connectionKey = nextKey;

    if (!host) {
      host = document.getElementById("motionView");
      if (!host) return;
      host.innerHTML = `
        <div class="motion-top"><h2>板子水平仪</h2><span class="motion-status" role="status"></span></div>
        <div class="motion-instrument">
          <div class="motion-dial" aria-hidden="true">
            <span class="motion-target"></span><span class="motion-goal" hidden></span><span class="motion-bubble" hidden></span>
            <span class="motion-x">+X</span><span class="motion-y">+Y</span>
          </div>
          <dl class="motion-values">
            <div><dt>横滚</dt><dd class="motion-roll">—</dd></div>
            <div><dt>俯仰</dt><dd class="motion-pitch">—</dd></div>
            <div class="motion-source"></div>
          </dl>
        </div>
        <div class="motion-actions"><button type="button">以当前姿态置零</button><button type="button">恢复默认零点</button></div>
        <p class="motion-help"></p>
        <div class="motion-game"><div class="motion-game-actions"><strong>倾斜挑战</strong><button type="button" class="motion-play">开始挑战</button></div>
          <p class="motion-game-note" role="status" hidden></p></div>`;
      const [capture, reset] = host.querySelectorAll("button");
      capture.onclick = () => {
        const next = motionReading(current);
        if (next.gravity) zero = next.gravity;
        show(current);
      };
      reset.onclick = () => {
        zero = null;
        show(current);
      };
      host.querySelector(".motion-play").onclick = () => {
        if (game && motionGoals[game.stage]) {
          game = null;
        } else {
          const next = motionReading(current);
          if (next.gravity) game = startMotionChallenge(next);
        }
        show(current);
      };
    }

    if (host.closest(".tab")?.hidden) {
      if (game) game.streak = 0;
      return;
    }
    const reading = motionReading(state);
    const angles = reading.gravity && motionAngles(reading.gravity, zero);
    const onTarget = angles && angles.tilt <= 3;
    const status = reading.reason || (onTarget ? "已进入 ±3°" : "调整倾角");
    const statusNode = host.querySelector(".motion-status");
    if (statusNode.textContent !== status) statusNode.textContent = status;
    host.dataset.signal = onTarget ? "level" : angles ? "tilt" : "idle";
    const formatAngle = (value) =>
      value == null ? "—" : Number(value.toFixed(1)) + "°";
    host.querySelector(".motion-roll").textContent = formatAngle(angles?.roll);
    host.querySelector(".motion-pitch").textContent = formatAngle(
      angles?.pitch,
    );
    const bubble = host.querySelector(".motion-bubble");
    bubble.hidden = !angles?.showBubble;
    if (!bubble.hidden) {
      bubble.style.left = 50 + angles.bubbleX * 42 + "%";
      bubble.style.top = 50 + angles.bubbleY * 42 + "%";
    }
    const [capture, reset] = host.querySelectorAll("button");
    if (game) advanceMotionChallenge(game, reading, angles);
    renderMotionChallenge(host, game, reading);
    const playing = Boolean(game && motionGoals[game.stage]);
    capture.disabled = !reading.gravity || playing;
    reset.disabled = !zero || playing;
    host.querySelector(".motion-source").textContent = reading.logId
      ? reading.g.toFixed(2) +
        " g · " +
        reading.speed.toFixed(1) +
        " °/s · L" +
        reading.logId
      : "";
    const reference = zero ? "本次连接的自定零点" : "传感器 Z 轴朝上";
    host.querySelector(".motion-help").textContent =
      "参考面：" + reference + "。静置时读数，内圈 3°，外圈 15°。";
  }
  return show;
})();

function renderMotion(state) {
  motionView(state);
}
