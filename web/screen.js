"use strict";
(() => {
  const image = $("screenFrame"), dock = $("screenDock"), layout = $("screenLayout");
  const testTabs = new Set(["monitor", "hardware", "terminal", "voice", "signal", "help"]);
  const narrow = matchMedia("(max-width: 970px)");
  let activeTab = "monitor";
  let controller = null, timer = 0, shown = "";
  const source = $("screenSource");
  function syncSource() {
    const local = source.value === "mcp";
    source.disabled = Boolean(controller);
    for (const input of [$("screenAddress"), $("screenPath"), $("screenCode")]) {
      input.parentElement.hidden = local;
      input.disabled = local || Boolean(controller);
    }
    $("screenHttpHelp").hidden = local;
    $("screenMcpHelp").hidden = !local;
    $("screenSetup").hidden = !local || Boolean(controller);
  }
  source.onchange = syncSource;
  syncSource();
  function stop(message = "已停止") {
    controller?.abort();
    controller = null;
    clearTimeout(timer);
    image.hidden = true;
    image.removeAttribute("src");
    if (shown) URL.revokeObjectURL(shown);
    shown = "";
    $("screenEmpty").hidden = false;
    $("screenMeta").textContent = "";
    $("screenStatus").textContent = message;
    layout.classList.remove("receiving");
    $("screenDockHint").textContent = dock.open ? "屏幕预览" : "展开连接";
    $("screenStart").disabled = false;
    $("screenStop").disabled = true;
    syncSource();
  }
  $("screenForm").onsubmit = async event => {
    event.preventDefault();
    if (controller) return;
    const address = $("screenAddress").value.trim(), code = $("screenCode").value;
    const path = $("screenPath").value.trim();
    const current = new AbortController();
    controller = current;
    $("screenStart").disabled = true;
    $("screenStop").disabled = false;
    syncSource();
    $("screenStatus").textContent = "正在连接";
    async function next() {
      let candidate = "";
      try {
        const res = await fetch("/api/screen/frame", {
          method: "POST", signal: current.signal,
          headers: {"Content-Type":"application/json", "X-BoardBench":token},
          body: JSON.stringify(source.value === "mcp" ? {source:"mcp"} : {address, code, path})
        });
        if (!res.ok) throw Error((await res.json()).error || "投屏连接失败");
        candidate = URL.createObjectURL(await res.blob());
        const probe = new Image();
        probe.src = candidate;
        await probe.decode();
        if (controller !== current) return;
        const previous = shown;
        shown = candidate;
        candidate = "";
        image.src = shown;
        image.hidden = false;
        if (previous) URL.revokeObjectURL(previous);
        layout.classList.add("receiving");
        $("screenDockHint").textContent = source.value === "mcp" ? "小智本地会话" : address;
        $("screenEmpty").hidden = true;
        if ($("screenStatus").textContent !== "投影中") $("screenStatus").textContent = "投影中";
        $("screenMeta").textContent = probe.naturalWidth + " × " + probe.naturalHeight + " · " + new Date().toLocaleTimeString();
        timer = setTimeout(next, 1200);
      } catch (err) {
        if (controller === current) stop(err.message);
      } finally {
        if (candidate) URL.revokeObjectURL(candidate);
      }
    }
    next();
  };
  $("screenStop").onclick = () => stop();
  function placeViewer() {
    const inTest = testTabs.has(activeTab);
    if (!inTest && activeTab !== "screen" && controller) stop();
    const parent = narrow.matches ? $("testMainPanel") : $("testSide");
    if (dock.parentElement !== parent) parent.prepend(dock);
    dock.hidden = !inTest;
    if (inTest && controller) dock.open = true;
    const target = inTest ? $("screenDockContent") : $("screenHome");
    if (layout.parentElement !== target) target.append(layout);
  }
  dock.addEventListener("toggle", () => {
    if (!dock.open && !dock.hidden && controller) stop();
    if (!controller) $("screenDockHint").textContent = dock.open ? "屏幕预览" : "展开连接";
  });
  document.addEventListener("boardbench:tab", event => {
    const fromFullScreen = activeTab === "screen" && controller;
    activeTab = event.detail;
    placeViewer();
    if (fromFullScreen && testTabs.has(activeTab))
      $("testMainPanel").scrollIntoView({block:"start"});
  });
  narrow.addEventListener("change", placeViewer);
  placeViewer();
  document.addEventListener("visibilitychange", () => { if (document.hidden && controller) stop(); });
  window.addEventListener("pagehide", () => stop());
})();
