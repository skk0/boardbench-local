"""Page through one recording without touching the live transport."""

from aiohttp import web
from monitor import PARTS, decode


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


def routes(bench):
    routes = web.RouteTableDef()

    @routes.get("/api/replay/{run}/events")
    async def events(req):
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
            return web.json_response({"error": str(exc)}, status=400)
        if not bench.db.execute("SELECT 1 FROM runs WHERE id=?", (run,)).fetchone():
            return web.json_response({"error": "找不到该会话。"}, status=404)
        last = (
            bench.db.execute("SELECT max(id) FROM logs WHERE run=?", (run,)).fetchone()[
                0
            ]
            or 0
        )
        upper = min(last, cutoff) if cutoff is not None else last
        rows = bench.rows(
            "SELECT id,at,level,source,message FROM logs "
            "WHERE run=? AND id>? AND id<=? ORDER BY id LIMIT ?",
            (run, after, upper, limit + 1),
        )
        more = len(rows) > limit
        rows = rows[:limit]
        for row in rows:
            row["boundary"] = connection_boundary(
                row["level"], row["source"], row["message"]
            )
            observation = decode(row["message"]) if row["level"] == "RX" else None
            part, status, value = observation[:3] if observation else (None, None, None)
            row.update(
                part=part, part_name=PARTS.get(part, part), status=status, value=value
            )
        return web.json_response(dict(events=rows, upper=upper, more=more))

    return routes
