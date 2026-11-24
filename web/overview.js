"use strict";

const renderOverview = (() => {
  const panel = $("overview");
  panel.innerHTML = `
    <div class="ov-bar">
      <div class="ov-tabs" role="group" aria-label="监控视图"><button id="ovBoardTab" aria-pressed="true">板卡总览</button><button id="ovPowerTab" aria-pressed="false">能源监控</button><button id="ovRuntimeTab" aria-pressed="false">运行监控</button></div>
      <div class="ov-actions"><span id="ovStatus" class="ov-status"></span><button id="ovConnect">连接设置</button></div>
    </div>
    <div id="ovBoard">
      <div id="ovStats" class="ov-grid"></div>
      <div class="ov-details">
        <section class="ov-card"><h3>元件上报覆盖</h3><div id="ovCoverage" class="ov-ring"><strong></strong></div><dl id="ovBoardInfo" class="ov-list"></dl></section>
        <section class="ov-card"><div class="ov-head"><h3>元件状态</h3><select id="ovFilter" aria-label="筛选元件"><option value="all">全部元件</option><option value="reported">已上报</option><option value="issue">异常与超时</option></select></div><div id="ovParts" class="ov-parts"></div></section>
      </div>
    </div>
    <div id="ovRuntime" hidden>
      <div id="ovRuntimeStats" class="ov-grid"></div>
      <div class="ov-details">
        <section class="ov-card"><h3>板端读数</h3><dl id="ovRuntimeInfo" class="ov-list"></dl></section>
        <section class="ov-card"><div class="ov-head"><h3>运行趋势</h3>
          <select id="ovRuntimeMetric" aria-label="运行趋势测量项"><option value="io">通信速率 · B/s</option><option value="sram">可用 SRAM · KiB</option><option value="rssi">Wi-Fi 信号 · dBm</option></select>
          </div><div id="ovRuntimeLegend" class="ov-legend"></div><canvas id="ovRuntimeTrend" role="img" aria-label="近期运行监控趋势"></canvas><p>按面板采样时间显示最近两分钟。</p></section>
      </div>
    </div>
    <div id="ovPower" hidden>
      <div id="ovPowerCards" class="ov-grid"></div>
      <div class="ov-details">
        <section class="ov-card"><h3>电池状态</h3><div id="ovSoc" class="ov-ring"><strong></strong></div><dl id="ovBatteryInfo" class="ov-list"></dl></section>
        <section class="ov-card"><div class="ov-head"><h3>运行曲线</h3><div class="ov-actions">
          <select id="ovUnit" aria-label="曲线测量项"><option value="v">电压 V</option><option value="a">电流 A</option><option value="w">功率 W</option></select>
          <select id="ovSpan" aria-label="曲线时间范围"><option value="2">最近 2 分钟</option><option value="5">最近 5 分钟</option><option value="15">最近 15 分钟</option></select>
          <a id="ovCsv" class="button">导出会话 CSV</a>
        </div></div><div id="ovLegend" class="ov-legend"></div><canvas id="ovTrend" role="img" aria-label="按电脑接收时间排列的电源趋势"></canvas><p id="ovTrendNote"></p></section>
      </div>
      <section id="ovCellsBox" class="ov-card"><div class="ov-head"><h3>单体电压分布 <small>V</small></h3><small id="ovCellDelta"></small></div><div id="ovCells" class="ov-cells"></div></section>
      <details class="ov-format"><summary>能源设备如何上报数据</summary><p>通过已连接的串口、TCP 或 UDP，每行发送一个 JSON 快照。传感器或网关提供哪些读数，就填写哪些字段。以下为格式示例。</p>
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
  function board() {
    const rows = live?.components || [], connected = !!live?.connected;
    const filter = idx("Filter").value;
    const key = JSON.stringify([connection, connected, rows, filter, live?.rx, live?.tx]);
    if (key === boardKey) return;
    boardKey = key;
    const seen = rows.filter(r => r.log_id);
    const errors = rows.filter(r => faults.has(r.status)), expired = rows.filter(r => r.status === "上报超时");
    const stats = [["已上报元件", live ? seen.length : "—"], ["设备异常", connected ? errors.length : "—"], ["上报超时", connected ? expired.length : "—"]];
    metrics("Stats", stats);
    const pct = rows.length ? Math.round(100 * seen.length / rows.length) : null;
    ring("Coverage", pct, pct === null ? "—" : pct + "%");
    list("BoardInfo", [["有记录 / 总数", live ? seen.length + " / " + rows.length : "—"], ["板型", live?.profile || "—"],
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
      item.disabled = !r.log_id;
      item.children[1].textContent = r.status;
      item.children[2].textContent = r.value || "—";
      item.children[1].className = "part-state";
      item.children[1].dataset.tone = faults.has(r.status) ? "fail" : r.status === "上报超时" ? "warn" : "neutral";
    }
    if (!shown.length) area.append(el("p", live ? "当前筛选没有元件。" : "等待本地服务。"));
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
  function graph(canvas, series, from, to, axes, data = points) {
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
        if (prev === null || p.time - prev > 90000 || p.time < prev) {g.moveTo(x(p.time),y(v));g.fillRect(x(p.time)-1,y(v)-1,2,2);}
        else g.lineTo(x(p.time),y(v));
        prev = p.time;
      }
      g.stroke();
    }
  }
  const runtime = createRuntimeMonitor();
  function drawRuntime() {
    const data = runtime.samples(), v = data.at(-1)?.values || {};
    metrics("RuntimeStats", [["接收速率",measure(v.rx,"B/s",1)],["发送速率",measure(v.tx,"B/s",1)],["Wi-Fi 信号",measure(v.rssi,"dBm",0)]]);
    const seconds = Number.isFinite(v.uptime) ? Math.floor(v.uptime/1000) : null;
    list("RuntimeInfo",[["CPU 主频",measure(v.cpu,"MHz")],["板端运行时长",seconds===null ? "—" : Math.floor(seconds/3600)+" 时 "+Math.floor(seconds%3600/60)+" 分 "+seconds%60+" 秒"],
      ["可用片内 SRAM",measure(v.sram,"KiB",1)],["可用 PSRAM",measure(v.psram,"KiB",1)],["PSRAM 总量",measure(v.totalPsram,"KiB",1)]]);
    const metric = idx("RuntimeMetric").value;
    const series = metric === "io" ? [{key:"rx",name:"接收",color:"battery"},{key:"tx",name:"发送",color:"load"}] :
      [{key:metric,name:metric==="sram"?"可用片内 SRAM":"Wi-Fi RSSI",color:"battery"}];
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
    const v = current?.values || {};
    const now = Date.now(), from = now - Number(idx("Span").value)*60000, unit = idx("Unit").value;
    const series = energyRails.map(r => ({...r, key:r.key+"_"+unit}));
    for (const [i,r] of energyRails.entries()) {
      const c = cards[i], k = r.key;
      c.number.replaceChildren(document.createTextNode(measure(v[k+"_v"],"")),el("small","V"));
      c.other.textContent = "电流 " + measure(v[k+"_a"],"A") + "　功率 " + measure(v[k+"_w"],"W");
      graph(c.spark,[{key:k+"_v",color:r.color}],from,now,false);
    }
    ring("Soc",v.soc_pct,measure(v.soc_pct,"%"));
    list("BatteryInfo",[["剩余电量",measure(v.soc_pct,"%")],["健康状态",measure(v.soh_pct,"%")],["电池温度",measure(v.battery_c,"°C")],
      ["控制器温度",measure(v.controller_c,"°C")],["设备告警",current?.alarm || "—"]]);
    idx("Legend").replaceChildren(...series.map(r => {
      const n = el("span",r.name+" "+measure(v[r.key],energyUnits[unit]));n.className="ov-dot";n.style.setProperty("--series","var(--"+r.color+")");return n;
    }));
    graph(idx("Trend"),series,from,now,true);
    const count = points.filter(p => p.time >= from && p.time <= now && series.some(s => Number.isFinite(p.values[s.key]))).length;
    const note = readError ? "读取本地能源日志失败，正在重试。" : !live?.connected ? "等待设备连接。" :
      !row ? "等待 power 遥测帧。可通过串口、TCP 或 UDP 上报。" : row.status !== "有读数" ? "能源遥测 · " + row.status :
      !last || last.id < row.log_id ? "正在读取能源日志…" : "最近一帧 L" + last.id + " · " + fmtTime(last.time);
    idx("TrendNote").textContent = note + (count ? "　窗口内 " + count + " 个有效采样点" : "　所选时段暂无此测量项。");
    const cells = current?.cells || [], nums = cells.filter(Number.isFinite);
    const max = Math.max(0,...nums);
    idx("CellDelta").textContent = cells.length ? cells.length+" 串 · 压差 "+(nums.length === cells.length ? measure((max-Math.min(...nums))*1000,"mV",1) : "待完整上报") : "";
    idx("Cells").replaceChildren(...cells.map((cv,i) => {
      const n = el("div"); n.className="ov-cell";const bar=el("i");
      bar.style.height=(Number.isFinite(cv) && max ? cv/max*62 : 0)+"px";
      n.append(el("span",measure(cv,"",3)),bar,el("small","#"+(i+1)));return n;
    }));
    if (!cells.length) idx("Cells").append(el("p","暂无单体电压读数。"));
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
    const label = !live ? "本地服务离线" : live.connected ? live.transport+" 已连接" : "设备未连接";
    idx("Status").textContent = live ? "会话 #"+live.run+" · "+label : label;
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
