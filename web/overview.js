"use strict";

const renderOverview = (() => {
  const panel = $("overview");
  panel.innerHTML = `
    <div class="ov-bar">
      <div class="ov-tabs" role="group" aria-label="监控视图"><button id="ovBoardTab" aria-pressed="true">板子总览</button><button id="ovPowerTab" aria-pressed="false">电源</button><button id="ovRuntimeTab" aria-pressed="false">运行情况</button></div>
      <div class="ov-actions"><span id="ovStatus" class="ov-status"></span><button id="ovConnect">去连接板子</button></div>
    </div>
    <div id="ovBoard">
      <section class="ov-card ov-identity" aria-labelledby="ovIdentityLabel">
        <h3 id="ovIdentityLabel">实际板型</h3>
        <div role="status"><strong id="ovModel"></strong><p id="ovModelReason"></p></div>
        <button type="button" id="ovModelLog" class="text-link" hidden></button>
      </section>
      <div id="ovStats" class="ov-grid"></div>
      <div class="ov-details">
        <section class="ov-card"><h3>清单里有多少部件报告了</h3><div id="ovCoverage" class="ov-ring"><strong></strong></div><dl id="ovBoardInfo" class="ov-list"></dl></section>
        <section class="ov-card"><div class="ov-head"><h3>部件状态</h3><select id="ovFilter" aria-label="只看哪些部件"><option value="all">全部部件</option><option value="reported">有报告的</option><option value="issue">有问题的</option></select></div><div id="ovParts" class="ov-parts"></div></section>
      </div>
    </div>
    <div id="ovRuntime" hidden>
      <div id="ovRuntimeStats" class="ov-grid"></div>
      <div class="ov-details">
        <section class="ov-card"><h3>板子报告的数字</h3><dl id="ovRuntimeInfo" class="ov-list"></dl></section>
        <section class="ov-card"><div class="ov-head"><h3>变化曲线</h3>
          <select id="ovRuntimeMetric" aria-label="看哪条曲线"><option value="io">收发速度 · 字节/秒</option><option value="sram">剩余内存 SRAM · KiB</option><option value="rssi">Wi-Fi 信号 · dBm（越接近 0 越好）</option></select>
          </div><div id="ovRuntimeLegend" class="ov-legend"></div><canvas id="ovRuntimeTrend" role="img" aria-label="最近两分钟的变化曲线"></canvas><p>显示最近两分钟。内存、信号需要配套的诊断固件才有。</p></section>
      </div>
    </div>
    <div id="ovPower" hidden>
      <div id="ovPowerCards" class="ov-grid"></div>
      <div class="ov-details">
        <section class="ov-card"><h3>电池状态</h3><div id="ovSoc" class="ov-ring"><strong></strong></div><dl id="ovBatteryInfo" class="ov-list"></dl></section>
        <section class="ov-card"><div class="ov-head"><h3>变化曲线</h3><div class="ov-actions">
          <select id="ovUnit" aria-label="曲线测量项"><option value="v">电压 V</option><option value="a">电流 A</option><option value="w">功率 W</option></select>
          <select id="ovSpan" aria-label="曲线时间范围"><option value="2">最近 2 分钟</option><option value="5">最近 5 分钟</option><option value="15">最近 15 分钟</option></select>
          <a id="ovCsv" class="button">导出表格</a>
        </div></div><div id="ovLegend" class="ov-legend"></div><canvas id="ovTrend" role="img" aria-label="按电脑接收时间排列的电源趋势"></canvas><p id="ovTrendNote"></p></section>
      </div>
      <section id="ovCellsBox" class="ov-card"><div class="ov-head"><h3>单体电压分布 <small>V</small></h3><small id="ovCellDelta"></small></div><div id="ovCells" class="ov-cells"></div></section>
      <details class="ov-format"><summary>电源设备要怎么报告数据（给写固件的人看）</summary><p>电源页需要板子或网关每行输出一个 JSON，有哪些读数就填哪些字段。格式示例：</p>
        <pre>{"bb":1,"part":"power","status":"sample","value":{"battery_v":12.42,"battery_a":0.74,"pv_v":14.29,"load_w":0.6,"soc_pct":98,"cells_v":[4.14,4.14,4.14]}}</pre></details>
    </div>`;
  let live = null, mode = "board", connection = "", points = [], loaded = 0;
  let pending = null, boardKey = "", readError = false;
  const faults = new Set(["设备报错", "固件报错", "无应答"]);
  const idx = id => $("ov" + id);
  const measure = (v, unit, digits = 2) => Number.isFinite(v)
    ? v.toLocaleString("zh-CN", { maximumFractionDigits: digits }) + (unit ? " " + unit : "") : "—";
  function list(id, entries) {
    idx(id).replaceChildren(...entries.map(([name, value]) => {
      const row = el("div"); row.append(el("dt", name), el("dd", value)); return row;
    }));
  }
  function ring(id, pct, label) {
    idx(id).style.setProperty("--fill", (pct ?? 0) + "%");
    idx(id).firstElementChild.textContent = label;
  }
  function metrics(container, items) {
    idx(container).replaceChildren(...items.map(([name, value]) => {
      const card = el("div"); card.className = "ov-card";
      const number = el("div", value); number.className = "ov-number";
      card.append(el("small", name), number);
      return card;
    }));
  }
  function identity() {
    const fields = live?.connected ? live.device_identity || {} : {};
    const model = fields.board;
    let reason;
    if (!live) reason = "暂时无法读取连接状态。";
    else if (!live.connected) {
      reason = !live.auto_usb ? "USB 自动连接已关闭，可在「板子状态」开启或手动连接。" :
        live.job ? "设备任务进行中，等待串口可用。" : live.usb_message || "等待 USB 串口。";
    } else if (model) reason = [model.source, fields.chip && "主控 " + fields.chip.value].filter(Boolean).join(" · ");
    else if (fields.chip) reason = "已识别芯片 " + fields.chip.value + "，当前日志尚未报告整板型号。";
    else if (fields.bridge) reason = "已识别 USB 转串口 " + fields.bridge.value + "，尚无整板型号证据。";
    else if (!live.connection_rx) reason = "已连接 " + live.port + "，本次连接尚未收到数据。";
    else reason = "已收到设备数据，尚未读到可识别的板型声明。";
    const title = model?.value || (live?.connected ? "未知" : live ? "未连接" : "暂不可用");
    if (idx("Model").textContent !== title) idx("Model").textContent = title;
    if (idx("ModelReason").textContent !== reason) idx("ModelReason").textContent = reason;
    const evidence = model || fields.chip || fields.bridge;
    const link = idx("ModelLog");
    link.hidden = !evidence?.log_id;
    if (!link.hidden) {
      const run = live.run, ref = "L" + evidence.log_id;
      link.textContent = "查看原始日志 " + ref;
      link.onclick = handle(() => showEvidence({run}, ref));
    }
  }
  function board() {
    identity();
    const rows = live?.components || [], connected = !!live?.connected;
    const filter = idx("Filter").value;
    const key = JSON.stringify([connection, connected, rows, filter, live?.profile, live?.rx, live?.tx]);
    if (key === boardKey) return;
    boardKey = key;
    const seen = rows.filter(r => r.log_id);
    const errors = rows.filter(r => faults.has(r.status)), expired = rows.filter(r => r.status === "上报超时");
    const stats = [["有报告的部件", live ? seen.length : "—"], ["报错的部件", connected ? errors.length : "—"], ["很久没报的", connected ? expired.length : "—"]];
    metrics("Stats", stats);
    const expected = rows.filter(r => r.expected);
    const reported = expected.filter(r => r.log_id).length;
    const pct = expected.length ? Math.round(100 * reported / expected.length) : null;
    ring("Coverage", pct, pct === null ? "—" : pct + "%");
    list("BoardInfo", [["已报告 / 清单里的部件", expected.length ? reported + " / " + expected.length : "—"], ["测试清单", live?.profile || "—"],
      ["接收 / 发送", live ? live.rx.toLocaleString() + " / " + live.tx.toLocaleString() + " B" : "—"]]);
    const shown = rows.filter(r => filter === "all" || (filter === "reported" ? r.log_id : faults.has(r.status) || r.status === "上报超时"));
    const area = idx("Parts");
    const visible = new Set(shown.map(r => r.part));
    for (const child of [...area.children]) if (!visible.has(child.dataset.part)) child.remove();
    for (const r of shown) {
      let item = area.querySelector('[data-part="' + r.part + '"]');
      if (!item) {
        item = el("button"); item.dataset.part = r.part;
        item.append(el("strong", r.name), el("span"), el("small"));
        item.onclick = () => {
          document.querySelector('[data-tab="monitor"]').click();
          componentNodes.get(r.part)?.b.click();
        };
        area.append(item);
      }
      item.children[0].textContent = r.model ? r.name + " · " + r.model : r.name;
      item.disabled = !r.log_id;
      item.children[1].textContent = plainStatus(r.status);
      item.children[2].textContent = r.value || "—";
      item.children[1].className = "part-state";
      item.children[1].dataset.tone = faults.has(r.status) ? "fail" : r.status === "上报超时" ? "warn" : "neutral";
    }
    if (!shown.length) area.append(el("p", live ? "没有符合条件的部件。" : "正在启动…"));
  }
  function card(rail) {
    const box = el("section"); box.className = "ov-card";
    const head = el("div"); head.className = "ov-rail-title";
    const label = el("h3", rail.name); label.className = "ov-dot"; label.style.setProperty("--series", "var(--" + rail.color + ")");
    const number = el("div"); number.className = "ov-number";
    const other = el("small"), spark = el("canvas"); spark.className = "ov-spark"; spark.setAttribute("aria-hidden","true");
    head.append(label); box.append(head, number, other, spark); idx("PowerCards").append(box);
    return { number, other, spark };
  }
  const cards = energyRails.map(card);
  function graph(canvas, series, from, to, axes, data = points, gapMs = 90000) {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    const scale = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(w * scale); canvas.height = Math.round(h * scale);
    const g = canvas.getContext("2d"); g.scale(scale, scale);
    const style = getComputedStyle(panel);
    const window = data.filter(p => p.time >= from && p.time <= to);
    const values = series.flatMap(s => window.map(p => p.values[s.key]).filter(Number.isFinite));
    if (!values.length) {
      if (axes) {
        g.fillStyle = style.getPropertyValue("--sub"); g.font = "13px sans-serif";
        g.textAlign = "center"; g.fillText("暂无趋势数据",w/2,h/2);
      }
      return;
    }
    if (window.length > 1 && window.at(-1).time > window[0].time) {
      from = window[0].time; to = window.at(-1).time;
    }
    let low = Math.min(...values), high = Math.max(...values);
    if (low === high) { const pad = Math.abs(low) * .02 || .1; low -= pad; high += pad; }
    const left = axes ? 54 : 3, right = w - 4, top = axes ? 10 : 3, bottom = h - (axes ? 19 : 3);
    const x = t => left + (t - from) / Math.max(1,to - from) * (right-left);
    const y = v => bottom - (v-low)/(high-low)*(bottom-top);
    if (axes) {
      g.font = "11px sans-serif";
      for (let i=0; i<3; i++) {
        const value = low + (high-low)*i/2;
        g.strokeStyle = style.getPropertyValue("--line"); g.beginPath(); g.moveTo(left,y(value)); g.lineTo(right,y(value)); g.stroke();
        g.fillStyle = style.getPropertyValue("--sub"); g.fillText(measure(value,""),0,y(value)+3,49);
      }
      g.fillText(fmtTime(from).slice(0,to-from < 1000 ? 12 : 8),left,h-2);
      g.textAlign="right";g.fillText(fmtTime(to).slice(0,to-from < 1000 ? 12 : 8),right,h-2);
    }
    for (const s of series) {
      g.strokeStyle = g.fillStyle = style.getPropertyValue("--"+s.color);
      g.lineWidth = 2; g.beginPath();
      let prev = null;
      for (const p of window) {
        if (p.time < from || p.time > to) continue;
        const v = p.values[s.key];
        if (!Number.isFinite(v)) { prev=null; continue; }
        if (prev === null || p.time - prev > gapMs || p.time < prev) {g.moveTo(x(p.time),y(v));g.fillRect(x(p.time)-1,y(v)-1,2,2);}
        else g.lineTo(x(p.time),y(v));
        prev = p.time;
      }
      g.stroke();
    }
  }
  const runtime = createRuntimeMonitor();
  function drawRuntime() {
    const data = runtime.samples(), v = data.at(-1)?.values || {};
    metrics("RuntimeStats", [["收到的速度",measure(v.rx,"字节/秒",1)],["发出的速度",measure(v.tx,"字节/秒",1)],["Wi-Fi 信号",measure(v.rssi,"dBm",0)]]);
    const seconds = Number.isFinite(v.uptime) ? Math.floor(v.uptime/1000) : null;
    list("RuntimeInfo",[["芯片速度（主频）",measure(v.cpu,"MHz")],["板子开机多久了",seconds===null ? "—" : Math.floor(seconds/3600)+" 时 "+Math.floor(seconds%3600/60)+" 分 "+seconds%60+" 秒"],
      ["剩余内存（SRAM）",measure(v.sram,"KiB",1)],["剩余扩展内存（PSRAM）",measure(v.psram,"KiB",1)],["扩展内存总量（PSRAM）",measure(v.totalPsram,"KiB",1)]]);
    const metric = idx("RuntimeMetric").value;
    const series = metric === "io" ? [{key:"rx",name:"接收",color:"battery"},{key:"tx",name:"发送",color:"load"}] :
      [{key:metric,name:metric==="sram"?"剩余内存（SRAM）":"Wi-Fi 信号",color:"battery"}];
    idx("RuntimeLegend").replaceChildren(...series.map(s => {
      const n=el("span",s.name);n.className="ov-dot";n.style.setProperty("--series","var(--"+s.color+")");return n;
    }));
    const now=Date.now();
    graph(idx("RuntimeTrend"),series,now-120000,now,true,data);
  }
  function power() {
    const row = live?.components.find(r => r.part === "power");
    const last = points.at(-1);
    if (live) idx("Csv").href="/api/report/"+live.run+"/csv";
    else idx("Csv").removeAttribute("href");
    const current = live?.connected && row?.status === "有读数" && !readError && last && last.id >= row.log_id ? last : null;
    const gapMs = row?.timeout === null ? Infinity : (row?.timeout ?? 90) * 1000;
    const v = current?.values || {};
    const now = Date.now(), from = now - Number(idx("Span").value)*60000, unit = idx("Unit").value;
    const series = energyRails.map(r => ({...r, key:r.key+"_"+unit}));
    for (const [i,r] of energyRails.entries()) {
      const c = cards[i], k = r.key;
      c.number.replaceChildren(document.createTextNode(measure(v[k+"_v"],"")),el("small","V"));
      c.other.textContent = "电流 " + measure(v[k+"_a"],"A") + "　功率 " + measure(v[k+"_w"],"W");
      graph(c.spark,[{key:k+"_v",color:r.color}],from,now,false,points,gapMs);
    }
    ring("Soc",v.soc_pct,measure(v.soc_pct,"%"));
    list("BatteryInfo",[["剩余电量",measure(v.soc_pct,"%")],["健康状态",measure(v.soh_pct,"%")],["电池温度",measure(v.battery_c,"°C")],
      ["控制器温度",measure(v.controller_c,"°C")],["设备告警",current?.alarm || "—"]]);
    idx("Legend").replaceChildren(...series.map(r => {
      const n = el("span",r.name+" "+measure(v[r.key],energyUnits[unit]));n.className="ov-dot";n.style.setProperty("--series","var(--"+r.color+")");return n;
    }));
    graph(idx("Trend"),series,from,now,true,points,gapMs);
    const count = points.filter(p => p.time >= from && p.time <= now && series.some(s => Number.isFinite(p.values[s.key]))).length;
    const note = readError ? "读取电源记录失败，正在重试。" : !live?.connected ? "等板子连上。" :
      !row ? "还没收到电源数据（需要板子或网关按下面的格式报告）。" : row.status !== "有读数" ? "电源数据 · " + plainStatus(row.status) :
      !last || last.id < row.log_id ? "正在读取电源记录…" : "最近一次 L" + last.id + " · " + fmtTime(last.time);
    idx("TrendNote").textContent = note + (count ? "　这段时间有 " + count + " 个读数" : "　这段时间没有这一项的读数。");
    const cells = current?.cells || [], nums = cells.filter(Number.isFinite);
    const max = Math.max(0,...nums);
    idx("CellDelta").textContent = cells.length ? cells.length+" 节 · 最高和最低差 "+(nums.length === cells.length ? measure((max-Math.min(...nums))*1000,"mV",1) : "等报全") : "";
    idx("Cells").replaceChildren(...cells.map((cv,i) => {
      const n = el("div"); n.className="ov-cell";const bar=el("i");
      bar.style.height=(Number.isFinite(cv) && max ? cv/max*62 : 0)+"px";
      n.append(el("span",measure(cv,"",3)),bar,el("small","#"+(i+1)));return n;
    }));
    if (!cells.length) idx("Cells").append(el("p","还没有每节电池的电压。"));
  }
  async function loadPower(row) {
    if (!row?.log_id || row.log_id <= loaded || pending) return;
    const controller = new AbortController(); pending = controller;
    const run = live.run;
    try {
      const records = await get("/api/components/"+run+"/power",{signal:controller.signal});
      if (pending !== controller) return;
      points = records.filter(r => r.id >= row.first_log_id).reverse().map(parseEnergy);
      loaded = points.at(-1)?.id || 0;
      readError = false;
    } catch { if (!controller.signal.aborted) readError = true; }
    finally { if (pending === controller) {pending=null;if (!panel.hidden && mode==="power") power();} }
  }
  function draw() {
    if (panel.hidden) return;
    const label = !live ? "软件后台没响应" : live.connected ? "已连上板子" : "还没连上板子";
    idx("Status").textContent = live ? "记录 #"+live.run+" · "+label : label;
    if (mode==="board") board();
    else if (mode==="runtime") drawRuntime();
    else {power(); if (live?.connected) loadPower(live.components.find(r=>r.part==="power"));}
  }
  function select(next) {
    mode=next;
    for (const [id,k] of [["Board","board"],["Power","power"],["Runtime","runtime"]]) {
      idx(id).hidden=next!==k;idx(id+"Tab").setAttribute("aria-pressed", String(next===k));
    }
    draw();
  }
  idx("Connect").onclick=()=>{document.querySelector('[data-tab="monitor"]').click();$("transport").scrollIntoView({block:"center"});$("transport").focus();};
  idx("BoardTab").onclick=()=>select("board");idx("PowerTab").onclick=()=>select("power");
  idx("RuntimeTab").onclick=()=>select("runtime"); idx("RuntimeMetric").onchange=draw;
  idx("Filter").onchange=draw;idx("Unit").onchange=draw;idx("Span").onchange=draw;
  new ResizeObserver(()=>{if (!panel.hidden && mode==="power") power();}).observe(idx("Trend"));
  return next => {
    live=next; runtime.update(next);
    const k=live ? live.run+"/"+live.connection_id : "";
    if (connection!==k) {
      pending?.abort();pending=null;connection=k;points=[];loaded=0;readError=false;boardKey="";
    }
    draw();
  };
})();
