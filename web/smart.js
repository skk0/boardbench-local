"use strict";

const smartLabels = {
  verified: "已验证",
  fault: "异常",
  absent: "固件声明未安装",
  observe: "需现场观察",
  unknown: "证据不足",
};
const smartNodes = new Map();
let smartCounts, smartRun, smartLoading = false, smartSaving = false, smartReady = false;

function renderCounts() {
  const hardware = !$("hardware").hidden;
  $("countsLabel").textContent = hardware ? "已验证 / 异常" : "验收通过 / 失败";
  const active = state.connected && smartRun === state.run && smartCounts;
  $("counts").textContent = hardware
    ? (active || "— / —")
    : ["通过", "失败"].map((s) => state.results.filter((r) => r.state === s).length).join(" / ");
}

function smartUnavailable(message) {
  smartReady = false;
  smartCounts = null;
  if (state) renderCounts();
  $("smartLive").textContent = "等待连接";
  $("smartLive").classList.remove("live");
  if ($("smartHelp").textContent !== message) $("smartHelp").textContent = message;
  $("smartSummary").hidden = $("smartPending").hidden = true;
  $("smartMain").replaceChildren();
  $("smartPendingRows").replaceChildren();
  smartNodes.clear();
  $("smartSave").disabled = true;
}

function smartRow(row, run) {
  let parts = smartNodes.get(row.id);
  if (!parts) {
    const box = el("article"), title = el("strong"), pill = el("span");
    const detail = el("p"), evidence = el("button");
    box.className = "smart-row";
    pill.className = "smart-pill";
    evidence.className = "text-link";
    box.append(title, pill, detail, evidence);
    parts = { box, title, pill, detail, evidence };
    smartNodes.set(row.id, parts);
  }
  parts.title.textContent = row.name;
  parts.pill.textContent = smartLabels[row.status];
  parts.pill.dataset.status = row.status;
  parts.detail.textContent = row.detail;
  parts.evidence.hidden = !row.log_id;
  if (row.log_id) {
    const ref = "L" + row.log_id;
    parts.evidence.textContent = "日志 " + ref;
    parts.evidence.setAttribute("aria-label", row.name + "的证据日志 " + ref);
    parts.evidence.onclick = handle(() => showEvidence({ run }, ref));
  }
  return parts.box;
}

function fillSmartList(container, rows, run) {
  // Preserve keyboard focus and scroll position while new evidence arrives.
  rows.forEach((row, index) => {
    const node = smartRow(row, run);
    if (container.children[index] !== node)
      container.insertBefore(node, container.children[index] || null);
  });
  while (container.children.length > rows.length) container.lastElementChild.remove();
}

function renderSmart(data) {
  if (!data.connected) {
    const job = state.job && state.suite?.status === "运行中";
    smartUnavailable(job
      ? "当前未连接设备。结束人工验收后即可连接板子，已记录的结果将保留。"
      : "用数据线连接板子的 UART 口，首次在上方选择串口并连接。随后自动检测。");
    return;
  }
  smartReady = true;
  $("smartLive").textContent = "自动检测中";
  $("smartLive").classList.add("live");
  $("smartSave").disabled = smartSaving;
  const ordered = ["fault", "verified", "observe", "unknown", "absent"];
  const groups = Object.fromEntries(ordered.map((s) => [s, []]));
  data.rows.forEach((row) => groups[row.status].push(row));
  smartCounts = groups.verified.length + " / " + groups.fault.length;
  renderCounts();
  const message = groups.fault.length
    ? "设备已报告异常，点击对应的日志可查看原文。"
    : !data.rows.some((r) => r.log_id)
      ? "正在监听板端输出。首条有效读数到达后会自动显示结论。"
      : "结论随板端日志自动更新。断开连接时会保存本次有上报的结果。";
  if ($("smartHelp").textContent !== message) $("smartHelp").textContent = message;
  const summary = $("smartSummary");
  summary.hidden = false;
  const counts = ["verified", "fault", "observe", "unknown"].map((s) =>
    smartLabels[s] + " " + groups[s].length);
  if (groups.absent.length) counts.push(smartLabels.absent + " " + groups.absent.length);
  const summaryKey = counts.join("|");
  if (summary.dataset.key !== summaryKey) {
    summary.dataset.key = summaryKey;
    summary.replaceChildren(...counts.map((s) => el("span", s)));
  }
  const observed = groups.observe.filter((r) => r.log_id);
  fillSmartList($("smartMain"), [...groups.fault, ...observed, ...groups.verified, ...groups.absent], data.run);
  const pending = [...groups.observe.filter((r) => !r.log_id), ...groups.unknown];
  $("smartPendingLabel").textContent = "待验证项目 · " + pending.length;
  $("smartPending").hidden = !pending.length;
  fillSmartList($("smartPendingRows"), pending, data.run);
}

async function updateSmart() {
  if ($("hardware").hidden || !state) return;
  $("smartProfileName").textContent = state.profile;
  $("stopManualForSmart").hidden = state.connected || state.suite?.status !== "运行中";
  if (smartRun !== state.run) {
    smartRun = state.run;
    smartUnavailable("正在读取当前会话…");
    $("smartPending").open = false;
    $("smartSaved").textContent = "";
    for (const [id, format] of [["smartTxt", "txt"], ["smartJson", "json"]]) {
      $(id).href = "/api/report/" + state.run + "/" + format;
      $(id).hidden = false;
    }
  }
  if (!state.connected) {
    renderSmart({ connected: false });
    return;
  }
  if (smartLoading) return;
  smartLoading = true;
  const run = state.run;
  try {
    const data = await get("/api/smart");
    if (state.run === run && data.run === run && data.connected === state.connected)
      renderSmart(data);
  } catch {
    if (state.run === run) smartUnavailable("暂时无法读取检测结果，稍后会自动重试。");
  } finally {
    smartLoading = false;
  }
}

bind("openSmart", () => {
  $("tabs").querySelector('[data-tab="hardware"]').click();
  $("hardware").scrollIntoView({block: "nearest"});
});
bind("smartSave", async () => {
  if (smartSaving || !smartReady) return;
  smartSaving = true;
  $("smartSave").disabled = true;
  const run = state.run;
  try {
    const saved = await get("/api/smart-report", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-BoardBench": token },
      body: JSON.stringify({ run }),
    });
    if (state.run !== run) return;
    $("smartSaved").textContent = "已保存 · " + fmtTime(saved.at);

  } finally {
    smartSaving = false;
    $("smartSave").disabled = !smartReady;
  }
});

bind("stopManualForSmart", () => act("stop"));
