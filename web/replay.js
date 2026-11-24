"use strict";

// Archived events are fetched in bounded segments. This module has no device controls.
const replayPanel = (() => {
  const panel = document.getElementById("replay");
  const PAGE = 250;
  const KEEP_CHECKPOINTS = 12;
  panel.innerHTML = `
    <div class="section-title"><i class="ico clock-counter-clockwise" aria-hidden="true"></i><h2>会话回放</h2></div>
    <div class="rp-toolbar"><label>本地会话<select id="rpRun"></select></label>
      <button id="rpReload">刷新记录</button><button id="rpVcd">导出按键波形</button></div>
    <p id="rpNotice" role="status"></p>
    <div id="rpPlayer" hidden>
      <div class="rp-player">
        <div class="rp-clock"><strong id="rpElapsed"></strong><span id="rpTime"></span></div>
        <div class="rp-track"><canvas id="rpDensity" aria-hidden="true"></canvas>
          <input id="rpSeek" type="range" min="0" step="1" aria-label="拖动本段时间轴"></div>
        <div class="rp-axis"><span id="rpFirst"></span><span id="rpLast"></span></div>
        <div class="rp-controls"><button id="rpPlay" class="primary">播放</button>
          <label class="rp-rate">倍速 <select id="rpRate" aria-label="回放速度">
            <option value="0.5">0.5×</option><option value="1" selected>1×</option>
            <option value="2">2×</option><option value="4">4×</option><option value="8">8×</option>
          </select></label>
          <button id="rpPrevEvent">上一条</button><button id="rpNextEvent">下一条</button>
          <button id="rpNextIssue" aria-controls="rpIssue">下一处异常</button>
        </div>
      </div>
      <div class="rp-paging"><span id="rpSegment"></span><div>
        <button id="rpFirstPage">回到开头</button><button id="rpPrevPage">上一段</button><button id="rpNextPage">下一段</button>
      </div></div>
      <section id="rpIssue" class="rp-issue" hidden>
        <div class="rp-issue-head"><div><h3 id="rpIssueTitle">异常上下文</h3>
          <p id="rpIssueNote" role="status"></p></div><button id="rpIssueClose">收起</button></div>
        <div id="rpIssueRows"></div>
      </section>
      <div class="rp-columns"><section><h3>元件最后上报</h3><div id="rpParts"></div></section>
        <section><h3>光标前的事件</h3><div id="rpLog"></div></section></div>
    </div>`;
  const node = (id) => panel.querySelector("#rp" + id);
  const clock = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
    hourCycle: "h23",
  });
  const firstCheckpoint = () => ({
    after: 0,
    start: null,
    parts: new Map(),
    disconnected: false,
    number: 1,
  });
  let requestNumber = 0,
    visits = 0,
    selected = "",
    requestedRun = null;
  let upper,
    origin,
    events = [],
    points = [],
    checkpoints = [],
    segment,
    more = false;
  let cursor = -1,
    position = 0,
    frame = 0,
    anchorTime = 0,
    anchorPosition = 0,
    busy = false;
  let aborter = null,
    exportToken = 0;
  let issueRequest = null,
    lastIssue = null,
    issuesFinished = false;
  function clearIssue() {
    if (!issueRequest && lastIssue === null && node("Issue").hidden) return;
    issueRequest?.abort();
    issueRequest = lastIssue = null;
    issuesFinished = false;
    node("Issue").hidden = true;
    node("IssueRows").replaceChildren();
    node("NextIssue").disabled = busy || !events.length;
  }
  async function nextIssue() {
    if (busy || issueRequest || issuesFinished || !events.length) return;
    pause();
    const after = lastIssue ?? events[cursor]?.id ?? segment.after;
    const controller = (issueRequest = new AbortController());
    node("Issue").hidden = false;
    node("IssueTitle").textContent = "异常上下文";
    node("IssueNote").textContent = "正在查找 L" + after + " 之后的错误与告警…";
    setControls();
    try {
      const query = new URLSearchParams({ after, upper });
      const found = await get(
        "/api/replay/" + encodeURIComponent(selected) + "/next-issue?" + query,
        {
          signal: controller.signal,
        },
      );
      if (controller !== issueRequest) return;
      if (!found.event) {
        issuesFinished = true;
        node("IssueNote").textContent =
          "已查到本次回放的末尾，L" + after + " 后没有其他错误或告警。";
        return;
      }
      lastIssue = found.event.id;
      node("IssueTitle").textContent =
        (found.event.issue_level === "WARN" ? "告警" : "错误") +
        " · L" +
        lastIssue;
      const from = events[cursor]?.id ?? segment.after;
      node("IssueNote").textContent =
        "命中时间 " + stamp(found.event.at) + " · 回放光标停在 L" + from;
      const context = (found.context || [found.event]).map((event) => {
        const line = eventBlock(event, false);
        if (event.id === lastIssue) line.classList.add("rp-issue-hit");
        return line;
      });
      const rows = node("IssueRows");
      rows.replaceChildren(...context);
      const hit = rows.querySelector(".rp-issue-hit");
      if (hit) {
        rows.scrollTop +=
          hit.getBoundingClientRect().top -
          rows.getBoundingClientRect().top -
          Math.max(0, (rows.clientHeight - hit.offsetHeight) / 2);
      }
    } catch (error) {
      if (!controller.signal.aborted)
        node("IssueNote").textContent = "查找失败，可重试 · " + error.message;
    } finally {
      if (controller === issueRequest) {
        issueRequest = null;
        setControls();
      }
    }
  }
  function elapsed(ms) {
    const seconds = Math.floor(Math.max(0, ms) / 1000);
    return [
      Math.floor(seconds / 3600),
      Math.floor(seconds / 60) % 60,
      seconds % 60,
    ]
      .map((v) => String(v).padStart(2, "0"))
      .join(":");
  }
  function stamp(value) {
    return clock.format(new Date(value));
  }
  function pause() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    node("Play").textContent = "播放";
  }
  function setControls() {
    const enabled = !busy && events.length > 0;
    node("Play").disabled = !enabled;
    node("NextIssue").disabled =
      !enabled || Boolean(issueRequest) || issuesFinished;
    node("Seek").disabled = !enabled || points.at(-1) === segment?.start;
    node("NextEvent").disabled =
      !enabled || (cursor >= events.length - 1 && !more);
    node("PrevEvent").disabled =
      !enabled || (cursor <= 0 && checkpoints.length < 2);
    node("NextPage").disabled = !enabled || !more;
    node("PrevPage").disabled = !enabled || checkpoints.length < 2;
    node("FirstPage").disabled =
      !enabled || (segment?.number === 1 && cursor === 0);
    node("Vcd").disabled = !enabled;
  }
  function atSegmentStart() {
    return {
      parts: new Map(segment.parts),
      disconnected: segment.disconnected,
    };
  }
  function reduce(snapshot, event) {
    if (event.boundary === "connect" || event.boundary === "disconnect") {
      snapshot.parts.clear();
      snapshot.disconnected = event.boundary === "disconnect";
    }
    // The serial reader can flush a partial line after the port has already closed.
    if (snapshot.disconnected || !event.part) return;
    if (snapshot.parts.has(event.part) || snapshot.parts.size < 128)
      snapshot.parts.set(event.part, event);
  }
  function eventBlock(event, isPart) {
    const line = el("div");
    line.className = "rp-line";
    const title = el(
      "strong",
      isPart
        ? event.part_name || event.part
        : event.level + " · " + event.source,
    );
    const detail = el(
      "span",
      isPart
        ? [event.status, event.value]
            .filter((v) => v != null && v !== "")
            .join(" · ")
        : event.message,
    );
    const time = el("small", "L" + event.id + " · " + stamp(event.at));
    if (event.issue_level === "ERROR") line.classList.add("rp-fault");
    if (event.issue_level === "WARN") line.classList.add("rp-warn");
    line.append(title, time, detail);
    return line;
  }
  function show(index, time, force = false) {
    if (position !== time || cursor !== index || force) clearIssue();
    position = time;
    node("Seek").value = time - segment.start;
    node("Seek").setAttribute("aria-valuetext", stamp(time));
    node("Time").textContent = stamp(time);
    node("Elapsed").textContent = elapsed(time - origin);
    if (cursor === index && !force) return;
    cursor = index;
    const snapshot = atSegmentStart();
    for (let i = 0; i <= index; i++) reduce(snapshot, events[i]);
    const list = [...snapshot.parts.values()].sort((a, b) => b.id - a.id);
    node("Parts").replaceChildren(
      ...(list.length
        ? list.map((e) => eventBlock(e, true))
        : [
            el(
              "p",
              snapshot.disconnected
                ? "该时刻连接已断开。"
                : "此时还没有元件上报。",
            ),
          ]),
    );
    const recent = events.slice(Math.max(0, index - 19), index + 1).reverse();
    node("Log").replaceChildren(
      ...(recent.length
        ? recent.map((e) => eventBlock(e, false))
        : [el("p", "正在等待本段第一条事件。")]),
    );
    node("Segment").textContent =
      "第 " +
      segment.number +
      " 段 · " +
      (index + 1) +
      " / " +
      events.length +
      " 条";
    setControls();
  }
  function seek(time) {
    let lo = 0,
      hi = points.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (points[mid] <= time) lo = mid + 1;
      else hi = mid;
    }
    show(lo - 1, time);
  }
  function paint() {
    const canvas = node("Density");
    const width = Math.max(64, Math.floor(canvas.clientWidth || 300));
    const height = 42,
      scale = Math.min(devicePixelRatio || 1, 2);
    canvas.width = width * scale;
    canvas.height = height * scale;
    const ctx = canvas.getContext("2d");
    ctx.scale(scale, scale);
    const bins = Math.max(10, Math.min(180, Math.floor(width / 5)));
    const counts = new Uint16Array(bins),
      issues = new Uint8Array(bins);
    const span = Math.max(1, points.at(-1) - segment.start);
    points.forEach((t, i) => {
      const bin = Math.min(
        bins - 1,
        Math.floor(((t - segment.start) / span) * bins),
      );
      counts[bin]++;
      if (events[i].issue_level === "ERROR") issues[bin] = 2;
      else if (events[i].issue_level === "WARN" && !issues[bin])
        issues[bin] = 1;
    });
    const max = Math.max(...counts, 1);
    for (let i = 0; i < bins; i++) {
      const h = counts[i]
        ? 5 + (Math.log1p(counts[i]) / Math.log1p(max)) * 32
        : 1;
      ctx.fillStyle =
        issues[i] === 2
          ? "#ff8e82"
          : issues[i] === 1
            ? "#ffcf79"
            : counts[i]
              ? "#86c8ff"
              : "#526277";
      ctx.fillRect(
        (i * width) / bins,
        height - h,
        Math.max(1, width / bins - 1.5),
        h,
      );
    }
  }
  async function load(checkpoint, options = {}) {
    clearIssue();
    const request = ++requestNumber,
      run = selected;
    aborter?.abort();
    const controller = (aborter = new AbortController());
    pause();
    busy = true;
    setControls();
    node("Notice").textContent = "正在读取第 " + checkpoint.number + " 段…";
    try {
      const query = new URLSearchParams({
        after: String(checkpoint.after),
        limit: String(PAGE),
      });
      if (upper != null) query.set("upper", upper);
      const data = await get(
        "/api/replay/" + encodeURIComponent(run) + "/events?" + query,
        { signal: controller.signal },
      );
      if (request !== requestNumber || selected !== run) return;
      upper = data.upper;
      events = data.events;
      segment = checkpoint;
      more = data.more;
      if (!events.length) {
        node("Player").hidden = true;
        node("Notice").textContent = "这段会话尚无事件。";
        return;
      }
      let previous = checkpoint.start ?? Date.parse(events[0].at);
      if (!Number.isFinite(previous)) previous = 0;
      segment.start = previous;
      points = events.map((e) => {
        const t = Date.parse(e.at);
        previous = Math.max(previous, Number.isFinite(t) ? t : previous);
        return previous;
      });
      if (checkpoint.number === 1) origin = segment.start;
      if (options.rewind) checkpoints.pop();
      else if (checkpoints.at(-1) !== checkpoint) {
        checkpoints.push(checkpoint);
        if (checkpoints.length > KEEP_CHECKPOINTS) checkpoints.shift();
      }
      node("Player").hidden = false;
      node("Notice").textContent =
        "按记录时间回放 · 蓝色为普通事件，黄色为告警，红色为错误";
      node("Seek").max = points.at(-1) - segment.start;
      node("First").textContent = stamp(segment.start);
      node("Last").textContent = stamp(points.at(-1));
      busy = false;
      paint();
      const idx = options.atEnd
        ? events.length - 1
        : options.fromPlay && points[0] > segment.start
          ? -1
          : 0;
      show(idx, idx < 0 ? segment.start : points[idx], true);
      if (
        options.fromPlay &&
        !panel.hidden &&
        (more || idx < events.length - 1)
      )
        play();
    } catch (error) {
      if (request === requestNumber && !controller.signal.aborted)
        node("Notice").textContent = "回放读取失败 · " + error.message;
    } finally {
      if (request === requestNumber) {
        busy = false;
        setControls();
      }
    }
  }
  function next(fromPlay = false) {
    if (!more || busy) return;
    const snapshot = atSegmentStart();
    events.forEach((event) => reduce(snapshot, event));
    return load(
      {
        after: events.at(-1).id,
        start: points.at(-1),
        ...snapshot,
        number: segment.number + 1,
      },
      { fromPlay },
    );
  }
  function tick(now) {
    if (panel.hidden) {
      pause();
      return;
    }
    const time =
      anchorPosition + (now - anchorTime) * Number(node("Rate").value);
    seek(Math.min(time, points.at(-1)));
    if (time >= points.at(-1)) {
      pause();
      if (more) next(true);
    } else frame = requestAnimationFrame(tick);
  }
  function play() {
    if (busy || !events.length) return;
    if (position >= points.at(-1) && cursor === events.length - 1) {
      if (more) return next(true);
      if (segment.number > 1) {
        checkpoints = [];
        return load(firstCheckpoint(), { fromPlay: true });
      }
      show(0, points[0], true);
    }
    anchorTime = performance.now();
    anchorPosition = position;
    node("Play").textContent = "暂停";
    frame = requestAnimationFrame(tick);
  }
  function reset() {
    pause();
    clearIssue();
    requestNumber++;
    exportToken++;
    aborter?.abort();
    upper = null;
    events = [];
    points = [];
    checkpoints = [];
    segment = null;
    cursor = -1;
    busy = false;
    node("Player").hidden = true;
    setControls();
  }
  async function sessions(id = null) {
    const seq = ++visits;
    try {
      const runs = await get("/api/history");
      if (seq !== visits || panel.hidden) return;
      const target = String(id ?? selected ?? "");
      const preferred =
        runs.find((r) => String(r.id) === target) ||
        runs.find((r) => r.id !== state?.run) ||
        runs[0];
      node("Run").replaceChildren(
        ...runs.map((r) => {
          const o = el(
            "option",
            "#" +
              r.id +
              " · " +
              r.profile +
              " · " +
              new Date(r.started).toLocaleString(),
          );
          o.value = r.id;
          return o;
        }),
      );
      if (!preferred) {
        reset();
        selected = "";
        node("Notice").textContent = "还没有可回放的会话。";
        return;
      }
      node("Run").value = preferred.id;
      if (selected === String(preferred.id) && events.length && id === null)
        return;
      selectRun(preferred.id);
    } catch (e) {
      if (seq === visits && !panel.hidden)
        node("Notice").textContent = "无法读取会话 · " + e.message;
    }
  }
  function selectRun(id) {
    reset();
    selected = String(id);
    return load(firstCheckpoint());
  }
  function activation() {
    if (panel.hidden) {
      visits++;
      pause();
      clearIssue();
      return;
    }
    const id = requestedRun;
    requestedRun = null;
    sessions(id);
  }
  node("Run").onchange = () => {
    visits++;
    selectRun(node("Run").value);
  };
  node("Reload").onclick = () => sessions(node("Run").value || null);
  node("Play").onclick = () => (frame ? pause() : play());
  node("Rate").onchange = () => {
    if (frame) {
      anchorTime = performance.now();
      anchorPosition = position;
    }
  };
  node("Seek").oninput = () => {
    pause();
    seek(segment.start + Number(node("Seek").value));
  };
  node("PrevEvent").onclick = () => {
    pause();
    if (cursor > 0) show(cursor - 1, points[cursor - 1]);
    else if (checkpoints.length > 1)
      load(checkpoints.at(-2), { rewind: true, atEnd: true });
  };
  node("NextEvent").onclick = () => {
    pause();
    if (cursor + 1 < events.length) show(cursor + 1, points[cursor + 1]);
    else next();
  };
  node("FirstPage").onclick = () => {
    checkpoints = [];
    load(firstCheckpoint());
  };
  node("PrevPage").onclick = () => load(checkpoints.at(-2), { rewind: true });
  node("NextPage").onclick = () => next();
  node("NextIssue").onclick = nextIssue;
  node("IssueClose").onclick = () => {
    clearIssue();
    node("NextIssue").focus();
  };
  node("Vcd").onclick = async () => {
    const run = selected,
      version = ++exportToken;
    const path =
      "/api/replay/" + encodeURIComponent(run) + "/keys.vcd?upper=" + upper;
    node("Vcd").disabled = true;
    try {
      const response = await fetch(path, { method: "HEAD" });
      if (version !== exportToken || run !== selected) return;
      if (!response.ok)
        throw Error(
          response.status === 422
            ? "本次未记录 K0/K1/K2 的完整电平。"
            : "无法导出按键波形 · " + response.status,
        );
      const link = el("a");
      link.href = path;
      link.download = "boardbench-" + run + "-keys.vcd";
      panel.append(link);
      link.click();
      link.remove();
    } catch (e) {
      if (version === exportToken) node("Notice").textContent = e.message;
    } finally {
      if (version === exportToken) setControls();
    }
  };
  new MutationObserver(activation).observe(panel, {
    attributes: true,
    attributeFilter: ["hidden"],
  });
  new ResizeObserver(() => {
    if (!panel.hidden && events.length && segment) paint();
  }).observe(node("Density"));
  setControls();
  if (!panel.hidden) activation();
  return {
    open(id) {
      if (!panel.hidden) return sessions(id);
      requestedRun = id;
      document.querySelector('#tabs [data-tab="replay"]').click();
    },
  };
})();
function openReplay(runId) {
  return replayPanel.open(runId);
}
