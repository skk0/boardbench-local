"use strict";
let selectedPart = "",
  partLogs = [],
  partStamp = "",
  partRequest = 0,
  monitorRun = 0;
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
  $("partName").textContent = row.name + " · 最近 200 条";
  logKey = "";
  logs();
}
function renderComponents() {
  if (!state.components) return;
  if (monitorRun !== state.run) {
    monitorRun = state.run;
    clearPart();
    componentNodes.clear();
    $("componentRows").replaceChildren();
  }
  $("autoUsb").checked = state.auto_usb;
  const records = state.components;
  const seen = records.filter((x) => x.log_id).length;
  $("emptyParts").hidden = !$("onlyReported").checked || seen > 0;
  $("monitorInfo").textContent =
    state.profile + " · " + seen + " / " + records.length + " 个元件有上报";
  $("monitorHelp").textContent = state.connected
    ? seen
      ? "点击元件可单独查看它的带时间戳日志。"
      : "正在监听。固件输出的日志会出现在右侧，支持逐元件上报的固件会在此显示读数。"
    : state.auto_usb
      ? state.usb_message + "。使用 USB 数据线连接板子的 UART 串口。"
      : "USB 自动连接已关闭，可从上方选择串口。";
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
          $("partName").textContent = r.name + " · 正在读取";
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
    node.tr.hidden = $("onlyReported").checked && !r.log_id;
    node.tr.classList.toggle("selected", r.part === selectedPart);
    node.b.setAttribute("aria-pressed", String(r.part === selectedPart));
    node.pill.textContent = r.status;
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
