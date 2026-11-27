"use strict";
(() => {
  const view = $("screen"), image = $("screenFrame");
  let controller = null, timer = 0, shown = "";
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
    $("screenStart").disabled = false;
    $("screenStop").disabled = true;
    $("screenAddress").disabled = $("screenCode").disabled = false;
  }
  $("screenForm").onsubmit = async event => {
    event.preventDefault();
    if (controller) return;
    const address = $("screenAddress").value.trim(), code = $("screenCode").value;
    const current = new AbortController();
    controller = current;
    $("screenStart").disabled = true;
    $("screenStop").disabled = false;
    $("screenAddress").disabled = $("screenCode").disabled = true;
    $("screenStatus").textContent = "正在连接";
    async function next() {
      let candidate = "";
      try {
        const res = await fetch("/api/screen/frame", {
          method: "POST", signal: current.signal,
          headers: {"Content-Type":"application/json", "X-BoardBench":token},
          body: JSON.stringify({address, code})
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
  new MutationObserver(() => { if (view.hidden && controller) stop(); })
    .observe(view, {attributes:true, attributeFilter:["hidden"]});
  document.addEventListener("visibilitychange", () => { if (document.hidden && controller) stop(); });
  window.addEventListener("pagehide", () => stop());
})();
