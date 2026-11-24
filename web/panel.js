"use strict";
const $ = id => document.getElementById(id);
const el = (tag, text) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  return n;
};
let token, state, paused = false, timer, testKey = "", logKey = "", busy = false;
function notice(message) {
  $("feedback").textContent = message;
  $("feedback").className = "show";
  clearTimeout(timer);
  timer = setTimeout(() => $("feedback").className = "", 5000);
}
async function get(path, init) {
  const r = await fetch(path, init);
  if (!r.ok) {
    const text = await r.text();
    let message = text;
    try { message = JSON.parse(text).error || text; } catch {}
    throw Error(message);
  }
  return r.json();
}
async function act(op, data = {}) {
  await get("/api/" + op, {method:"POST",headers:{"Content-Type":"application/json","X-BoardBench":token},
    body:JSON.stringify({...data,run:state.run})});
  await refresh();
}
function handle(fn) {
  return async () => { try { await fn(); } catch (e) { notice(e.message); } };
}
function bind(id, fn) { $(id).onclick = handle(fn); }
function button(title, fn) {
  const b = el("button", title);
  b.onclick = handle(fn);
  return b;
}
const fmtTime = ts => new Date(ts).toLocaleTimeString("zh-CN",{hour12:false,hour:"2-digit",minute:"2-digit",second:"2-digit",fractionalSecondDigits:3});
function modal(title) {
  const body = $("dialogBody");
  body.replaceChildren(el("h2",title));
  if (!$("dialog").open) $("dialog").showModal();
  return body;
}
async function setup() {
  const d = await get("/api/setup");
  token = d.token;
  const selected = $("profile").value;
  $("profile").replaceChildren(...d.profiles.map(x => new Option(x,x)));
  if (selected) $("profile").value = selected;
  if (!$("ip").value) $("ip").value = d.ip;
}
async function ports() {
  const rows = await get("/api/ports");
  const old = $("port").value;
  $("port").replaceChildren(...rows.map(x => new Option(x.path+" · "+x.label,x.path)));
  if (!rows.length) $("port").add(new Option("等待 USB 串口",""));
  if (rows.some(x => x.path===old)) $("port").value=old;
  $("portNote").textContent = rows.length ? "发现 "+rows.length+" 个串口 · 请选择当前设备" : "未发现 USB 串口。插入数据线后点击扫描。";
  if (state) buttons();
}
function buttons() {
  const connected=state.connected, job=Boolean(state.job), port=Boolean($("port").value);
  $("connect").textContent=connected?"断开串口":"连接串口";
  $("connect").disabled=!connected && (!port || job);
  $("port").disabled=$("baud").disabled=$("framing").disabled=connected || job;
  $("probe").disabled=!port || job;
  $("stop").disabled=!job;
  $("all").disabled=!connected || job || !state.tests.some(t=>t.kind==="serial");
  $("send").disabled=!connected || job;
  $("new").disabled=connected || job || Boolean(state.echo);
  $("ending").disabled=$("encoding").value==="HEX";
}
function mark(t) {
  const body=modal(t.name);
  body.append(el("p",t.step));
  const label=el("label","实测现象或跳过原因");
  const input=el("textarea"); input.rows=3; label.append(input);
  const actions=el("div"); actions.className="row";
  for(const status of ["通过","失败","跳过"]) actions.append(button(status,async()=>{
    if (!input.value.trim()) { input.focus(); throw Error("请先填写观察结果。"); }
    await act("mark",{name:t.name,state:status,detail:input.value});
    $("dialog").close();
  }));
  body.append(label,actions);
}
function tests() {
  const key=JSON.stringify([state.run,state.results,state.connected,state.job]);
  if(key===testKey) return;
  testKey=key;
  const open=new Set([...$("tests").querySelectorAll("details[open]")].map(d=>d.dataset.name));
  const results=new Map(state.results.map(r=>[r.name,r]));
  const nodes=state.tests.map(t=>{
    const row=el("details"); row.className="test";row.dataset.name=t.name;row.open=open.has(t.name);
    const summary=el("summary");summary.append(el("span",t.name),el("small",(t.kind==="serial"?"命令":"人工")+" · "+(results.get(t.name)?.state || "未测")));
    row.append(summary,el("p",t.step));
    if(t.kind==="serial") row.append(el("p","TX  "+t.tx+"  →  RX 包含 "+t.expect));
    if(results.has(t.name)) row.append(el("p",results.get(t.name).detail));
    const b=button(t.kind==="serial"?"执行检测":"记录观察",()=>t.kind==="serial"?act("cases",{name:t.name}):mark(t));
    b.disabled=t.kind==="serial" && (!state.connected || !!state.job);
    row.append(b); return row;
  });
  for (const r of state.results.filter(r=>!state.tests.some(t=>t.name===r.name))) {
    const p=el("p",r.name+" · "+r.state+" · "+r.detail);
    nodes.unshift(p);
  }
  $("tests").replaceChildren(...nodes);
  $("suite").textContent=state.profile+" · "+state.tests.length+" 项";
}
function logs(force=false) {
  if (paused && !force) return;
  const query=$("search").value.toLowerCase(), level=$("level").value, hex=$("hex").checked;
  const key=[state.run,state.logs.at(-1)?.id,query,level,hex].join("/");
  if(key===logKey && !force)return;
  logKey=key;
  const lines=state.logs.filter(r=>(!level||r.level===level)&&[r.message,r.source,r.at,fmtTime(r.at),r.raw].some(s=>s.toLowerCase().includes(query)));
  $("stream").replaceChildren(...lines.map(r=>{
    const line=el("div");line.className="line";
    const time=el("time",fmtTime(r.at));time.title=r.at;
    const tag=el("span",r.level);tag.className="tag "+r.level;
    const msg=el("span",r.source+" │ "+(hex&&r.raw?r.raw:r.message));msg.className="msg";
    line.append(time,tag,msg);return line;
  }));
  if(!lines.length)$("stream").append(el("p","没有匹配的事件"));
  $("stream").scrollTop=$("stream").scrollHeight;
  $("logCount").textContent="显示 "+lines.length+" 条 · 完整记录可导出";
}
function numeric(line) {
  try {
    const v=JSON.parse(line);
    if(v && typeof v==="object") return Object.entries(v).filter(([,x])=>Number.isFinite(x));
  } catch {}
  const m=[...line.matchAll(/([\w.\-]+)\s*=\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)/gi)];
  if(m.length)return m.map(x=>[x[1],Number(x[2])]).filter(([,x])=>Number.isFinite(x));
  const cells=line.trim().split(",");
  if(cells.length && cells.every(v=>v.trim()!=="" && Number.isFinite(Number(v))))return cells.map((x,i)=>["CH"+(i+1),Number(x)]);
  return [];
}
function plots() {
  if($("signal").hidden)return;
  const channels=new Map();
  for(const r of state.logs.filter(r=>r.level==="RX")) for(const [k,v] of numeric(r.message)) {
    if(!channels.has(k) && channels.size<16)channels.set(k,[]);
    channels.get(k)?.push(v);
  }
  $("charts").replaceChildren();
  if(!channels.size)$("charts").append(el("p",'等待串口数值，例如 {"x":1.2,"y":3.4}'));
  for(const [k,vs] of channels) {
    const box=el("div"); box.className="plot";
    const low=Math.min(...vs), high=Math.max(...vs);
    box.append(el("strong",k+"  "+vs.at(-1)+"    ["+low+" … "+high+"]"));
    const c=el("canvas");c.width=700;c.height=100;c.setAttribute("aria-label",k+" 趋势");
    box.append(c);$("charts").append(box);
    const ctx=c.getContext("2d");ctx.strokeStyle="#277145";ctx.lineWidth=2;ctx.beginPath();
    vs.forEach((v,i)=>{const x=4+i*690/Math.max(1,vs.length-1), y=high===low?50:95-(v-low)*90/(high-low);if(i)ctx.lineTo(x,y);else{ctx.moveTo(x,y);ctx.fillRect(x,y,2,2);}});
    ctx.stroke();
  }
}
async function refresh() {
  const next=await get("/api/state"),changed=state?.run!==next.run;
  state=next;
  if(changed){$("profile").value=state.profile;paused=false;$("pause").textContent="暂停滚动";}
  $("badge").textContent=state.connected?"● 串口在线":"○ 本地服务在线";
  $("run").textContent="#"+state.run;
  $("counts").textContent=["通过","失败"].map(s=>state.results.filter(r=>r.state===s).length).join(" / ");
  $("bytes").textContent=state.rx.toLocaleString()+" / "+state.tx.toLocaleString();
  $("job").textContent=state.job || "空闲";
  $("echo").textContent=state.echo?"关闭回声服务":"开启回声服务";
  $("ota").value=state.echo;$("echoInfo").textContent=state.echo?"正在监听 · 收到 "+state.frames+" 个音频帧":"服务已关闭";
  $("logCSV").href="/api/report/"+state.run+"/csv";
  $("template").href="/api/template?name="+encodeURIComponent($("profile").value);
  buttons();tests();logs();plots();
}
bind("scan",ports);
bind("connect",()=>state.connected?act("disconnect"):act("connect",{port:$("port").value,baud:$("baud").value,framing:$("framing").value}));
bind("new",()=>act("new",{profile:$("profile").value}));
bind("probe",()=>{
  const body=modal("读取 ESP 芯片与 Flash");
  body.append(el("p","将占用 "+$("port").value+" 并复位设备进入 ROM 下载模式。读取完成后设备重新启动，串口监视可手动重新连接。"),
    button("复位并读取",async()=>{await act("probe",{port:$("port").value});$("dialog").close();}));
});
bind("all",()=>act("cases",{all:true}));bind("stop",()=>act("stop"));
bind("send",()=>act("send",{text:$("sendText").value,hex:$("encoding").value==="HEX",ending:$("ending").value}));
bind("note",async()=>{await act("note",{text:$("noteText").value});$("noteText").value="";});
bind("echo",()=>act("echo",{enable:!state.echo,host:$("ip").value,port:$("lanPort").value}));
bind("import",()=>$("file").click());
$("file").onchange=handle(async()=>{
  const f=$("file").files[0];if(!f)return;
  try{const data=JSON.parse(await f.text());await act("import",{data});await setup();$("profile").value=data.name.trim();$("profile").onchange();notice("配置已导入，点击新建测试即可使用。");}finally{$("file").value="";}
});
$("profile").onchange=()=>{$("template").href="/api/template?name="+encodeURIComponent($("profile").value);};
$("port").onchange=$("encoding").onchange=()=>buttons();
bind("pause",()=>{paused=!paused;$("pause").textContent=paused?"继续滚动":"暂停滚动";if(!paused)logs(true);});
for(const id of ["search","level","hex"])$(id).oninput=()=>logs(true);
bind("history",async()=>{
  const rows=await get("/api/history");const body=modal("测试历史与报告");
  for(const r of rows){
    const box=el("div");box.className="hist";box.append(el("strong","#"+r.id+" · "+r.profile),el("p",new Date(r.started).toLocaleString()+" · "+(r.port||"未绑定串口")));
    for(const f of ["txt","json","csv"]){const a=el("a",f.toUpperCase()+" ↓");a.href="/api/report/"+r.id+"/"+f;box.append(a);}
    body.append(box);
  }
});
for(const b of $("tabs").querySelectorAll("button")) b.onclick=()=>{
  document.querySelectorAll(".tab").forEach(n=>n.hidden=n.id!==b.dataset.tab);
  $("tabs").querySelectorAll("button").forEach(n=>n.classList.toggle("active",n===b));if(state)plots();
};
(async()=>{
  try{await setup();await ports();await refresh();}catch(e){notice(e.message);}
  setInterval(async()=>{if(busy)return;busy=true;try{if(!token)await setup();await refresh();}catch{$("badge").textContent="本地服务离线";}finally{busy=false;}},900);
})();
