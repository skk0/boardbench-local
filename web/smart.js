"use strict";

const smartLabels = {
  verified: "已验证",
  fault: "异常",
  absent: "固件说没装",
  observe: "需要你看一眼",
  unknown: "还看不出来",
};
const smartNodes = new Map();
let smartCounts, smartRun, smartLoading = false, smartSaving = false, smartReady = false;

function renderCounts() {
  const hardware = !$("hardware").hidden;
  $("countsLabel").textContent = hardware ? "正常 / 异常" : "检查通过 / 没通过";
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
    parts.evidence.setAttribute("aria-label", row.name + "的原始记录 " + ref);
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
      ? "现在没连板子。结束人工检查后就能连，已经记下的结果会保留。"
      : "先在上面连上板子（用数据线接到电脑），连上后自动开始体检。");
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
    ? "板子报告了问题，点后面的“日志 L…”能看原文。"
    : !data.rows.some((r) => r.log_id)
      ? "正在听板子说话，收到第一条有用的信息就会显示结论。"
      : "结论会随着板子的输出自动更新；断开时会自动保存一份。";
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
  $("smartPendingLabel").textContent = "还没看出结果的项目 · " + pending.length;
  $("smartPending").hidden = !pending.length;
  fillSmartList($("smartPendingRows"), pending, data.run);
}

async function updateSmart() {
  if ($("hardware").hidden || !state) return;
  $("smartProfileName").textContent = state.profile;
  $("stopManualForSmart").hidden = state.connected || state.suite?.status !== "运行中";
  if (smartRun !== state.run) {
    smartRun = state.run;
    smartUnavailable("正在读取…");
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
    if (state.run === run) smartUnavailable("暂时读不到体检结果，稍后自动重试。");
  } finally {
    smartLoading = false;
  }
}

bind("openSmart", () => {
  document.querySelector('.rail [data-tab="hardware"]').click();
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
