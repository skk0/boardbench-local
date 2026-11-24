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
  const sample = { g, speed, values, logId: row.log_id, at };
  if (Math.abs(g - 1) > 0.12 || speed > 8)
    return { ...sample, reason: "运动或读数偏离静置范围" };
  return { ...sample, gravity: [ax / g, ay / g, az / g] };
}

const motionSampleCount = 5;

function startMotionSampling(reading, run) {
  return {
    run,
    status: "running",
    samples: [],
    lastId: reading.logId,
    lastAt: reading.at,
    reference: reading.gravity,
  };
}

function advanceMotionSampling(session, reading) {
  if (session.status !== "running") return;
  if (!reading.gravity) {
    session.status = reading.reason;
    return;
  }
  if (reading.logId <= session.lastId) return;
  if (reading.at - session.lastAt > 8000 || reading.at < session.lastAt) {
    session.status = "采样时间不连续";
    return;
  }
  // Compare gravity directions to catch a slow change of pose between reports.
  const dot = session.reference.reduce(
    (sum, axis, i) => sum + axis * reading.gravity[i],
    0,
  );
  if (dot < Math.cos((3 * Math.PI) / 180)) {
    session.status = "采样期间姿态变化超过 3°";
    return;
  }
  session.lastAt = reading.at;
  session.lastId = reading.logId;
  session.samples.push(reading);
  if (session.samples.length === motionSampleCount) session.status = "complete";
}

function motionStatistics(samples) {
  if (samples.length !== motionSampleCount) return null;
  const mean = (values) => values.reduce((a, b) => a + b, 0) / values.length;
  const range = (values) => Math.max(...values) - Math.min(...values);
  const magnitude = samples.map((s) => s.g);
  const axes = motionLabels.map((_, i) => samples.map((s) => s.values[i]));
  return {
    normMean: mean(magnitude),
    normRange: range(magnitude),
    axisRange: axes.slice(0, 3).map(range),
    gyroMean: axes.slice(3).map(mean),
  };
}

function motionSampleFields(session) {
  const stats = motionStatistics(session.samples);
  const triple = (v) => v.map((n) => n.toFixed(3)).join(" / ");
  const stamp = (s) =>
    new Date(s.at).toLocaleString("zh-CN", {
      hour12: false,
      fractionalSecondDigits: 3,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  return [
    ["加速度模长均值", stats.normMean.toFixed(3) + " g"],
    ["模长峰峰值", stats.normRange.toFixed(3) + " g"],
    ["加速度峰峰值（X / Y / Z）", triple(stats.axisRange) + " g"],
    ["陀螺仪均值（X / Y / Z）", triple(stats.gyroMean) + " °/s"],
    [
      "采样时段（本地）",
      stamp(session.samples[0]) + " – " + stamp(session.samples[4]),
    ],
  ];
}

function motionSampleNote(session) {
  const lines = [
    "六轴静置采样 · 5 组",
    ...motionSampleFields(session).map(([k, v]) => k + "：" + v),
  ];
  lines.push("UTC 采样时间 · 日志编号 · AX / AY / AZ (g) · GX / GY / GZ (°/s)");
  for (const sample of session.samples) {
    lines.push(
      new Date(sample.at).toISOString() +
        " · L" +
        sample.logId +
        " · " +
        sample.values.join(" / "),
    );
  }
  return lines.join("\n");
}

function renderMotionSampling(host, session, reading, run) {
  const action = host.querySelector(".motion-collect");
  const running = session?.status === "running";
  action.textContent = running ? "取消采样" : "采集 5 组";
  action.disabled = !running && !reading.gravity;
  const note = host.querySelector(".motion-sampling-note");
  let message =
    "放稳后开始。采样窗口为 0.88–1.12 g、角速度 ≤8 °/s，姿态变化 ≤3°。";
  if (session) {
    const n = session.samples.length;
    if (running) message = "保持板子静止 · 已采集 " + n + " / 5 组";
    else if (session.status === "complete") message = "已采集 5 / 5 组";
    else
      message = "采样中断 · " + session.status + " · 已记录 " + n + " / 5 组";
  }
  if (note.textContent !== message) note.textContent = message;
  const result = host.querySelector(".motion-sampling-result");
  const save = host.querySelector(".motion-save");
  save.hidden = session?.status !== "complete";
  save.disabled =
    session?.run !== run || Boolean(session?.saving || session?.saved);
  save.textContent = session?.saved
    ? "已存入会话日志"
    : session?.saving
      ? "正在保存…"
      : "存入会话日志";
  if (session?.status !== "complete") {
    result.hidden = true;
    return;
  }
  if (!result.hidden && result.dataset.final === String(session.lastId)) return;
  result.hidden = false;
  result.dataset.final = String(session.lastId);
  result.replaceChildren();
  for (const [name, value] of motionSampleFields(session)) {
    result.append(el("dt", name), el("dd", value));
  }
  result.append(el("dt", "原始日志"));
  const refs = el("dd");
  refs.className = "motion-sampling-refs";
  for (const sample of session.samples) {
    const ref = "L" + sample.logId;
    refs.append(button(ref, () => showEvidence({ run }, ref)));
  }
  result.append(refs);
}

const motionView = (() => {
  let host, current, connectionKey, zero, sampling;

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
      if (sampling?.status === "running") sampling.status = "设备连接发生变化";
      else if (sampling?.status === "complete") sampling = null;
    }
    connectionKey = nextKey;

    if (!host) {
      host = document.getElementById("motionView");
      if (!host) return;
      host.innerHTML = `
        <div class="motion-top"><h2>板子水平仪</h2><span class="motion-status" role="status"></span></div>
        <div class="motion-instrument">
          <div class="motion-dial" aria-hidden="true">
            <span class="motion-ring"></span><span class="motion-bubble" hidden></span>
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
        <div class="motion-sampling"><div class="motion-actions"><strong>静置采样</strong><button type="button" class="motion-collect">采集 5 组</button></div>
          <p class="motion-sampling-note" role="status"></p><dl class="motion-sampling-result" hidden></dl><button type="button" class="motion-save" hidden>存入会话日志</button></div>`;
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
      host.querySelector(".motion-save").onclick = handle(async () => {
        const completed = sampling;
        if (
          completed?.status !== "complete" ||
          completed.saving ||
          completed.saved ||
          completed.run !== current?.run
        )
          return;
        completed.saving = true;
        show(current);
        try {
          await get("/api/note", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-BoardBench": token,
            },
            body: JSON.stringify({
              run: completed.run,
              text: motionSampleNote(completed),
            }),
          });
          completed.saved = true;
        } finally {
          completed.saving = false;
          show(current);
        }
      });
      host.querySelector(".motion-collect").onclick = () => {
        if (sampling?.status === "running") {
          sampling.status = "已取消";
        } else {
          const next = motionReading(current);
          if (next.gravity) sampling = startMotionSampling(next, current.run);
        }
        show(current);
      };
    }

    const reading = motionReading(state);
    if (sampling) advanceMotionSampling(sampling, reading);
    if (host.closest(".tab")?.hidden) return;
    const angles = reading.gravity && motionAngles(reading.gravity, zero);
    const level = angles && angles.tilt <= 3;
    const status = reading.reason || (level ? "水平范围 ±3°" : "已测得倾角");
    const statusNode = host.querySelector(".motion-status");
    if (statusNode.textContent !== status) statusNode.textContent = status;
    host.dataset.signal = level ? "level" : angles ? "tilt" : "idle";
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
    renderMotionSampling(host, sampling, reading, state?.run);
    capture.disabled = !reading.gravity;
    reset.disabled = !zero;
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
