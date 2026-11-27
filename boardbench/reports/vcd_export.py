"""Export recorded active-low key samples as a Verilog value change dump."""

import asyncio
from datetime import datetime, timedelta

from aiohttp import web

from boardbench.devices.monitor import decode
from boardbench.checks.smart import KEY_LEVELS

PAGE = 128
STALE_MS = 8000  # Same interval as the component monitor.
UNKNOWN = "xxx"
IDENTIFIERS = "abc"
TRANSPORTS = {"串口", "TCP", "UDP"}
HEADER = """$version BoardBench $end
$comment Host receive times relative to session start, in milliseconds.
DOWN = 0, UP = 1. Levels are held between samples.
x marks unknown input, disconnect, or no key sample for 8 seconds. $end
$timescale 1 ms $end
$scope module keys $end
$var wire 1 a K0 $end
$var wire 1 b K1 $end
$var wire 1 c K2 $end
$upscope $end
$enddefinitions $end
#0
$dumpvars
xa
xb
xc
$end
"""


async def observations(bench, run, upper, start):
    cursor = 0
    elapsed = 0
    last_sample = None
    connected = True
    while cursor < upper:
        page = bench.rows(
            "SELECT id,at,level,source,message FROM logs WHERE run=? AND id>? AND id<=? ORDER BY id LIMIT ?",
            (run, cursor, upper, PAGE),
        )
        if not page:
            break
        for row in page:
            now = datetime.fromisoformat(row["at"])
            # NTP can move the wall clock back. Preserve acquisition order with tied timestamps.
            elapsed = max(elapsed, (now - start) // timedelta(milliseconds=1))
            if last_sample is not None and elapsed - last_sample > STALE_MS:
                yield last_sample + STALE_MS, UNKNOWN
                last_sample = None
            source, message = row["source"], row["message"]
            if source not in TRANSPORTS:
                continue
            if row["level"] == "INFO":
                if message == "连接已关闭":
                    connected = False
                    last_sample = None
                    yield elapsed, UNKNOWN
                elif message.startswith(
                    ("已连接 ", "tcp://", "udp://", "沿用当前设备连接")
                ):
                    connected = True
            if row["level"] != "RX" or not connected:
                continue
            parsed = decode(message)
            if not parsed or parsed[0] != "buttons":
                continue
            levels = (
                KEY_LEVELS.fullmatch(parsed[2].strip())
                if parsed[1] == "有读数"
                else None
            )
            if levels:
                last_sample = elapsed
                yield (
                    elapsed,
                    "".join(
                        "0" if level == "DOWN" else "1" for level in levels.groups()
                    ),
                )
            else:
                last_sample = None
                yield elapsed, UNKNOWN
        cursor = page[-1]["id"]
        await asyncio.sleep(0)
    yield (
        elapsed,
        None,
    )  # Include session duration even if the last records are unrelated.


def failure(code, message, status):
    return web.json_response(
        {"code": code, "error": message},
        status=status,
        headers={"X-BoardBench-Error": code, "Cache-Control": "no-store"},
    )


def routes(bench):
    endpoints = web.RouteTableDef()

    @endpoints.get("/api/replay/{run}/keys.vcd")
    async def keys_vcd(request):
        run = request.match_info["run"]
        session = bench.db.execute(
            "SELECT id,started FROM runs WHERE id=?", (run,)
        ).fetchone()
        if session is None:
            return failure("unknown_run", "找不到这个会话。", 404)
        run = session["id"]
        upper = bench.db.execute(
            "SELECT coalesce(max(id),0) FROM logs WHERE run=?", (run,)
        ).fetchone()[0]
        if "upper" in request.query:
            raw = request.query["upper"]
            if (
                len(raw) > 19
                or not raw.isascii()
                or not raw.isdecimal()
                or int(raw) > upper
            ):
                return failure("invalid_upper", "回放截止日志编号无效。", 400)
            upper = int(raw)
        start = datetime.fromisoformat(session["started"])
        stream = observations(bench, run, upper, start)
        try:
            async for tick, levels in stream:
                if levels is not None and levels != UNKNOWN:
                    break
            else:
                return failure(
                    "no_key_samples", "本会话没有完整的 K0、K1、K2 电平上报。", 422
                )

            headers = {
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="boardbench-{run}-keys.vcd"',
                "Cache-Control": "no-store",
            }
            if request.method == "HEAD":
                return web.Response(headers=headers)

            response = web.StreamResponse(headers=headers)
            await response.prepare(request)
            output = [HEADER, f"#{tick}\n"]
            for level, identifier in zip(levels, IDENTIFIERS):
                output.append(f"{level}{identifier}\n")
            previous, last_tick = levels, tick
            async for tick, levels in stream:
                if levels is None:
                    if tick > last_tick:
                        output.append(f"#{tick}\n")
                    break
                if levels == previous:
                    continue
                if tick != last_tick:
                    output.append(f"#{tick}\n")
                for before, after, identifier in zip(previous, levels, IDENTIFIERS):
                    if before != after:
                        output.append(f"{after}{identifier}\n")
                previous, last_tick = levels, tick
                if len(output) >= 256:
                    await response.write("".join(output).encode("ascii"))
                    output.clear()
            if output:
                await response.write("".join(output).encode("ascii"))
            await response.write_eof()
            return response
        finally:
            await stream.aclose()

    return endpoints
