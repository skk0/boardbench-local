"use strict";
let aiTicket = "",
  aiBusy = false,
  aiReady = false,
  requestedRun = null;
const aiNav = document.querySelector('[data-tab="analysis"]');
const aiPost = (op, data = {}) =>
  get("/api/ai/" + op, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-BoardBench": token },
    body: JSON.stringify(data),
  });
function aiMessage(text, error = false) {
  $("aiStatus").textContent = text;
  $("aiStatus").classList.toggle("error", error);
}
function invalidatePreview() {
  aiTicket = "";
  $("aiPayload").hidden = true;
  $("aiPayload").replaceChildren();
}
async function busyAI(fn) {
  if (aiBusy) return;
  aiBusy = true;
  const controls = [
    ...document.querySelectorAll(
      "#analysis button,#analysis input,#analysis select,#analysis textarea",
    ),
  ];
  controls.forEach((n) => (n.disabled = true));
  try {
    await fn();
  } catch (e) {
    aiMessage(e.message, true);
  } finally {
    controls.forEach((n) => (n.disabled = false));
    aiBusy = false;
  }
}
function applyAISettings(s) {
  $("aiBase").value = s.base;
  $("aiModel").value = s.model;
  $("aiKey").value = "";
  $("aiKey").placeholder = s.has_key
    ? "已设置，留空可继续使用"
    : "本地免鉴权服务可留空";
  $("aiRemember").checked = s.remembered;
}
async function saveAI(clear = false) {
  const cfg = await aiPost("settings", {
    base: $("aiBase").value.trim(),
    model: $("aiModel").value.trim(),
    key: $("aiKey").value,
    remember: $("aiRemember").checked,
    clear,
  });
  applyAISettings(cfg);
  invalidatePreview();
  return cfg;
}
async function loadRunResults() {
  const id = $("aiRun").value;
  if (!id) return;
  const records = await get("/api/ai/history/" + id);
  if ($("aiRun").value !== id) return;
  const area = $("aiResults");
  area.replaceChildren();
  if (!records.length)
    area.append(el("p", "这次记录还没让 AI 分析过。"));
  records.forEach((rec) => {
    const article = el("article");
    article.className = "ai-record";
    article.append(
      el("h3", rec.snapshot.question || "测试诊断"),
      el("small", "已收到模型回复 · " + rec.model + " · " + new Date(rec.at).toLocaleString()),
    );
    const events = rec.snapshot.events;
    const legacyAI = events.filter(e => e.source === "操作" && e.text.startsWith("/api/ai/"));
    if (legacyAI.length) {
      const note = el("p", "这份报告包含先前的 AI 接口日志（" +
        legacyAI.map(e => e.ref).join("、") + "）。" +
        (events.every(e => e.source === "会话" || legacyAI.includes(e)) && !rec.snapshot.results.length
          ? "当时没有采集到板卡诊断证据。" : "这些记录描述的是当时的接口调用。"));
      note.className = "ai-context";
      article.append(note);
    }
    const a = rec.answer;
    if (a.warning) article.append(el("p", a.warning));
    for (const f of a.findings) {
      const item = el("div");
      item.className = "finding";
      item.append(el("strong", f.kind), el("span", f.text));
      const refs = el("div");
      refs.className = "ref-list";
      f.refs.forEach((ref) =>
        refs.append(button(ref, () => showEvidence(rec, ref))),
      );
      if (f.missing) refs.append(el("small", "引用缺失或无效，待人工核对。"));
      item.append(refs);
      article.append(item);
    }
    if (a.unverified) article.append(el("pre", a.unverified));
    if (a.next.length) {
      article.append(el("h4", "建议验证"));
      const ol = el("ol");
      a.next.forEach((t) => ol.append(el("li", t)));
      article.append(ol);
    }
    area.append(article);
  });
}
async function showEvidence(rec, ref) {
  const box = modal("原始记录 " + ref + " · 记录 #" + rec.run);
  if (ref.startsWith("L")) {
    const entry = await get("/api/ai/evidence/" + rec.run + "/" + ref.slice(1));
    if (!$("dialog").open || !box.isConnected) return;
    box.append(
      el("p", entry.at + " · " + entry.source + " · " + entry.level),
      el("pre", entry.message),
    );
    if (entry.raw) box.append(el("p", "原始字节"), el("pre", entry.raw));
    box.append(button("在回放中查看前后日志", () => {
      $("dialog").close();
      openReplay(rec.run, ref);
    }));
  } else {
    const item = rec.snapshot.results.find((x) => x.ref === ref);
    if (item)
      box.append(
        el("p", item.at + " · " + item.name + " · " + item.state),
        el("pre", item.detail),
      );
  }
}
async function enterAnalysis(run) {
  if (!state) return;
  if (aiBusy) {
    aiMessage("AI 正在分析，完成后才能换记录。");
    return;
  }
  invalidatePreview();
  const [cfg, history] = await Promise.all([
    get("/api/ai/settings"),
    get("/api/history"),
  ]);
  if (!aiReady) {
    applyAISettings(cfg);
    aiReady = true;
  }
  $("aiSettings").open = !cfg.model;
  $("aiRun").replaceChildren(
    ...history.map(
      (r) =>
        new Option(
          "#" +
            r.id +
            " · " +
            r.profile +
            " · " +
            new Date(r.started).toLocaleString(),
          r.id,
        ),
    ),
  );
  $("aiRun").value = String(run || state.run);
  aiMessage(
    cfg.model
      ? "当前模型 · " + cfg.model
      : "第一次用：先在下面“设置用哪个 AI”里填地址和模型（本机的 Ollama、LM Studio，或兼容 OpenAI 的云服务都行）。",
  );
  await loadRunResults();
}
function openAnalysis(run) {
  requestedRun = run;
  aiNav.click();
}
aiNav.addEventListener(
  "click",
  handle(async () => {
    const run = requestedRun || state?.run;
    requestedRun = null;
    await enterAnalysis(run);
  }),
);
bind("analyzeFromLog", () => openAnalysis(state.run));
$("aiRun").onchange = handle(async () => {
  invalidatePreview();
  await loadRunResults();
});
for (const id of [
  "aiFocus",
  "aiQuestion",
  "aiBase",
  "aiModel",
  "aiKey",
  "aiRemember",
])
  $(id).addEventListener("input", invalidatePreview);
bind("aiSave", () =>
  busyAI(async () => {
    await saveAI();
    aiMessage("设置已保存。");
  }),
);
bind("aiForget", () =>
  busyAI(async () => {
    await saveAI(true);
    aiMessage("当前接口的密钥已清除。");
  }),
);
bind("aiList", () =>
  busyAI(async () => {
    const typed = $("aiModel").value;
    await saveAI();
    aiMessage("正在读取模型列表…");
    const models = await aiPost("models");
    $("aiModels").replaceChildren(...models.map((x) => new Option(x, x)));
    if (!typed && models.length) $("aiModel").value = models[0];
    aiMessage(
      models.length
        ? "找到 " + models.length + " 个模型，可选择后保存。"
        : "服务没有列出模型，可以手动填写。",
    );
  }),
);
bind("aiTest", () =>
  busyAI(async () => {
    await saveAI();
    aiMessage("正在测试所选模型…");
    const res = await aiPost("test");
    aiMessage(res.message);
  }),
);
bind("aiPreview", () =>
  busyAI(async () => {
    const cfg = await saveAI();
    if (!cfg.model) {
      $("aiSettings").open = true;
      throw Error("请先填写模型名称。");
    }
    const preview = await aiPost("preview", {
      run: Number($("aiRun").value),
      focus: $("aiFocus").value,
      question: $("aiQuestion").value,
    });
    aiTicket = preview.ticket;
    const s = preview.snapshot,
      area = $("aiPayload");
    area.hidden = false;
    area.append(
      el("h3", "要发给 AI 的内容"),
      el("p", cfg.base + " · " + cfg.model),
      el(
        "p",
        "记录 #" +
          s.session +
          " · " +
          s.selected_events +
          " 条记录 · " +
          s.results.length +
          " 项检查结果",
      ),
    );
    if (!$("analysis").hidden) area.focus();
    if (!preview.ticket) {
      area.append(el("p", "这次记录里还没有内容。先连上板子收一些输出，或者换一条以前的记录。"));
      area.append(button("看板子状态", () => document.querySelector('.rail [data-tab="monitor"]').click()));
      aiMessage("暂无板卡诊断资料，本次未发送。");
      return;
    }
    const details = el("details");
    details.append(
      el("summary", "查看发送正文"),
      el("pre", JSON.stringify(s, null, 2)),
    );
    area.append(details);
    area.append(
      button("发送并分析", () =>
        busyAI(async () => {
          aiMessage("正在等待模型分析，设备日志继续采集…");
          let report;
          try {
            report = await aiPost("analyze", { ticket: aiTicket });
          } catch (error) {
            aiMessage("本次报告请求未完成 · " + error.message, true);
            return;
          }
          invalidatePreview();
          aiMessage("报告 #" + report.id + " 已收到并保存，可点击引用核对证据。");
          try {
            await loadRunResults();
          } catch {
            aiMessage("分析 #" + report.id + " 已保存。重新打开“AI 帮我看”能看到它。");
          }
        }),
      ),
    );
    area.querySelector("button").classList.add("primary");
    aiMessage("下面就是要发给 AI 的全部内容。看过没问题，再点“发送并分析”。");
  }),
);
