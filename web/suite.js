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
  $("manualFlag").textContent = running ? "进行中 · " + c.phase : "可选";
  $("suiteLaunch").hidden = running;
  $("profile").disabled = running;
  const plan = suitePlans[$("profile").value];
  if (plan) {
    const offline =
      plan.auto && !state.connected ? " 现在没连板子，软件自动查的项目会跳过。" : "";
    $("suiteScope").textContent =
      "共 " + plan.total + " 项：" + plan.auto + " 项软件自动查，" + plan.groups + " 组需要你看或听。" + offline;
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
    el("strong", c.mode === "quick" ? "只查常用的" : "全部检查"),
    el("span", c.phase + " · " + done + " / " + c.total + " 项"),
  );
  area.append(status);
  const meter = el("progress");
  meter.max = c.total;
  meter.value = done;
  meter.setAttribute("aria-label", "检查进度");
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
          "　没测 " +
          (c.total - done),
      ),
    );
    area.append(button("让 AI 看看这次结果", () => openAnalysis(c.run)));
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
          " 组。看完选结果，没选的算跳过。",
      ),
    );
    const controls = new Map();
    for (const t of g.items) {
      const row = el("div");
      row.className = "suite-item";
      const info = el("div");
      info.append(el("strong", t.name), el("p", t.step));
      const pick = el("select");
      pick.setAttribute("aria-label", t.name + "的结果");
      pick.replaceChildren(
        ...["未记录", "通过", "失败", "跳过"].map((k) => new Option(k === "未记录" ? "还没看" : k, k)),
      );
      row.append(info, pick);
      area.append(row);
      controls.set(t.name, pick);
    }
    const label = el("label", "看到的情况（有问题时一定要写）");
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
          : pick.value === "未记录"
            ? "跳过"
            : pick.value;
        if (state === "失败" && !text) {
          note.focus();
          throw Error("选了“失败”请写一下看到的现象，方便找原因。");
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
    const confirm = button("保存，下一组", () => send(false));
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
        "软件正在自动查 " +
          c.automatic_count +
          " 项，每一步都会记在右边的记录里。",
      ),
    );
  area.append(button("不查了", () => act("stop")));
}
