"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, text) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  return n;
};
let token,
  state,
  frozenLogs = null,
  connectionPending = "",
  refreshSequence = 0,
  appliedSequence = 0,
  timer,
  testKey = "",
  logKey = "",
  busy = false;
function notice(message) {
  if ($("dialog").open) {
    $("dialogFeedback").textContent = message;
    $("dialogFeedback").hidden = false;
    return;
  }
  $("feedback").textContent = message;
  $("feedback").className = "show";
  clearTimeout(timer);
  timer = setTimeout(() => ($("feedback").className = ""), 5000);
}
async function get(path, init) {
  const r = await fetch(path, init);
  if (!r.ok) {
    const text = await r.text();
    let message = text;
    try {
      message = JSON.parse(text).error || text;
    } catch {}
    throw Error(message);
  }
  return r.json();
}
async function act(op, data = {}) {
  await get("/api/" + op, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-BoardBench": token },
    body: JSON.stringify({ ...data, run: state.run }),
  });
  await refresh();
}
function handle(fn) {
  return async () => {
    try {
      await fn();
    } catch (e) {
      notice(e.message);
    }
  };
}
function bind(id, fn) {
  $(id).onclick = handle(fn);
}
function button(title, fn) {
  const b = el("button", title);
  b.onclick = handle(fn);
  return b;
}
const fmtTime = (ts) =>
  new Date(ts).toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
  });
function modal(title) {
  const body = el("div");
  body.id = "dialogBody";
  $("dialogBody").replaceWith(body);
  $("dialogTitle").textContent = title;
  $("dialogFeedback").hidden = true;
  $("dialogFeedback").textContent = "";
  if (!$("dialog").open) $("dialog").showModal();
  $("dialog").scrollTop = 0;
  return body;
}
async function setup() {
  const d = await get("/api/setup");
  token = d.token;
  suitePlans = d.plans;
  const selected = $("profile").value;
  $("profile").replaceChildren(...d.profiles.map((x) => new Option(x, x)));
  if (selected) $("profile").value = selected;
  if (!$("ip").value) $("ip").value = d.ip;
}
async function ports() {
  const rows = await get("/api/ports");
  const old = $("port").value;
  $("port").replaceChildren(
    ...rows.map((x) => new Option(x.path + " · " + (x.saved?.name || x.label), x.path)),
  );
  if (!rows.length) $("port").add(new Option("没找到 USB 端口", ""));
  if (rows.some((x) => x.path === old)) $("port").value = old;
  portSettings(rows);
  $("portNote").dataset.usb = rows.length
    ? "找到 " + rows.length + " 个 USB 端口，选你的板子后点“连接”。"
    : "没找到 USB 端口。插上数据线后点“重新找端口”。";
  if (state) buttons();
}
function buttons() {
  const connected = state.connected,
    job = Boolean(state.job),
    port = Boolean($("port").value);
  const mode = $("transport").value;
  const net = mode !== "串口";
  $("transport").closest(".controls").dataset.mode = net ? "network" : "serial";
  const input = net ? $("netPort") : $("baud");
  const validNumber = input.value !== "" && input.validity.valid &&
    Number.isInteger(input.valueAsNumber);
  const ready = validNumber && (net ? Boolean($("host").value.trim()) : port);
  $("linkTitle").textContent = connected ? state.port : "连接板子";
  $("linkState").textContent = connected
    ? mode === "UDP"
      ? "已设置好板子的地址"
      : "已连上板子"
    : "还没连上板子";
  $("portNote").textContent = connected
    ? mode === "串口"
      ? "正在接收板子的输出（只听不发）· 速度 " + state.baud
      : "收到和发出的内容都会记在右边的记录里。"
    : net
      ? !$("host").value.trim() ? "请填板子的 IP 地址或名字。"
        : !validNumber ? "端口号要填 1–65535 之间的整数。" : "填好了，可以点“连接”。"
      : port && !validNumber ? "速度要填 300–4000000 之间的整数，不确定就填 115200。"
      : $("portNote").dataset.usb || "正在找电脑上的 USB 端口…";
  document
    .querySelector(".instrument")
    .classList.toggle("connected", connected);
  const artKey = mode + connected;
  if ($("boardArt").dataset.key !== artKey) {
    drawInstrument(connected, mode === "串口" ? "USB" : mode);
    $("boardArt").dataset.key = artKey;
  }
  document.querySelectorAll(".usb").forEach((e) => (e.hidden = net));
  document.querySelectorAll(".net").forEach((e) => (e.hidden = !net));
  $("host").disabled =
    $("netPort").disabled =
    $("transport").disabled =
      connected || job || Boolean(connectionPending);
  $("connect").textContent = connectionPending || (connected ? "断开" : "连接");
  $("connect").disabled = Boolean(connectionPending) || (!connected && (!ready || job));
  $("port").disabled =
    $("baud").disabled =
    $("framing").disabled =
      connected || job || Boolean(connectionPending);
  $("probe").disabled = $("backup").disabled = net || !port || job || Boolean(connectionPending);
  $("stop").disabled = !job;
  $("all").disabled =
    !connected || job || !state.tests.some((t) => t.kind === "serial");
  $("send").disabled = !connected || job;
  $("new").disabled = job || Boolean(state.echo);
  $("ending").disabled = $("encoding").value === "HEX";
}
function mark(t) {
  const body = modal(t.name);
  body.append(el("p", t.step));
  const label = el("label", "实测现象或跳过原因");
  const input = el("textarea");
  input.rows = 3;
  label.append(input);
  const actions = el("div");
  actions.className = "row";
  for (const status of ["通过", "失败", "跳过"])
    actions.append(
      button(status, async () => {
        if (!input.value.trim()) {
          input.focus();
          throw Error("请先填写观察结果。");
        }
        await act("mark", { name: t.name, state: status, detail: input.value });
        $("dialog").close();
      }),
    );
  body.append(label, actions);
}
function tests() {
  const key = JSON.stringify([
    state.run,
    state.results,
    state.connected,
    state.job,
  ]);
  if (key === testKey) return;
  testKey = key;
  const open = new Set(
    [...$("tests").querySelectorAll("details[open]")].map(
      (d) => d.dataset.name,
    ),
  );
  const results = new Map(state.results.map((r) => [r.name, r]));
  const nodes = state.tests.map((t) => {
    const row = el("details");
    row.className = "test";
    row.dataset.name = t.name;
    row.dataset.result = results.get(t.name)?.state || "未测";
    row.open = open.has(t.name);
    const summary = el("summary");
    summary.append(
      el("span", t.name),
      el(
        "small",
        (t.kind === "serial" ? "自动" : "人工") +
          " · " +
          (results.get(t.name)?.state || "还没测"),
      ),
    );
    row.append(summary, el("p", t.step));
    if (t.kind === "serial")
      row.append(el("p", "发送 " + t.tx + "  →  等板子回答里出现 " + t.expect));
    if (results.has(t.name)) row.append(el("p", results.get(t.name).detail));
    const b = button(t.kind === "serial" ? "发命令检查" : "记下看到的情况", () =>
      t.kind === "serial" ? act("cases", { name: t.name }) : mark(t),
    );
    b.disabled = t.kind === "serial" && (!state.connected || !!state.job);
    row.append(b);
    return row;
  });
  for (const r of state.results.filter(
    (r) => !state.tests.some((t) => t.name === r.name),
  )) {
    const p = el("p", r.name + " · " + r.state + " · " + r.detail);
    nodes.unshift(p);
  }
  $("tests").replaceChildren(...nodes);
  $("suite").textContent = state.profile + " · " + state.tests.length + " 项";
}
function currentLogs() {
  return selectedPart ? partLogs : state?.logs || [];
}
function setLogFollow(follow) {
  frozenLogs = follow ? null : currentLogs().slice();
  $("pause").textContent = follow ? "停在这里" : "回到最新";
  $("pause").setAttribute("aria-pressed", String(!follow));
  updateLogCount();
}
function updateLogCount() {
  const count = $("stream").childElementCount && $("stream").firstElementChild.matches(".line")
    ? $("stream").childElementCount : 0;
  $("logCount").textContent = "显示 " + count + " 条 · " +
    (frozenLogs ? "画面停住了，后台还在记录" : "自动滚到最新");
}
function logs(force = false) {
  if (!state || (frozenLogs && !force)) return;
  const query = $("search").value.toLowerCase(),
    level = $("level").value,
    hex = $("hex").checked;
  const records = frozenLogs || currentLogs();
  const key = [
    state.run,
    selectedPart,
    records.at(-1)?.id,
    query,
    level,
    hex,
  ].join("/");
  if (key === logKey && !force) return;
  logKey = key;
  const deviceLevel = (r) => {
    if (r.level !== "RX") return "";
    const text = r.message.replace(/\x1b\[[0-9;]*m/g, "").trimStart();
    if (/^(?:E\s*\(\d+\)|ERR(?:OR)?\b|panic\b|Guru Meditation)/i.test(text))
      return "ERROR";
    if (/^(?:W\s*\(\d+\)|WARN(?:ING)?\b)/i.test(text)) return "WARN";
    return "";
  };
  const lines = records.filter(
    (r) =>
      (!level || r.level === level || deviceLevel(r) === level) &&
      [r.message, r.source, r.at, fmtTime(r.at), r.raw].some((s) =>
        s.toLowerCase().includes(query),
      ),
  );
  const previousTop = $("stream").scrollTop;
  $("stream").replaceChildren(
    ...lines.map((r) => {
      const line = el("div");
      line.className = "line";
      const time = el("time", fmtTime(r.at));
      time.title = r.at;
      const tag = el("span", r.level);
      tag.className = "tag " + (deviceLevel(r) || r.level);
      const msg = el(
        "span",
        r.source +
          (deviceLevel(r) ? " " + deviceLevel(r) : "") +
          " │ " +
          (hex && r.raw ? r.raw : r.message),
      );
      msg.className = "msg";
      line.append(time, tag, msg);
      return line;
    }),
  );
  $("clearLogFilters").hidden = !query && !level;
  if (!lines.length) {
    const message = query || level ? "没有符合条件的记录。"
      : selectedPart ? "这个部件还没有报告过。" : "还没有记录。连上板子后，板子输出的内容会出现在这里。";
    $("stream").append(el("p", message));
  }
  $("stream").scrollTop = frozenLogs ? previousTop : $("stream").scrollHeight;
  updateLogCount();
}
function numeric(line) {
  try {
    const v = JSON.parse(line);
    if (v?.bb === 1 && typeof v.part === "string")
      return v.part === "imu" && v.status === "sample" ? motionChannels(v.value) : [];
    if (v && typeof v === "object")
      return Object.entries(v).filter(([, x]) => Number.isFinite(x));
  } catch {}
  const m = [
    ...line.matchAll(
      /([\w.\-]+)\s*=\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)/gi,
    ),
  ];
  if (m.length)
    return m
      .map((x) => [x[1], Number(x[2])])
      .filter(([, x]) => Number.isFinite(x));
  const cells = line.trim().split(",");
  if (
    cells.length &&
    cells.every((v) => v.trim() !== "" && Number.isFinite(Number(v)))
  )
    return cells.map((x, i) => ["CH" + (i + 1), Number(x)]);
  return [];
}
function plots() {
  if ($("signal").hidden) return;
  const channels = new Map();
  for (const r of state.logs.filter((r) => r.level === "RX"))
    for (const [k, v] of numeric(r.message)) {
      if (!channels.has(k) && channels.size < 16) channels.set(k, []);
      channels.get(k)?.push(v);
    }
  $("charts").replaceChildren();
  if (!channels.size)
    $("charts").append(el("p", '还没有能画的数字。板子输出像 {"x":1.2,"y":3.4} 这样的行，就会出现曲线。'));
  for (const [k, vs] of channels) {
    const box = el("div");
    box.className = "plot";
    const low = Math.min(...vs),
      high = Math.max(...vs);
    box.append(
      el("strong", k + "  " + vs.at(-1) + "    [" + low + " … " + high + "]"),
    );
    const c = el("canvas");
    c.width = 700;
    c.height = 100;
    c.setAttribute("aria-label", k + " 趋势");
    box.append(c);
    $("charts").append(box);
    const ctx = c.getContext("2d");
    ctx.strokeStyle =
      document.documentElement.dataset.theme === "dark" ? "#78baff" : "#0866bb";
    ctx.lineWidth = 2;
    ctx.beginPath();
    vs.forEach((v, i) => {
      const x = 4 + (i * 690) / Math.max(1, vs.length - 1),
        y = high === low ? 50 : 95 - ((v - low) * 90) / (high - low);
      if (i) ctx.lineTo(x, y);
      else {
        ctx.moveTo(x, y);
        ctx.fillRect(x, y, 2, 2);
      }
    });
    ctx.stroke();
  }
}
async function refresh() {
  const sequence = ++refreshSequence;
  const next = await get("/api/state");
  if (sequence < appliedSequence) return;
  appliedSequence = sequence;
  const changed = state?.run !== next.run;
  state = next;
  if (changed) {
    $("profile").value = state.profile;
    setLogFollow(true);
  }
  $("badge").textContent = state.connected
    ? "● 监视端口已连接"
    : "○ 监视端口未连接";
  $("run").textContent = "#" + state.run;
  renderCounts();
  $("bytes").textContent =
    state.rx.toLocaleString() + " / " + state.tx.toLocaleString();
  $("job").textContent = state.job || "没有任务";
  $("echo").textContent = state.echo ? "结束回声测试" : "开始回声测试";
  $("ota").value = state.echo;
  $("echoInfo").textContent = state.echo
    ? "正在等板子连过来 · 已收到 " + state.frames + " 段声音"
    : "回声测试没开";
  const checkin = state.echo_checkin;
  const checkinLink = $("echoCheckin");
  checkinLink.hidden = !checkin;
  if (checkin) {
    const summary = Object.entries(checkin.fields).map(([k, v]) => k + " " + v).join(" · ");
    checkinLink.textContent = "板子最近一次连过来 · " + (summary || "没说型号") + " · L" + checkin.log_id;
    checkinLink.onclick = handle(() => showEvidence({run: checkin.run}, "L" + checkin.log_id));
  }
  $("logCSV").href = "/api/report/" + state.run + "/csv";
  $("template").href =
    "/api/template?name=" + encodeURIComponent($("profile").value);
  if (
    state.usb_ports &&
    $("port").dataset.paths !==
      JSON.stringify(state.usb_ports)
  ) {
    const chosen = $("port").value;
    const rows = state.usb_ports;
    $("port").dataset.paths = JSON.stringify(rows);
    $("port").replaceChildren(
      ...rows.map((x) => new Option(x.path + " · " + (x.saved?.name || x.label), x.path)),
    );
    if (!rows.length) $("port").add(new Option("没找到 USB 端口", ""));
    if (rows.some((x) => x.path === chosen)) $("port").value = chosen;
    portSettings(rows);
    $("portNote").dataset.usb = rows.length
      ? "找到 " + rows.length + " 个 USB 端口，选你的板子后点“连接”。"
      : "请用 USB 数据线把板子接到电脑上。";
  }
  if (state.connected) {
    $("transport").value = state.transport;
    if (state.transport === "串口") {
      $("port").value = state.port;
      $("baud").value = state.baud;
      $("framing").value = state.framing;
    } else {
      $("host").value = state.network_host;
      $("netPort").value = state.network_port;
    }
  }
  buttons();
  tests();
  renderSuite();
  $("endurance").textContent = state.endurance ? "稳定性测试中 · " + state.endurance.variant : "长时间稳定性测试";
  $("usbMessage").textContent = state.auto_usb && !state.connected ? state.usb_message : "";
  $("usbMessage").hidden = !$("usbMessage").textContent;
  $("usbMessage").classList.toggle("warn", /重启|断开|失败|停止/.test(state.usb_message || ""));
  renderComponents();
  renderFlash();
  renderOverview(state);
  renderMotion(state);
  updateSmart();
  logs();
  plots();
}
bind("scan", ports);
bind("connect", async () => {
  if ($("connect").disabled || connectionPending) return;
  connectionPending = state.connected ? "正在断开…" : "正在连接…";
  buttons();
  try {
    if (state.connected) await act("disconnect");
    else if ($("transport").value === "串口") {
      await act("connect", {
        port: $("port").value, baud: $("baud").value, framing: $("framing").value,
      });
    } else {
      await act("network", {
        transport: $("transport").value, host: $("host").value.trim(), port: $("netPort").value,
      });
    }
  } finally {
    connectionPending = "";
    buttons();
  }
});
bind("new", () => act("new", { profile: $("profile").value }));
bind("probe", () => {
  const body = modal("读芯片型号和闪存");
  body.append(
    el(
      "p",
      "会先停止接收 " +
        $("port").value +
        "，让板子重启进“下载模式”，读出芯片型号、闪存大小等信息，读完板子会再重启一次。不会改动板子里的内容。",
    ),
    button("重启并读取", async () => {
      await act("probe", { port: $("port").value });
      $("dialog").close();
    }),
  );
});
bind("backup", () => {
  const body = modal("备份整块闪存");
  body.append(
    el(
      "p",
      "板子会重启进“下载模式”，把里面的全部内容读出来存到电脑上（16 MB 大约要几分钟），不会改动板子。读完后在“导出报告”里能下载这个备份文件。文件里可能有 Wi-Fi 密码等信息，请别随便发给别人。",
    ),
    button("重启并开始备份", async () => {
      await act("backup", { port: $("port").value });
      $("dialog").close();
    }),
  );
});
bind("all", () => act("cases", { all: true }));
bind("stop", () => act("stop"));
bind("send", () =>
  act("send", {
    text: $("sendText").value,
    hex: $("encoding").value === "HEX",
    ending: $("ending").value,
  }),
);
bind("note", async () => {
  await act("note", { text: $("noteText").value });
  $("noteText").value = "";
});
bind("echo", () =>
  act("echo", {
    enable: !state.echo,
    host: $("ip").value,
    port: $("lanPort").value,
  }),
);
bind("import", () => $("file").click());
$("file").onchange = handle(async () => {
  const f = $("file").files[0];
  if (!f) return;
  try {
    const data = JSON.parse(await f.text());
    previewProfile(data);
  } finally {
    $("file").value = "";
  }
});
$("profile").onchange = () => {
  $("template").href =
    "/api/template?name=" + encodeURIComponent($("profile").value);
  if (state) renderSuite();
};
$("transport").onchange =
  $("encoding").onchange =
    () => buttons();
for (const id of ["host", "netPort", "baud"]) $(id).oninput = () => state && buttons();
bind("pause", () => {
  setLogFollow(Boolean(frozenLogs));
  if (!frozenLogs) logs(true);
});
$("stream").addEventListener("scroll", () => {
  const stream = $("stream");
  if (!frozenLogs && stream.clientHeight && stream.scrollHeight - stream.clientHeight - stream.scrollTop > 12) {
    setLogFollow(false);
  }
});
bind("clearLogFilters", () => {
  $("search").value = $("level").value = "";
  logs(true);
  $("search").focus();
});
for (const id of ["search", "level", "hex"]) $(id).oninput = () => logs(true);
bind("history", async () => {
  const rows = await get("/api/history");
  const body = modal("导出报告和以前的记录");
  body.append(el("p", "每次连接或换板子型号都会新开一条记录。选一条下载：TXT 给人看，JSON 是完整数据（给工具用），表格（CSV）能用 Excel 打开。"));
  archiveTools(body);
  for (const r of rows) {
    const box = el("div");
    box.className = "hist";
    box.append(
      el("strong", "#" + r.id + " · " + r.profile),
      el(
        "p",
        new Date(r.started).toLocaleString() + " · " + (r.port || "没连板子"),
      ),
    );
    const downloads = el("div");
    downloads.className = "report-downloads";
    for (const f of ["txt", "json", "csv"]) {
      const a = el("a", { txt: "TXT 报告", json: "JSON 完整数据", csv: "表格 CSV" }[f] + " ↓");
      a.href = "/api/report/" + r.id + "/" + f;
      downloads.append(a);
    }
    box.append(downloads);
    for (const f of r.backups || []) {
      const a = el("a", "闪存备份文件 ↓");
      a.href = "/api/backup/" + f.id;
      box.append(a, el("p", "SHA-256 " + f.sha256));
    }
    const actions = el("div");
    actions.className = "row";
    actions.append(
      button("分享故障记录", () => previewPack(r.id)),
      button("回看", () => {
        $("dialog").close();
        openReplay(r.id);
      }),
      button("AI 帮我看", () => {
        $("dialog").close();
        openAnalysis(r.id);
      }),
    );
    box.append(actions);
    body.append(box);
  }
});
const tabButtons = [...document.querySelectorAll(".rail [data-tab]")];
for (const b of tabButtons)
  b.onclick = () => {
    document.body.classList.toggle("ai-view", ["analysis", "replay", "overview", "screen"].includes(b.dataset.tab));
    if (b.dataset.tab !== "monitor" && selectedPart) {
      clearPart();
      logs(true);
    }
    $("crumb").textContent = b.textContent.trim();
    for (const n of tabButtons) {
      if (n === b) n.setAttribute("aria-current", "page");
      else n.removeAttribute("aria-current");
      n.classList.toggle("active", n === b);
    }
    if ($("moreTools").contains(b)) $("moreTools").open = true;
    document
      .querySelectorAll(".tab")
      .forEach((n) => (n.hidden = n.id !== b.dataset.tab));
    if (state) {
      renderCounts();
      plots();
      if (b.dataset.tab === "hardware") updateSmart();
      if (b.dataset.tab === "overview") renderOverview(state);
      if (b.dataset.tab === "signal") renderMotion(state);
      if (b.dataset.tab === "flash") renderFlash(true);
    }
  };
for (const b of document.querySelectorAll("[data-go]"))
  b.onclick = () => {
    if (b.dataset.go === "report") return $("history").click();
    document.querySelector('.rail [data-tab="' + b.dataset.go + '"]').click();
    if (b.dataset.errors) {
      $("level").value = "ERROR";
      logs(true);
    }
    if (b.dataset.focus) $(b.dataset.focus).focus();
  };
$("workdate").textContent = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "short",
  day: "numeric",
  weekday: "short",
}).format(new Date());
const storedTheme = localStorage.getItem("boardbench.theme");
document.documentElement.dataset.theme = storedTheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
bind("theme", () => {
  const theme =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("boardbench.theme", theme);
});
document.addEventListener("DOMContentLoaded", async () => {
  try {
    await setup();
    await ports();
    await refresh();
  } catch (e) {
    notice(e.message);
  }
  setInterval(async () => {
    if (busy) return;
    busy = true;
    try {
      if (!token) await setup();
      await refresh();
    } catch {
      $("badge").textContent = "软件后台没响应";
      renderMotion(null);
      renderOverview(null);
      if (!$("hardware").hidden) smartUnavailable("软件后台没响应，恢复后会接着检查。");
    } finally {
      busy = false;
    }
  }, 900);
});
