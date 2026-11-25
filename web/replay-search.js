"use strict";

function createReplaySearch(node, recording, eventBlock, pause) {
  const input = node("Query"), status = node("SearchStatus"), rows = node("Matches");
  let active = null, hit = null, reference = null;
  let atStart = true, atEnd = false;

  function controls() {
    node("Find").disabled = !input.value.trim() || Boolean(active) || atEnd;
    node("Find").textContent = hit ? "下一处" : "查找";
    node("FindPrev").hidden = !hit;
    node("FindPrev").disabled = atStart || Boolean(active);
    node("Pin").hidden = !hit || reference?.id === hit.id;
    node("Unpin").hidden = node("Delta").hidden = !reference;
    if (reference) {
      let text = "起点 L" + reference.id;
      if (hit) {
        const delta = Date.parse(hit.at) - Date.parse(reference.at);
        if (Number.isFinite(delta)) text += " → L" + hit.id + " · " + (delta > 0 ? "+" : "") + delta.toLocaleString("zh-CN") + " ms";
      }
      node("Delta").textContent = text;
    }
  }
  function announce(message) {
    status.textContent = message;
    status.hidden = !message;
  }
  function cancel() {
    active?.abort();
    active = null;
    announce("");
    controls();
  }
  function clearHits() {
    hit = null;
    atStart = true;
    atEnd = false;
    rows.replaceChildren();
    rows.hidden = true;
    cancel();
  }
  async function find(previous) {
    const { run, upper } = recording();
    const q = input.value.trim();
    if (active || !run || upper == null || !q || (previous ? atStart : atEnd)) return;
    pause();
    const controller = (active = new AbortController());
    controls();
    announce("正在查找…");
    try {
      const params = new URLSearchParams({ q, upper, after: hit?.id || 0, direction: previous ? "previous" : "next" });
      const result = await get("/api/replay/" + encodeURIComponent(run) + "/search?" + params, { signal: controller.signal });
      if (active !== controller) return;
      if (!result.event) {
        if (previous) atStart = true;
        else atEnd = true;
        announce(!hit ? "这次记录里没找到。" : previous ? "已是第一处匹配。" : "已是最后一处匹配。");
        return;
      }
      atStart = !hit && !previous;
      atEnd = false;
      hit = result.event;
      rows.hidden = false;
      rows.replaceChildren(...result.context.map(event => {
        const line = eventBlock(event, false);
        if (event.id === hit.id) line.classList.add("rp-issue-hit");
        return line;
      }));
      const match = rows.querySelector(".rp-issue-hit");
      rows.scrollTop += match.getBoundingClientRect().top - rows.getBoundingClientRect().top -
        (rows.clientHeight - match.offsetHeight) / 2;
      announce("命中 L" + hit.id + " · " + hit.source + " · " + fmtTime(hit.at));
    } catch (error) {
      if (!controller.signal.aborted) announce("查找失败，可重试 · " + error.message);
    } finally {
      if (active === controller) {
        active = null;
        controls();
      }
    }
  }
  input.oninput = clearHits;
  node("SearchForm").onsubmit = event => { event.preventDefault(); find(false); };
  node("FindPrev").onclick = () => find(true);
  node("Pin").onclick = () => { reference = hit; controls(); };
  node("Unpin").onclick = () => { reference = null; controls(); };
  controls();
  return {
    cancel,
    locate(ref) {
      input.value = ref;
      clearHits();
      return find(false);
    },
    reset() {
      reference = null;
      input.value = "";
      clearHits();
    },
  };
}
