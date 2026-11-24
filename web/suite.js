"use strict";
let suiteKey = "",
  suitePlans = {};
function suiteAction(mode) {
  const profile = $("profile").value;
  return act("suite-start", { mode, profile });
}
bind("runBoard", () => suiteAction("full"));
bind("runQuick", () => suiteAction("quick"));
function renderSuite() {
  const c = state.suite;
  const running = c?.status === "运行中";
  $("suiteLaunch").hidden = running;
  $("profile").disabled = running;
  const plan = suitePlans[$("profile").value];
  if (plan) {
    const offline =
      plan.auto && !state.connected ? " 当前未连接设备，命令项将跳过。" : "";
    $("suiteScope").textContent =
      plan.total +
      " 项 · " +
      plan.auto +
      " 项自动 · " +
      plan.groups +
      " 组现场观察。" +
      offline;
  }
  $("runBoard").disabled = $("runQuick").disabled = Boolean(state.job);
  const done = c ? Object.keys(c.outcomes).length : 0;
  const key = [state.run, c?.id, c?.phase, c?.index, done].join("/");
  if (key === suiteKey) return;
  suiteKey = key;
  const area = $("suiteProgress");
  area.replaceChildren();
  if (!c) return;
  const status = el("div");
  status.className = "suite-status";
  status.append(
    el("strong", c.mode === "quick" ? "日常快测" : "完整验收"),
    el("span", c.phase + " · " + done + " / " + c.total + " 项"),
  );
  area.append(status);
  const meter = el("progress");
  meter.max = c.total;
  meter.value = done;
  meter.setAttribute("aria-label", "整板检测进度");
  area.append(meter);
  if (!running) {
    const totals = { 通过: 0, 失败: 0, 跳过: 0 };
    Object.values(c.outcomes).forEach((x) => totals[x.state]++);
    area.append(
      el(
        "p",
        Object.entries(totals)
          .map(([k, v]) => k + " " + v)
          .join("　") +
          "　未测 " +
          (c.total - done),
      ),
    );
    area.append(button("分析本次结果", () => openAnalysis(c.run)));
    return;
  }
  if (c.group) {
    const g = c.group;
    area.append(
      el("h3", g.title),
      el(
        "p",
        "第 " +
          (c.index + 1) +
          " / " +
          c.group_count +
          " 组。按下面步骤观察，只需单独标记失败或跳过的项目。",
      ),
    );
    const controls = new Map();
    for (const t of g.items) {
      const row = el("div");
      row.className = "suite-item";
      const info = el("div");
      info.append(el("strong", t.name), el("p", t.step));
      const pick = el("select");
      pick.setAttribute("aria-label", t.name + "的观察结果");
      pick.replaceChildren(
        ...["待确认", "失败", "跳过"].map((k) => new Option(k, k)),
      );
      row.append(info, pick);
      area.append(row);
      controls.set(t.name, pick);
    }
    const label = el("label", "本组观察记录（有异常时填写）");
    const note = el("textarea");
    note.rows = 2;
    note.maxLength = 1400;
    label.append(note);
    area.append(label);
    const send = async (skip) => {
      const text = note.value.trim(),
        marks = {};
      for (const [name, pick] of controls) {
        const state = skip
          ? "跳过"
          : pick.value === "待确认"
            ? "通过"
            : pick.value;
        if (state === "失败" && !text) {
          note.focus();
          throw Error("请记录故障现象，便于定位和后续 AI 分析。");
        }
        marks[name] = {
          state,
          detail:
            text ||
            (state === "通过"
              ? "操作者已现场观察并确认此项正常。"
              : "操作员跳过此项。"),
        };
      }
      await act("suite-confirm", { suite: c.id, index: c.index, marks });
    };
    const actions = el("div");
    actions.className = "row";
    const confirm = button("确认其余通过并继续", () => send(false));
    confirm.classList.add("primary");
    actions.append(
      confirm,
      button("跳过本组", () => send(true)),
    );
    area.append(actions);
  } else
    area.append(
      el(
        "p",
        "自动检测 " +
          c.automatic_count +
          " 项。每次收发、超时和设备错误均记录在日志中。",
      ),
    );
  area.append(button("停止本轮检测", () => act("stop")));
}
