"use strict";
// 刷固件 wizard: 选文件 → 识别和检查 → 看清会改哪些地方 → 备份 → 刷写 → 校验 → 开机确认.
let flashState = null,
  flashLoading = false,
  flashKey = "",
  flashChoice = { confirm: false, full: false };
const flashKinds = {
  merged: "整包固件：引导程序、分区表和应用都在一个文件里",
  app: "只有应用程序：会放进板子现有的应用区，引导程序和分区表不动",
  build: "编译文件夹：按编译结果里写好的位置，把几个文件分别刷进去",
};
const checkWords = { pass: "没问题", warn: "注意", block: "不能刷" };
const kb = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.ceil(n / 1024) + " KB");
const hex = (n) => "0x" + n.toString(16).padStart(6, "0");

function flashPort() {
  return $("transport").value === "串口" ? $("port").value : "";
}
async function flashAct(op, data = {}) {
  await act(op, data);
  await renderFlash(true);
}
async function openFirmware(body, headers) {
  const state = await get("/api/flash/open", {
    method: "POST",
    headers: { "X-BoardBench": token, ...headers },
    body,
  });
  flashChoice = { confirm: false, full: false };
  flashState = state;
  flashKey = "";
  drawFlash();
}
const openPath = (path) => openFirmware(JSON.stringify({ path }), { "Content-Type": "application/json" });
const uploadFile = (file) =>
  openFirmware(file, { "Content-Type": "application/octet-stream", "X-File-Name": encodeURIComponent(file.name) });

async function renderFlash(force = false) {
  if (!token || flashLoading || ($("flash").hidden && !force && !flashState?.running)) return;
  flashLoading = true;
  try {
    flashState = await get("/api/flash");
  } catch {
    return;
  } finally {
    flashLoading = false;
  }
  drawFlash();
}

function section(title, ...nodes) {
  const box = el("section");
  box.className = "flash-card";
  box.append(el("h3", title), ...nodes);
  return box;
}
function checkbox(text, checked, onchange) {
  const label = el("label");
  label.className = "checkline";
  const input = el("input");
  input.type = "checkbox";
  input.checked = checked;
  input.onchange = handle(() => onchange(input.checked));
  label.append(input, document.createTextNode(text));
  return label;
}

function pickStep(s) {
  const nodes = [el("p", "不知道你拿到的是哪种文件也没关系：单个 .bin、发布用的 .zip、或者 ESP-IDF 工程的 build 文件夹都行，软件会自己认出来。也可以把文件直接拖到这里。")];
  const row = el("div");
  row.className = "row";
  const api = window.pywebview?.api;
  const file = el("input");
  file.type = "file";
  file.accept = ".bin,.zip";
  file.hidden = true;
  file.onchange = handle(async () => {
    if (file.files[0]) await uploadFile(file.files[0]);
    file.value = "";
  });
  const chooseFile = button("选择固件文件（.bin / .zip）…", async () => {
    if (!api) return file.click();
    const path = await api.pick_firmware(false);
    if (path) await openPath(path);
  });
  chooseFile.classList.add("primary");
  row.append(chooseFile, file);
  if (api)
    row.append(button("选择编译文件夹…", async () => {
      const path = await api.pick_firmware(true);
      if (path) await openPath(path);
    }));
  nodes.push(row);
  const pathLabel = el("label", api ? "或者粘贴文件 / 文件夹的路径" : "选编译文件夹：浏览器里不能直接选文件夹，请把路径粘贴到这里");
  const path = el("input");
  path.placeholder = "例如 /Users/me/xiaozhi/build";
  path.spellcheck = false;
  pathLabel.append(path);
  const go = button("打开", () => openPath(path.value));
  path.onkeydown = (e) => e.key === "Enter" && go.click();
  const pathRow = el("div");
  pathRow.className = "row flash-path";
  pathRow.append(pathLabel, go);
  nodes.push(pathRow);
  return section("第 1 步：选固件", ...nodes);
}

function firmwareCard(s) {
  const f = s.firmware;
  const list = el("ul");
  list.className = "flash-files";
  for (const r of f.files)
    list.append(el("li", (r.offset === null ? "位置由板子的分区决定" : hex(r.offset)) + " · " + kb(r.size) + " · " + r.label));
  const change = button("换一个文件", () => flashAct("flash-close"));
  change.disabled = s.running;
  return section("选好的固件", el("p", f.source), el("p", flashKinds[f.kind]), list, change);
}

function checkStep(s) {
  const port = flashPort();
  const nodes = [];
  if (!s.board)
    nodes.push(el("p", "软件会让板子重启进“下载模式”，读出芯片型号、闪存大小和现在的分区，再和固件逐项比对。这一步只读，不会写板子。"));
  nodes.push(el("p", port ? "要刷的板子：" + port + "（在上方“选板子”里可以换）" : "请先在上方“怎么连”选 USB 线，再在“选板子”里选你的板子。"));
  if (s.board) {
    const b = s.board;
    const facts = el("dl");
    facts.className = "flash-facts";
    for (const [k, v] of [
      ["芯片", b.description || b.chip],
      ["闪存大小", b.flash_size ? kb(b.flash_size) : "—"],
      ["板子自报型号", b.reported_board?.value || "没看到"],
      ["板子编号（MAC）", b.mac || "—"],
    ]) {
      const row = el("div");
      row.append(el("dt", k), el("dd", v));
      facts.append(row);
    }
    nodes.push(facts);
  }
  if (s.plan) {
    const list = el("div");
    list.className = "flash-checks";
    for (const c of s.plan.checks) {
      const row = el("div");
      row.className = "flash-check";
      const pill = el("span", checkWords[c.state]);
      pill.className = "flash-pill";
      pill.dataset.state = c.state;
      row.append(pill, el("strong", c.title), el("p", c.detail));
      list.append(row);
    }
    nodes.push(list);
  }
  const go = button(s.board ? "重新检查" : "连接板子并检查（板子会重启一次）", () => flashAct("flash-inspect", { port }));
  if (!s.board) go.classList.add("primary");
  go.disabled = !port || s.running;
  nodes.push(go);
  return section("第 2 步：识别和检查", ...nodes);
}

function planStep(s) {
  const p = s.plan;
  const table = el("table");
  table.className = "flash-plan";
  const head = table.createTHead().insertRow();
  for (const t of ["位置", "哪一块", "大小", "写什么"]) head.append(el("th", t));
  const body = table.createTBody();
  for (const w of p.writes) {
    const tr = body.insertRow();
    tr.append(el("td", hex(w.offset)), el("td", w.where), el("td", kb(w.size)),
      el("td", w.blank ? "清空（写成空白）" : w.label));
  }
  const nodes = [el("p", "下面每一行都会被改写，其他地方保持原样。刷之前会先把这些地方原来的内容备份到电脑上。"), table];
  if (p.skipped.length)
    nodes.push(el("p", "不会动：" + p.skipped.map((x) => hex(x.offset) + " " + x.label).join("；")));
  nodes.push(checkbox("恢复出厂（会清空 Wi-Fi 等设置）", s.factory_reset, (v) => flashAct("flash-factory", { value: v })));
  const confirmCheck = p.checks.find((c) => c.confirm);
  if (confirmCheck)
    nodes.push(checkbox("我确认：型号对不上我也要刷（" + confirmCheck.detail.split("。")[0] + "）", flashChoice.confirm,
      (v) => { flashChoice.confirm = v; flashKey = ""; drawFlash(); }));
  nodes.push(checkbox("同时备份整块闪存（更保险，多花几分钟）", flashChoice.full, (v) => { flashChoice.full = v; }));
  const backupBytes = p.backup.reduce((n, r) => n + r.end - r.start, 0) + (flashChoice.full ? s.board.flash_size : 0);
  const writeBytes = p.writes.reduce((n, w) => n + w.size, 0);
  // Measured on an ESP32-S3 over its USB port: reading ~45 KB/s, compressed writing ~110 KB/s.
  const minutes = Math.max(1, Math.ceil((backupBytes / 45000 + writeBytes / 110000 + 30) / 60));
  const start = button("备份并开始刷写", () => {
    const box = modal("确定开始刷写吗？");
    const steps = el("ol");
    for (const t of ["先备份板子原来的内容（" + kb(backupBytes) + "）", "写入新固件（" + kb(writeBytes) + "）并逐段校验",
      "重启板子，看它能不能正常开机"]) steps.append(el("li", t));
    box.append(steps, el("p", "大约需要 " + minutes + " 分钟。刷写时请不要拔线，也不要关掉软件。"));
    const row = el("div");
    row.className = "row";
    const ok = button("开始", async () => {
      $("dialog").close();
      await flashAct("flash-start", { port: flashPort(), confirm_board: flashChoice.confirm, full_backup: flashChoice.full });
    });
    ok.classList.add("primary");
    row.append(ok, button("再想想", () => $("dialog").close()));
    box.append(row);
  });
  start.classList.add("primary");
  start.disabled = p.blocked || (p.needs_confirm && !flashChoice.confirm) || !flashPort() || s.running;
  const why = p.blocked ? "有“不能刷”的检查项，所以不能开始。" : p.needs_confirm && !flashChoice.confirm ? "型号对不上，需要先勾选上面的确认。" : "";
  nodes.push(start);
  if (why) nodes.push(el("p", why));
  return section("第 3 步：看清会改哪些地方", ...nodes);
}

function progressStep(s) {
  const nodes = [];
  const pr = s.progress;
  if (pr) {
    nodes.push(el("p", pr.text));
    if (pr.total) {
      const bar = el("progress");
      bar.max = pr.total;
      bar.value = pr.done;
      bar.setAttribute("aria-label", pr.text);
      nodes.push(bar, el("p", Math.floor((100 * pr.done) / pr.total) + "%"));
    }
  }
  nodes.push(el("p", "请不要拔线，也不要关掉软件。"));
  if (s.step <= 3) nodes.push(button("停止（只能在开始写入前停）", () => act("stop")));
  return section(s.steps[Math.min(s.step, 6)] + "中…", ...nodes);
}

function resultCard(s) {
  const r = s.result;
  const box = section(r.state === "ok" ? "完成" : r.state === "fail" ? "没有成功" : "请确认一下", el("p", r.text));
  box.dataset.state = r.state;
  const row = el("div");
  row.className = "row";
  if (s.backup)
    row.append(button("恢复刷写前的样子", () => restoreBackup(s.backup.id)));
  row.append(button("再刷一个", () => flashAct("flash-close")), button("看板子状态", () =>
    document.querySelector('.rail [data-tab="monitor"]').click()));
  box.append(row);
  return box;
}

function restoreBackup(id) {
  const box = modal("恢复备份");
  box.append(el("p", "会把备份里记下的那些地方写回板子，让板子回到刷写之前的样子。Wi-Fi 等设置不会被改回去（除非那次刷写时选了“恢复出厂”）。要用备份它的那块板子。"),
    el("p", flashPort() ? "要恢复的板子：" + flashPort() : "请先在上方选好板子的 USB 端口。"));
  const row = el("div");
  row.className = "row";
  const ok = button("开始恢复", async () => {
    $("dialog").close();
    await flashAct("flash-restore", { port: flashPort(), backup: id });
  });
  ok.classList.add("primary");
  ok.disabled = !flashPort();
  row.append(ok, button("取消", () => $("dialog").close()));
  box.append(row);
}

function backupList(s) {
  if (!s.backups.length) return section("以前的备份", el("p", "还没有。每次刷写前软件都会自动备份，放在 " + s.backup_dir + "。"));
  const list = el("div");
  for (const b of s.backups) {
    const item = el("div");
    item.className = "hist";
    const before = b.before ? "刷之前是 " + b.before.project + " " + b.before.version + " · " : "";
    item.append(el("strong", new Date(b.created).toLocaleString() + " · " + b.reason),
      el("p", before + kb(b.size) + (b.full ? " · 含整块闪存" : "") + " · 板子 " + b.mac + (b.complete ? "" : " · 那次刷写没有完成")),
      el("p", b.folder));
    const restore = button("恢复这个备份", () => restoreBackup(b.id));
    restore.disabled = s.running;
    item.append(restore);
    list.append(item);
  }
  return section("以前的备份", list);
}

function drawFlash() {
  const s = flashState;
  if (!s) return;
  const key = JSON.stringify([s, flashChoice.confirm, flashPort()]);
  if (key === flashKey) return;
  flashKey = key;
  $("flashSteps").replaceChildren(...s.steps.map((t, i) => {
    const li = el("li", t);
    li.className = i < s.step ? "done" : i === s.step ? "current" : "";
    if (i === s.step) li.setAttribute("aria-current", "step");
    return li;
  }));
  const parts = [];
  if (!s.firmware) parts.push(pickStep(s));
  else {
    parts.push(firmwareCard(s));
    const busy = s.running && s.step >= 3;
    if (!busy) parts.push(checkStep(s));
    if (s.plan && !busy && !s.result) parts.push(planStep(s));
    if (s.running) parts.push(progressStep(s));
    if (s.result && !s.running) parts.push(resultCard(s));
  }
  parts.push(backupList(s));
  $("flashBody").replaceChildren(...parts);
}

$("flashBody").ondragover = (e) => e.preventDefault();
$("flashBody").ondrop = (e) => {
  e.preventDefault();
  const file = e.dataTransfer.files[0];
  if (file) handle(() => uploadFile(file))();
};
$("port").addEventListener("change", () => drawFlash());
