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
  const box = modal("记住的板子");
  const records = state.remembered_usb;
  box.append(el("p", "连接过一次、而且有唯一序列号的板子会出现在这里。勾选“插上就自动连接”后，插上它就会自动连上。"));
  if (!records.length)
    box.append(el("p", "还没有记住的板子。"));
  for (const record of records) {
    const section = el("div");
    section.className = "hist";
    const [vid, pid, serial] = JSON.parse(record.identity);
    section.append(
      el("strong", record.name || "还没起名"),
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
    const name = field(section, "起个好记的名字", el("input"));
    name.maxLength = 64;
    name.value = record.name;
    section.append(
      button("保存名字", async () => {
        await act("usb-name", { identity: record.identity, name: name.value });
        $("knownUsb").click();
      }),
    );
    box.append(section);
  }
});
bind("cadence", () => {
  const box = modal("多久没报算超时");
  const run = state.run;
  box.append(el("p", "有的部件每隔几秒报一次，有的只在有事时才报。告诉软件它是哪种，就不会把“没事不报”误当成“很久没报”。按板子型号保存 · " + state.profile));
  if (!state.components.length) {
    box.append(el("p", "等板子先报告部件。"));
    return;
  }
  const part = field(
    box,
    "部件",
    pick(state.components.map((r) => [r.part, r.name])),
  );
  const mode = field(
    box,
    "这个部件怎么报告",
    pick([
      ["auto", "默认（软件自己判断）"],
      ["periodic", "定时报告"],
      ["event", "有事才报"],
      ["once", "只报一次"],
    ]),
  );
  const interval = field(box, "大概多少秒报一次", el("input"));
  interval.type = "number";
  interval.min = 1;
  interval.max = 86400;
  const note = el("p");
  box.append(note);
  function describe() {
    interval.parentElement.hidden = mode.value !== "periodic";
    note.textContent =
      mode.value === "periodic"
        ? "超过 3 倍时间还没报，就标成“很久没报”。"
        : mode.value === "auto"
          ? "用软件内置的时间。"
          : "只记下最后一次报告的时间，不会因为没报而标成超时。";
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
      if (run !== state.run) throw Error("记录已经换了，请重新打开这个设置。");
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
  if (!Array.isArray(data?.tests)) throw Error("这个文件不是检查清单（里面要有 tests）。");
  const box = modal("导入检查清单 · " + data.name);
  const commands = data.tests.filter((t) => t.kind === "serial");
  if (commands.length) {
    const which = field(
      box,
      "先用例子试一下哪一项",
      pick(commands.map((t, i) => [i, t.name])),
    );
    const mode = field(
      box,
      "怎么判断回答对不对",
      pick([
        ["default", "按清单里写的"],
        ["line", "整行完全一样"],
        ["frame", "整段字节完全一样"],
        ["contains", "包含这段文字就算对"],
      ]),
    );
    const good = field(box, "正确回答的例子", el("textarea")),
      bad = field(box, "错误回答的例子", el("textarea"));
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
      button("用例子试一下", async () => {
        storeExamples();
        const { success_sample, failure_sample, ...test } = current();
        const run = (sample) =>
          postDiagnostic("/api/profile/sample", { test, sample });
        const first = await run(good.value);
        const second = bad.value ? (await run(bad.value)).outcome : "没填";
        result.textContent =
          "正确的例子：" + first.outcome + "　错误的例子：" + second;
      }),
      result,
    );
  }
  box.append(
    button("导入", async () => {
      await act("import", { data });
      await setup();
      $("profile").value = data.name.trim();
      $("profile").onchange();
      $("dialog").close();
      notice("检查清单已导入。");
    }),
  );
}
function archiveTools(box) {
  const bar = el("div");
  bar.className = "row";
  bar.append(button("稳定性测试记录", showRecords));
  const file = el("input");
  file.type = "file";
  file.accept = ".json";
  file.hidden = true;
  async function load(f) {
    if (!f || f.size > 524288) throw Error("故障记录文件要小于 512 KB。");
    const { run } = await postDiagnostic(
      "/api/fault-pack/import",
      JSON.parse(await f.text()),
    );
    $("dialog").close();
    await openReplay(run);
  }
  file.onchange = handle(() => load(file.files[0]));
  const drop = button("打开别人发来的故障记录（可以拖进来）", () => file.click());
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
        " 条记录。密码、IP 地址、邮箱等已经自动遮住；不想分享的内容可以在下面直接删掉，下载的文件和这里看到的一样。",
    ),
  );
  const area = field(box, "要分享的内容", el("textarea"));
  area.rows = 13;
  area.value = JSON.stringify(pack, null, 2);
  box.append(
    button("下载故障记录", () => {
      const data = JSON.parse(area.value);
      if (data.format !== pack.format || !Array.isArray(data.events))
        throw Error("请别删掉文件的整体结构（format 和 events）。");
      const blob = new Blob([area.value], { type: "application/json" });
      if (blob.size > 524288) throw Error("文件超过 512 KB 了，请再删一些。");
      const a = el("a");
      a.download = "boardbench-fault-" + run + ".json";
      a.href = URL.createObjectURL(blob);
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    }),
  );
}

bind("endurance", () => {
  const box = modal("长时间稳定性测试");
  const active = state.endurance;
  if (active) {
    box.append(
      el(
        "p",
        active.variant +
          " · 从 " +
          fmtTime(active.started) +
          " 开始 · 计划 " +
          active.minutes +
          " 分钟",
      ),
      button("结束并查看结果", async () => {
        await act("endurance-stop");
        await showRecords();
      }),
    );
  } else {
    box.append(el("p", "让板子一直开着，软件记下这段时间里它重启了几次、哪个部件多久没报告。只听不发，不会改动板子。"));
    const variant = field(box, "板子型号和配件（自己写，方便以后对比）", el("input"));
    variant.placeholder = "例如 BOX 一代 / 无摄像头";
    variant.maxLength = 80;
    variant.value = state.device_identity?.board?.value || "";
    const minutes = field(box, "测多久（分钟）", el("input"));
    minutes.type = "number";
    minutes.min = 1;
    minutes.max = 10080;
    minutes.value = 30;
    const start = button("开始测试", async () => {
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
          ? "到时间自动保存；中途断开会记下“中断”。"
          : "请先连上板子。",
      ),
      start,
    );
  }
  box.append(button("看以前的测试记录", showRecords));
});
async function showRecords() {
  const rows = await get("/api/endurance");
  const box = modal("稳定性测试记录");
  if (!rows.length) {
    box.append(el("p", "还没有记录。在“板子状态”页点“长时间稳定性测试”开始。"));
    return;
  }
  for (const r of rows) {
    const item = el("div");
    item.className = "hist";
    item.append(
      el(
        "strong",
        r.variant + " · " + r.status + (r.reference ? " · 标准样板" : ""),
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
      button(ref ? "和标准样板比一比" : "看结果", () =>
        showMeasurement(r, ref),
      ),
    );
    if (!r.reference && ["已到时", "手动结束"].includes(r.status))
      item.append(
        button("设成标准样板（以后拿来对比）", async () => {
          await act("endurance-reference", { record: r.id });
          await showRecords();
        }),
      );
    box.append(item);
  }
}
function showMeasurement(r, ref) {
  const box = modal(r.variant + " · " + (ref ? "和标准样板对比" : "测试结果"));
  const table = el("table");
  table.className = "diagnostic-measurements";
  const header = table.createTHead().insertRow();
  for (const title of [
    "项目",
    "这次 #" + r.id,
    ...(ref ? ["标准样板 #" + ref.id] : []),
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
      label.append(el("small", "不一样"));
    }
    for (const record of ref ? [r, ref] : [r]) {
      const cell = tr.insertCell();
      cell.append(document.createTextNode(String(fn(record) ?? "没看到")));
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
      : "没报告";
  }
  metric("记录时长（秒）", (v) => v.seconds);
  metric("收到的记录（条）", (v) => v.rx_logs);
  metric("板子重启了几次", (v) => v.boots);
  metric("开机到第一次报告用了（毫秒）", (v) => v.startup_ms);
  for (const [key, label] of [
    ["board", "板子自报型号"],
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
      metric(part + " 最久多少秒没报告", (v) => v.gaps[part]);
  }
  box.append(table, button("返回", showRecords));
}
