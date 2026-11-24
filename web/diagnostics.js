"use strict";

function postDiagnostic(path, payload) {
  return get(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-BoardBench": token },
    body: JSON.stringify(payload),
  });
}
function field(parent, caption, input) {
  const wrapper = el("label", caption);
  input.setAttribute("aria-label", caption);
  wrapper.append(input);
  parent.append(wrapper);
  return input;
}
function pick(options) {
  const select = el("select");
  select.replaceChildren(
    ...options.map(([value, name]) => new Option(name, value)),
  );
  return select;
}
let scannedPorts = [];
function portSettings(rows) {
  scannedPorts = rows;
  if (state?.connected) return;
  const saved = rows.find((r) => r.path === $("port").value)?.saved;
  if (saved) {
    $("baud").value = saved.baud;
    $("framing").value = saved.framing;
  }
}
$("port").onchange = () => {
  portSettings(scannedPorts);
  buttons();
};
bind("knownUsb", () => {
  const box = modal("已记住的 USB 设备");
  const records = state.remembered_usb;
  if (!records.length)
    box.append(el("p", "首次连接有唯一序列号的串口后，可在这里命名。"));
  for (const record of records) {
    const section = el("div");
    section.className = "hist";
    const [vid, pid, serial] = JSON.parse(record.identity);
    section.append(
      el("strong", record.name || "未命名设备"),
      el(
        "p",
        [vid, pid].map((n) => n.toString(16).padStart(4, "0")).join(":") +
          " · " +
          serial +
          " · " +
          record.baud +
          " " +
          record.framing,
      ),
    );
    const name = field(section, "别名", el("input"));
    name.maxLength = 64;
    name.value = record.name;
    section.append(
      button("保存别名", async () => {
        await act("usb-name", { identity: record.identity, name: name.value });
        $("knownUsb").click();
      }),
    );
    box.append(section);
  }
});
bind("cadence", () => {
  const box = modal("元件上报设置");
  const run = state.run;
  box.append(el("p", "保存到检测配置 · " + state.profile));
  if (!state.components.length) {
    box.append(el("p", "等待设备首次上报元件。"));
    return;
  }
  const part = field(
    box,
    "元件",
    pick(state.components.map((r) => [r.part, r.name])),
  );
  const mode = field(
    box,
    "上报方式",
    pick([
      ["auto", "默认"],
      ["periodic", "周期上报"],
      ["event", "事件触发"],
      ["once", "一次性声明"],
    ]),
  );
  const interval = field(box, "预计周期（秒）", el("input"));
  interval.type = "number";
  interval.min = 1;
  interval.max = 86400;
  const note = el("p");
  box.append(note);
  function describe() {
    interval.parentElement.hidden = mode.value !== "periodic";
    note.textContent =
      mode.value === "periodic"
        ? "连续三个周期未收到更新时提示超时。"
        : mode.value === "auto"
          ? "采用内置元件的时限。"
          : "保留最近的上报时间，不按静默时长判为超时。";
  }
  part.onchange = () => {
    const r = state.components.find((r) => r.part === part.value);
    [mode.value, interval.value] = r?.cadence || ["auto", 0];
    if (Number(interval.value) < 1) interval.value = 30;
    describe();
  };
  mode.onchange = describe;
  part.onchange();
  box.append(
    button("保存设置", async () => {
      if (run !== state.run) throw Error("会话已切换，请重新打开设置。");
      await act("cadence", {
        part: part.value,
        mode: mode.value,
        seconds: Number(interval.value),
      });
      $("dialog").close();
    }),
  );
});
function previewProfile(data) {
  if (!Array.isArray(data?.tests)) throw Error("请选择包含 tests 的测试配置。");
  const box = modal("导入配置 · " + data.name);
  const commands = data.tests.filter((t) => t.kind === "serial");
  if (commands.length) {
    const which = field(
      box,
      "离线试跑项目",
      pick(commands.map((t, i) => [i, t.name])),
    );
    const mode = field(
      box,
      "应答规则",
      pick([
        ["default", "沿用配置默认"],
        ["line", "整行一致"],
        ["frame", "完整 HEX 帧"],
        ["contains", "包含标志"],
      ]),
    );
    const good = field(box, "成功样例", el("textarea")),
      bad = field(box, "失败样例", el("textarea"));
    const result = el("p");
    result.setAttribute("role", "status");
    const current = () => commands[Number(which.value)];
    which.onchange = () => {
      const t = current();
      mode.value = t.match || "default";
      good.value = t.success_sample ?? t.expect;
      bad.value = t.failure_sample ?? t.reject ?? "";
      result.textContent = "";
    };
    which.onchange();
    function storeExamples() {
      const t = current();
      if (mode.value === "default") delete t.match;
      else t.match = mode.value;
      for (const [key, node] of [
        ["success_sample", good],
        ["failure_sample", bad],
      ]) {
        if (node.value) t[key] = node.value;
        else delete t[key];
      }
    }
    for (const node of [mode, good, bad]) node.oninput = storeExamples;
    box.append(
      button("试跑样例", async () => {
        storeExamples();
        const { success_sample, failure_sample, ...test } = current();
        const run = (sample) =>
          postDiagnostic("/api/profile/sample", { test, sample });
        const first = await run(good.value);
        const second = bad.value ? (await run(bad.value)).outcome : "未填写";
        result.textContent =
          "成功样例：" + first.outcome + "　失败样例：" + second;
      }),
      result,
    );
  }
  box.append(
    button("导入此配置", async () => {
      await act("import", { data });
      await setup();
      $("profile").value = data.name.trim();
      $("profile").onchange();
      $("dialog").close();
      notice("配置已导入。");
    }),
  );
}
function archiveTools(box) {
  const bar = el("div");
  bar.className = "row";
  bar.append(button("长测记录与样板", showRecords));
  const file = el("input");
  file.type = "file";
  file.accept = ".json";
  file.hidden = true;
  async function load(f) {
    if (!f || f.size > 524288) throw Error("请选择小于 512 KiB 的故障包。");
    const { run } = await postDiagnostic(
      "/api/fault-pack/import",
      JSON.parse(await f.text()),
    );
    $("dialog").close();
    await openReplay(run);
  }
  file.onchange = handle(() => load(file.files[0]));
  const drop = button("打开故障包（可拖入）", () => file.click());
  drop.ondragover = (e) => e.preventDefault();
  drop.ondrop = (e) => {
    e.preventDefault();
    handle(() => load(e.dataTransfer.files[0]))();
  };
  bar.append(drop, file);
  box.append(bar);
}
async function previewPack(run) {
  const pack = await get("/api/fault-pack/" + run);
  const box = modal("预览故障记录 · #" + run);
  box.append(
    el(
      "p",
      pack.events.length +
        " 条日志。可在下方删去不便分享的内容，下载文件与预览一致。",
    ),
  );
  const area = field(box, "故障包内容", el("textarea"));
  area.rows = 13;
  area.value = JSON.stringify(pack, null, 2);
  box.append(
    button("下载故障包", () => {
      const data = JSON.parse(area.value);
      if (data.format !== pack.format || !Array.isArray(data.events))
        throw Error("请保留故障包结构。");
      const blob = new Blob([area.value], { type: "application/json" });
      if (blob.size > 524288) throw Error("文件大于 512 KiB。");
      const a = el("a");
      a.download = "boardbench-fault-" + run + ".json";
      a.href = URL.createObjectURL(blob);
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    }),
  );
}

bind("endurance", () => {
  const box = modal("稳定性长测");
  const active = state.endurance;
  if (active) {
    box.append(
      el(
        "p",
        active.variant +
          " · 开始于 " +
          fmtTime(active.started) +
          " · 计划 " +
          active.minutes +
          " 分钟",
      ),
      button("结束并查看结果", async () => {
        await act("endurance-stop");
        await showRecords();
      }),
    );
  } else {
    const variant = field(box, "板型与选装配置", el("input"));
    variant.placeholder = "例如 BOX 一代 / 无摄像头";
    variant.maxLength = 80;
    variant.value = state.device_identity?.board?.value || "";
    const minutes = field(box, "记录时长（分钟）", el("input"));
    minutes.type = "number";
    minutes.min = 1;
    minutes.max = 10080;
    minutes.value = 30;
    const start = button("开始只读长测", async () => {
      await act("endurance-start", {
        variant: variant.value,
        minutes: Number(minutes.value),
      });
      $("dialog").close();
    });
    start.disabled = !state.connected;
    box.append(
      el(
        "p",
        state.connected
          ? "到时自动保存，断开连接时记录中断。"
          : "请先连接待测设备。",
      ),
      start,
    );
  }
  box.append(button("查看长测记录", showRecords));
});
async function showRecords() {
  const rows = await get("/api/endurance");
  const box = modal("长测记录与样板");
  if (!rows.length) {
    box.append(el("p", "尚无记录，可从元件监视开始长测。"));
    return;
  }
  for (const r of rows) {
    const item = el("div");
    item.className = "hist";
    item.append(
      el(
        "strong",
        r.variant + " · " + r.status + (r.reference ? " · 样板" : ""),
      ),
      el(
        "p",
        new Date(r.started).toLocaleString() +
          " · " +
          r.profile +
          " · " +
          r.seconds +
          " 秒",
      ),
    );
    const ref = rows.find(
      (v) =>
        v.reference &&
        v.id !== r.id &&
        v.variant === r.variant &&
        v.profile === r.profile &&
        ["board", "chip"].every(
          (k) =>
            !r.identity[k] ||
            !v.identity[k] ||
            r.identity[k].value === v.identity[k].value,
        ),
    );
    item.append(
      button(ref ? "与同款样板对比" : "查看测量", () =>
        showMeasurement(r, ref),
      ),
    );
    if (!r.reference && ["已到时", "手动结束"].includes(r.status))
      item.append(
        button("设为此配置的样板", async () => {
          await act("endurance-reference", { record: r.id });
          await showRecords();
        }),
      );
    box.append(item);
  }
}
function showMeasurement(r, ref) {
  const box = modal(r.variant + " · " + (ref ? "样板对比" : "长测结果"));
  const table = el("table");
  table.className = "diagnostic-measurements";
  const header = table.createTHead().insertRow();
  for (const title of [
    "项目",
    "本次 #" + r.id,
    ...(ref ? ["样板 #" + ref.id] : []),
  ]) {
    const th = el("th", title);
    th.scope = "col";
    header.append(th);
  }
  const rows = table.createTBody();
  function metric(name, fn, evidence) {
    const tr = rows.insertRow();
    const label = el("th", name);
    label.scope = "row";
    tr.append(label);
    if (ref && fn(r) !== fn(ref)) {
      label.append(el("small", "差异"));
    }
    for (const record of ref ? [r, ref] : [r]) {
      const cell = tr.insertCell();
      cell.append(document.createTextNode(String(fn(record) ?? "未观测")));
      const log = evidence?.(record);
      if (log) {
        const link = button("L" + log, () =>
          showEvidence({ run: record.run }, "L" + log),
        );
        link.className = "text-link";
        cell.append(link);
      }
    }
  }
  function partText(record, id) {
    const part = record.components[id];
    return part
      ? [part.model, part.status].filter(Boolean).join(" · ")
      : "未上报";
  }
  metric("记录时长（秒）", (v) => v.seconds);
  metric("接收日志（条）", (v) => v.rx_logs);
  metric("观测到的 ROM 启动（次）", (v) => v.boots);
  metric("最近 ROM 到主控首报（毫秒）", (v) => v.startup_ms);
  for (const [key, label] of [
    ["board", "固件板型"],
    ["chip", "芯片"],
    ["firmware", "固件"],
    ["version", "应用版本"],
  ])
    metric(
      label,
      (v) => v.identity[key]?.value,
      (v) => v.identity[key]?.log_id,
    );
  const parts = new Set([
    ...Object.keys(r.components),
    ...Object.keys(ref?.components || {}),
  ]);
  for (const part of parts) {
    metric(
      part,
      (v) => partText(v, part),
      (v) => v.components[part]?.log_id,
    );
    if (part in r.gaps || (ref && part in ref.gaps))
      metric(part + " 最长静默（秒）", (v) => v.gaps[part]);
  }
  box.append(table, button("返回长测记录", showRecords));
}
