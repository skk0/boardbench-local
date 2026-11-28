"use strict";
const plainStatusText = {
  "固件声明未安装": "固件说没装",
  "未提供读数": "固件没给读数",
  "控制状态": "只知道开关",
  "固件日志": "有输出",
  "固件报错": "板子报错",
  "未上报": "没报告",
  "等待设备": "等板子",
  "上报超时": "很久没报",
  "未启用": "没启用",
};
const statusHelp = {
  "有读数": "板子报了这个部件的读数，说明它在工作。",
  "有应答": "板子能和这个部件通信。",
  "设备报错": "板子说这个部件出错了，点部件名看原文。",
  "无应答": "板子找不到这个部件（没接好、没装或坏了）。",
  "固件声明未安装": "固件明确说这块板子没装这个部件，不算故障。",
  "未提供读数": "固件没有这个部件的驱动或读数。",
  "控制状态": "只知道控制引脚是开是关，不能说明部件本身好坏。",
  "固件日志": "板子的日志里提到了这个部件，但没有明确说好坏。",
  "固件报错": "板子的日志里这个部件报了错。",
  "上报超时": "这个部件很久没报告了，可能卡住或断开。",
  "连接断开": "板子已经断开，显示的是断开前最后的状态。",
  "未上报": "固件还没说过这个部件，不代表坏了。",
};
const plainStatus = (s) => plainStatusText[s] || s;
let selectedPart = "",
  partLogs = [],
  partStamp = "",
  partRequest = 0,
  monitorRun = "";
const componentNodes = new Map();
function clearPart() {
  setLogFollow(true);
  selectedPart = "";
  partLogs = [];
  partStamp = "";
  partRequest++;
  $("partScope").hidden = true;
  logKey = "";
}
async function loadPart(part, force = false) {
  if (!part) return;
  const row = state.components.find((r) => r.part === part);
  if (!row) return;
  const key = [state.run, part, row.log_id].join("/");
  if (!force && partStamp === key) return;
  const req = ++partRequest;
  const rows = await get(
    "/api/components/" + state.run + "/" + encodeURIComponent(part),
  );
  if (req !== partRequest || selectedPart !== part) return;
  partStamp = key;
  partLogs = rows.reverse().map((r) => ({ ...r, source: row.name }));
  $("partScope").hidden = false;
  $("partName").textContent = row.name + " · 最近 200 条记录";
  logKey = "";
  logs();
}
function renderComponents() {
  if (!state.components) return;
  const scope = state.run + "/" + state.connection_id;
  if (monitorRun !== scope) {
    monitorRun = scope;
    clearPart();
    componentNodes.clear();
    $("componentRows").replaceChildren();
  }
  const identity = state.device_identity || {};
  const identityBox = $("deviceIdentity");
  const identityKey = JSON.stringify([scope, identity]);
  if (identityBox.dataset.key !== identityKey) {
    identityBox.dataset.key = identityKey;
    identityBox.replaceChildren();
    for (const [key, label] of [["board", "板子自报型号"], ["chip", "芯片"], ["firmware", "固件"], ["version", "固件版本"]]) {
      const entry = identity[key];
      if (!entry) continue;
      const run = state.run;
      const link = button(label + " · " + entry.value + " · L" + entry.log_id,
        () => showEvidence({ run }, "L" + entry.log_id));
      link.className = "text-link";
      identityBox.append(link);
    }
    identityBox.hidden = !identityBox.childElementCount;
  }
  $("autoUsb").checked = state.auto_usb;
  const records = state.components;
  const seen = records.filter((x) => x.log_id).length;
  $("emptyParts").hidden = records.length > 0 && (!$("onlyReported").checked || seen > 0);
  $("monitorInfo").textContent =
    "测试清单 · " + state.profile + " · " + seen + " 个部件有报告";
  $("monitorHelp").hidden = !(state.connected && seen);
  $("monitorHelp").textContent = "点部件名能看它的记录。";
  $("emptyParts").textContent = state.connected
    ? "已连上，正在等板子报告部件状态。"
    : "连上板子后，这里会显示各个部件的状态和最新读数。";
  for (const r of records) {
    let node = componentNodes.get(r.part);
    if (!node) {
      const tr = el("tr"),
        name = el("td"),
        b = button(r.name, async () => {
          setLogFollow(true);
          selectedPart = r.part;
          partLogs = [];
          logKey = "";
          logs(true);
          $("partScope").hidden = false;
          $("partName").textContent = r.name + " · 正在读取…";
          await loadPart(r.part, true);
          if (selectedPart !== r.part) return;
          renderComponents();
          if (matchMedia("(max-width:970px)").matches)
            $("stream").scrollIntoView({ block: "center" });
        });
      name.append(b);
      const status = el("td"),
        pill = el("span");
      status.append(pill);
      const val = el("td"),
        time = el("td");
      tr.append(name, status, val, time);
      $("componentRows").append(tr);
      node = { tr, pill, val, time, b };
      componentNodes.set(r.part, node);
    }
    node.b.textContent = r.model ? r.name + " · " + r.model : r.name;
    node.tr.hidden = $("onlyReported").checked && !r.log_id;
    node.tr.classList.toggle("selected", r.part === selectedPart);
    node.b.setAttribute("aria-pressed", String(r.part === selectedPart));
    node.pill.textContent = plainStatus(r.status);
    node.pill.title = statusHelp[r.status] || "";
    node.pill.className = "part-state";
    node.pill.dataset.tone = ["设备报错", "固件报错", "无应答"].includes(
      r.status,
    )
      ? "fail"
      : r.status === "上报超时"
        ? "warn"
        : r.status === "有读数" || r.status === "有应答"
          ? "ok"
          : "neutral";
    node.val.textContent = r.value || "—";
    node.time.textContent = r.at ? fmtTime(r.at) : "—";
  }
  if (selectedPart) handle(() => loadPart(selectedPart))();
}
bind("partAll", () => {
  clearPart();
  logs(true);
  renderComponents();
});
$("autoUsb").onchange = handle(() =>
  act("auto-usb", { enabled: $("autoUsb").checked }),
);
$("onlyReported").onchange = () => renderComponents();
