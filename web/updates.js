"use strict";
(() => {
  const trigger = $("appUpdate");
  const labels = {
    idle: "检查更新", checking: "正在检查…", current: "重新检查", available: "下载更新",
    downloading: "正在下载…", verifying: "正在校验…", ready: "立即重启更新",
    restarting: "正在重启…", error: "重试检查",
  };
  let snapshot, body, poll, pending = false;

  function paint(s) {
    snapshot = s;
    trigger.hidden = false;
    trigger.textContent = s.phase === "ready" ? "新版已就绪" : s.phase === "available" ? "有新版本" : "软件更新";
    if (!body?.isConnected || !$("dialog").open) return;
    const pct = s.size ? Math.min(100, Math.floor(100 * s.received / s.size)) : 0;
    const messages = {
      idle: "可从 GitHub 检查稳定版本。", checking: "正在查询 GitHub 发布记录…",
      current: "你使用的已经是最新版本。", available: "可下载 " + s.latest,
      downloading: "正在下载 " + s.latest + " · " + pct + "%",
      verifying: "正在校验并准备安装包…", ready: s.latest + " 已就绪，重启后安装。",
      restarting: "正在关闭窗口并安装更新…", error: "",
    };
    const msg = body.querySelector("[role=status]");
    msg.textContent = [messages[s.phase], s.error].filter(Boolean).join("　");
    const progress = body.querySelector("progress");
    progress.hidden = s.phase !== "downloading";
    progress.value = pct;
    const notes = body.querySelector("details");
    notes.hidden = !s.notes;
    notes.lastChild.textContent = s.notes;
    const action = body.querySelector("button");
    action.textContent = labels[s.phase];
    action.disabled = pending || ["checking", "verifying", "downloading", "restarting"].includes(s.phase);
  }

  function refresh(s) {
    clearTimeout(poll);
    paint(s);
    if (["checking", "downloading", "verifying"].includes(s.phase)) {
      poll = setTimeout(async () => {
        try { refresh(await get("/api/update")); }
        catch (e) { paint({...s, phase: "error", error: e.message}); }
      }, 450);
    }
  }
  trigger.onclick = async () => {
    body = modal("软件更新");
    body.classList.add("app-update");
    const version = el("strong", "BoardBench  v" + snapshot.version);
    const status = el("p");
    status.setAttribute("role", "status");
    const progress = el("progress");
    progress.setAttribute("aria-label", "下载进度");
    progress.max = 100;
    const notes = el("details");
    notes.append(el("summary", "更新内容"), el("pre"));
    const action = el("button");
    action.className = "primary";
    action.onclick = async () => {
      const op = ({available: "download", ready: "install"})[snapshot.phase] || "check";
      pending = true;
      paint(snapshot);
      try {
        const s = await get("/api/update/" + op, {method: "POST", headers: {"X-BoardBench": token}});
        pending = false;
        refresh(s);
      } catch (e) {
        pending = false;
        try { refresh(await get("/api/update")); } catch { paint(snapshot); }
        notice(e.message);
      }
    };
    body.append(version, status, progress, notes, action);
    refresh(snapshot);
    try { refresh(await get("/api/update")); } catch (e) { notice(e.message); }
  };
  window.showDesktopUpdate = s => { if (s) refresh(s); };
})();
