"""Page through one recording without touching the live transport."""

import asyncio
import re

from aiohttp import web
from boardbench.devices.monitor import PARTS, decode


def unsigned(value, name):
    if not (value.isascii() and value.isdecimal() and len(value) <= 19):
        raise ValueError(f"{name} 参数无效")
    number = int(value)
    if number > 2**63 - 1:
        raise ValueError(f"{name} 超出范围")
    return number


def connection_boundary(level, source, message):
    if level != "INFO" or source not in {"串口", "TCP", "UDP"}:
        return None
    if message == "连接已关闭":
        return "disconnect"
    if message.startswith("沿用当前设备连接 · "):
        return "connect"
    if source == "串口" and message.startswith("已连接 "):
        return "connect"
    if source in {"TCP", "UDP"} and " · 本机端口 " in message:
        return "connect"
    return None


FIELDS = "SELECT id,at,level,source,message FROM logs WHERE run=?"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
DEVICE_ERROR = re.compile(
    r"^(?:E\s*\(\d+\)|ERR(?:OR)?\b|panic\b|Guru Meditation)", re.I
)
DEVICE_WARN = re.compile(r"^(?:W\s*\(\d+\)|WARN(?:ING)?\b)", re.I)


def annotate(row):
    level, source, message = row["level"], row["source"], row["message"]
    observation = decode(message) if level == "RX" else None
    part, status, value = observation[:3] if observation else (None, None, None)
    severity = level if level in ("ERROR", "WARN") else None
    if level == "RX":
        text = ANSI.sub("", message).lstrip()
        if status in ("设备报错", "固件报错", "无应答") or DEVICE_ERROR.match(text):
            severity = "ERROR"
        elif DEVICE_WARN.match(text):
            severity = "WARN"
    row.update(
        part=part,
        part_name=PARTS.get(part, part),
        status=status,
        value=value,
        issue_level=severity,
        boundary=connection_boundary(level, source, message),
    )
    return row


def routes(bench):
    endpoints = web.RouteTableDef()

    def bounds(req):
        try:
            run = unsigned(req.match_info["run"], "会话")
            after = unsigned(req.query.get("after", "0"), "after")
            limit = unsigned(req.query.get("limit", "250"), "limit")
            cutoff = (
                unsigned(req.query["upper"], "upper") if "upper" in req.query else None
            )
            if not 1 <= limit <= 500:
                raise ValueError("每页可读取 1 至 500 条事件。")
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        if not bench.db.execute("SELECT 1 FROM runs WHERE id=?", (run,)).fetchone():
            raise web.HTTPNotFound(text="找不到该会话。")
        last = (
            bench.db.execute("SELECT max(id) FROM logs WHERE run=?", (run,)).fetchone()[
                0
            ]
            or 0
        )
        return run, after, min(last, cutoff) if cutoff is not None else last, limit

    def page(run, after, upper, count):
        return bench.rows(
            FIELDS + " AND id>? AND id<=? ORDER BY id LIMIT ?",
            (run, after, upper, count),
        )

    @endpoints.get("/api/replay/{run}/events")
    async def events(req):
        run, after, upper, limit = bounds(req)
        rows = page(run, after, upper, limit + 1)
        return web.json_response(
            dict(
                events=[annotate(r) for r in rows[:limit]],
                upper=upper,
                more=len(rows) > limit,
            )
        )

    def match_response(run, row, upper):
        if row is None:
            return web.json_response(dict(event=None, context=[], upper=upper))
        log_id = row["id"]
        before = bench.rows(
            FIELDS + " AND id<? ORDER BY id DESC LIMIT 5", (run, log_id)
        )
        event = annotate(row)
        context = ([annotate(r) for r in reversed(before)] + [event]
                   + [annotate(r) for r in page(run, log_id, upper, 3)])
        return web.json_response(dict(event=event, context=context, upper=upper))

    async def yield_to_capture(req):
        await asyncio.sleep(0)
        if req.transport is None or req.transport.is_closing():
            raise asyncio.CancelledError

    @endpoints.get("/api/replay/{run}/next-issue")
    async def next_issue(req):
        run, after, upper, _ = bounds(req)
        while after < upper:
            rows = page(run, after, upper, 250)
            if not rows:
                break
            for row in rows:
                if annotate(row)["issue_level"]:
                    return match_response(run, row, upper)
            after = rows[-1]["id"]
            await yield_to_capture(req)
        return match_response(run, None, upper)

    @endpoints.get("/api/replay/{run}/search")
    async def search(req):
        run, after, upper, _ = bounds(req)
        term = req.query.get("q", "").strip()
        direction = req.query.get("direction", "next")
        if not term or len(term) > 160 or direction not in ("next", "previous"):
            raise web.HTTPBadRequest(text="请输入 1–160 个字符并使用有效的查找方向。")
        backwards = direction == "previous"
        # An explicit #L reference is scoped to the same recording and frozen end.
        ref = re.fullmatch(r"#?L([0-9]+)", term, re.I)
        if ref:
            try:
                log_id = unsigned(ref[1], "日志编号")
            except ValueError as exc:
                raise web.HTTPBadRequest(text=str(exc)) from exc
            rows = bench.rows(FIELDS + " AND id=? AND id<=?", (run, log_id, upper))
            allowed = log_id < after if backwards else log_id > after
            return match_response(run, rows[0] if rows and allowed else None, upper)

        term = term.casefold()
        while True:
            if backwards:
                rows = bench.rows(FIELDS + " AND id<? AND id<=? ORDER BY id DESC LIMIT 250",
                                  (run, after, upper))
            else:
                rows = page(run, after, upper, 250)
            for row in rows:
                if any(term in ANSI.sub("", row[key]).casefold()
                       for key in ("message", "source", "level")):
                    return match_response(run, row, upper)
            if len(rows) < 250:
                return match_response(run, None, upper)
            after = rows[-1]["id"]
            await yield_to_capture(req)

    return endpoints
