"""BoardBench local control plane."""

import argparse
import asyncio
import csv
import contextlib
import io
import json
import sys
from pathlib import Path
import secrets
import webbrowser

if __name__ == "__main__" and sys.argv[1:2] == ["--esp-worker"]:
    import esptool

    sys.argv.pop(1)
    esptool._main()
    sys.exit(0)

from aiohttp import web
from ai_analysis import Analysis
from bench import Bench
from echo import Echo
from monitor import AutoUSB
from suite import Suite, make_plan

ROOT = Path(__file__).resolve().parent
DEFAULT = Path.home() / ".boardbench"


def make_app(directory, monitor_usb=True):
    directory.mkdir(parents=True, exist_ok=True)
    bench = Bench(directory / "history.sqlite3")
    echo = Echo(bench)
    ai = Analysis(bench)
    suite = Suite(bench)
    token = secrets.token_urlsafe(24)
    lock = asyncio.Lock()
    auto = AutoUSB(bench, lock)

    @web.middleware
    async def policy(req, handler):
        if req.url.host not in {"127.0.0.1", "localhost", "::1"}:
            raise web.HTTPForbidden()
        if req.method == "POST" and req.headers.get("X-BoardBench") != token:
            raise web.HTTPForbidden()
        try:
            response = await handler(req)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            bench.log("ERROR", "操作", f"{req.path} · {exc}")
            return web.json_response({"error": str(exc)}, status=400)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    app = web.Application(middlewares=[policy], client_max_size=256 * 1024)
    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(req):
        return web.FileResponse(ROOT / "web/index.html")

    @routes.get("/api/setup")
    async def setup(req):
        profiles = bench.profiles()
        plans = {}
        for name, tests in profiles.items():
            plan = make_plan(tests, "full")
            plans[name] = dict(
                total=len(tests),
                auto=len(plan["automatic"]),
                groups=len(plan["groups"]),
            )
        return web.json_response(
            dict(token=token, profiles=list(profiles), plans=plans, ip=Echo.address())
        )

    @routes.get("/api/ports")
    async def ports(req):
        return web.json_response(await asyncio.to_thread(bench.ports))

    @routes.get("/api/state")
    async def state(req):
        results = bench.rows(
            "SELECT name,state,detail,at FROM results WHERE run=?", (bench.run,)
        )
        logs = bench.rows(
            "SELECT * FROM (SELECT * FROM logs WHERE run=? ORDER BY id DESC LIMIT 350) ORDER BY id",
            (bench.run,),
        )
        return web.json_response(
            dict(
                run=bench.run,
                profile=bench.profile,
                tests=bench.tests,
                results=results,
                connected=bool(bench.ser),
                transport=bench.transport,
                port=bench.usb,
                baud=bench.baud,
                framing=bench.framing,
                network_host=bench.network_host,
                network_port=bench.network_port,
                job=bench.taskname,
                suite=suite.state(),
                storage_hold=bench.storage_hold,
                components=bench.components.states(bench.profile, bool(bench.ser)),
                auto_usb=auto.enabled,
                usb_message=auto.message,
                usb_ports=auto.online,
                rx=bench.rx,
                tx=bench.tx,
                logs=logs,
                echo=echo.url,
                frames=echo.frames,
            )
        )

    @routes.get("/api/components/{run}/{part}")
    async def component_log(req):
        run = int(req.match_info["run"])
        part = req.match_info["part"]
        return web.json_response(
            bench.rows(
                "SELECT l.id,l.at,l.level,l.message,l.raw FROM component_events e JOIN logs l ON l.id=e.log WHERE e.run=? AND e.part=? ORDER BY l.id DESC LIMIT 200",
                (run, part),
            )
        )

    @routes.get("/api/history")
    async def history(req):
        runs = bench.rows("SELECT id,started,profile,port FROM runs ORDER BY id DESC")
        backups = {}
        for f in bench.rows("SELECT * FROM backups ORDER BY rowid DESC"):
            backups.setdefault(f["run"], []).append(f)
        for r in runs:
            r["backups"] = backups.get(r["id"], [])
        return web.json_response(runs)

    @routes.get("/api/template")
    async def template(req):
        name = req.query.get("name", bench.profile)
        return web.json_response(
            dict(name=name, tests=bench.profiles()[name]),
            headers={
                "Content-Disposition": 'attachment; filename="board-profile.json"'
            },
        )

    @routes.get("/api/backup/{key}")
    async def backup_file(req):
        key = req.match_info["key"]
        row = bench.db.execute("SELECT run FROM backups WHERE id=?", (key,)).fetchone()
        if not row:
            raise web.HTTPNotFound()
        return web.FileResponse(
            bench.backups / (key + ".bin"),
            headers={
                "Content-Disposition": f'attachment; filename="boardbench-{row[0]}-{key[:8]}.bin"'
            },
        )

    @routes.get("/api/report/{run}/{fmt}")
    async def report(req):
        data = bench.report(int(req.match_info["run"]), include_logs=False)
        fmt = req.match_info["fmt"]
        if fmt in ("json", "txt"):
            data["ai_analyses"] = ai.history(data["id"])
            data["suites"] = suite.history(data["id"])
        if fmt == "json":
            body = json.dumps(data, ensure_ascii=False)[:-1] + ', "logs": ['
            mime = "application/json"
        elif fmt == "csv":
            body = "\ufeffUTC time,level,source,message,hex\r\n"
            mime = "text/csv"
        elif fmt == "txt":
            byname = {r["name"]: r for r in data["results"]}
            names = list(
                dict.fromkeys([t["name"] for t in data["tests"]] + list(byname))
            )
            rows = [
                f"BoardBench #{data['id']} · {data['profile']} · {data['port']}",
                data["started"],
                "",
                "检测结果",
            ]
            for name in names:
                r = byname.get(name, dict(state="未测", detail=""))
                rows.append(f"{r['state']}  {name}  {r['detail']}")
            for batch in data["suites"]:
                rows.extend(
                    [
                        "",
                        f"批量检测 #{batch['id']} · {batch['status']} · {batch['started']}",
                    ]
                )
                for name, outcome in batch["outcomes"].items():
                    rows.append(f"{outcome['state']}  {name}  {outcome['detail']}")
            for ai_row in data["ai_analyses"]:
                rows.extend(["", f"AI 分析 · {ai_row['model']} · {ai_row['at']}"])
                for result in ai_row["snapshot"]["results"]:
                    rows.append(
                        f"{result['ref']}  {result['at']}  {result['name']} · {result['state']} · {result['detail']}"
                    )
                answer = ai_row["answer"]
                for finding in answer.get("findings", []):
                    rows.append(
                        f"{finding['kind']} · {finding['text']}  {' '.join(finding['refs'])}"
                    )
                rows.extend("建议 · " + step for step in answer.get("next", []))
                if answer.get("unverified"):
                    rows.append("未核验文本 · " + answer["unverified"])
            body = "\n".join(rows) + "\n\n事件日志 · UTC\n"
            mime = "text/plain"
        else:
            raise web.HTTPNotFound()
        upper = bench.db.execute(
            "SELECT coalesce(max(id),0) FROM logs WHERE run=?", (data["id"],)
        ).fetchone()[0]
        response = web.StreamResponse(
            headers={
                "Content-Type": mime + "; charset=utf-8",
                "Content-Disposition": f'attachment; filename="boardbench-{data["id"]}.{fmt}"',
                "Cache-Control": "no-store",
            }
        )
        await response.prepare(req)
        await response.write(body.encode())
        cursor = 0
        while cursor < upper:
            entries = bench.rows(
                "SELECT id,at,level,source,message,raw FROM logs WHERE run=? AND id>? AND id<=? ORDER BY id LIMIT 128",
                (data["id"], cursor, upper),
            )
            if not entries:
                break
            if fmt == "json":
                chunk = ("," if cursor else "") + ",".join(
                    json.dumps(r, ensure_ascii=False) for r in entries
                )
            elif fmt == "txt":
                chunk = "".join(
                    f"L{r['id']}  {r['at']} [{r['level']}] {r['source']} | {r['message']}\n"
                    for r in entries
                )
            else:
                f = io.StringIO()
                writer = csv.writer(f)
                for r in entries:
                    fields = [r[k] for k in ("at", "level", "source", "message", "raw")]
                    writer.writerow(
                        [
                            "'" + v
                            if v.lstrip().startswith(("=", "+", "-", "@"))
                            else v
                            for v in fields
                        ]
                    )
                chunk = f.getvalue()
            await response.write(chunk.encode())
            cursor = entries[-1]["id"]
            await asyncio.sleep(0)
        if fmt == "json":
            await response.write(b"]}")
        await response.write_eof()
        return response

    @routes.post("/api/{op}")
    async def command(req):
        d = await req.json()
        op = req.match_info["op"]
        async with lock:
            if d.get("run") != bench.run:
                raise ValueError("测试会话已更新，请刷新页面。")
            if op == "new":
                if echo.runner:
                    raise ValueError("请先停止局域网回声。")
                await bench.select_profile(d["profile"])
            elif op == "import":
                bench.add_profile(d["data"])
            elif op == "auto-usb":
                auto.enable(d["enabled"] is True)
            elif op == "connect":
                bench.connect(d["port"], int(d["baud"]), d.get("framing", "8N1"))
                auto.remember(d["port"], int(d["baud"]), d.get("framing", "8N1"))
            elif op == "network":
                await bench.network(d["transport"], d["host"], int(d["port"]))
            elif op == "disconnect":
                if bench.transport == "串口":
                    auto.ignored.add(bench.usb)
                await bench.stop()
                await bench.disconnect()
            elif op == "backup":
                port = d["port"]
                bench.select(port)
                bench.start("Flash 完整备份", lambda: bench.backup(port))
            elif op == "probe":
                port = d["port"]
                bench.select(port)
                bench.start("ESP ROM 探测", lambda: bench.probe(port))
            elif op == "send":
                if bench.job:
                    raise ValueError("检测执行中，稍后再发送。")
                if d.get("hex"):
                    data = bytes.fromhex(d["text"])
                else:
                    data = (
                        d["text"]
                        + {"CRLF": "\r\n", "LF": "\n", "CR": "\r", "无": ""}[
                            d["ending"]
                        ]
                    ).encode()
                bench.send(data)
            elif op == "suite-start":
                profile = d.get("profile", bench.profile)
                if profile != bench.profile:
                    if echo.runner:
                        raise ValueError("切换板型前请先关闭当前回声测试。")
                    await bench.select_profile(profile)
                suite.start(d["mode"])
                await asyncio.sleep(0)
            elif op == "suite-confirm":
                suite.confirm(d)
            elif op == "cases":
                items = [
                    t
                    for t in bench.tests
                    if t["kind"] == "serial"
                    and (d.get("name") == t["name"] or d.get("all") is True)
                ]
                if not items or not bench.ser:
                    raise ValueError("需要已连接设备和可执行命令的测试配置。")
                bench.start("设备应答测试", lambda: bench.cases(items))
            elif op == "stop":
                await bench.stop()
            elif op == "mark":
                if suite.state() and suite.state()["status"] == "运行中":
                    raise ValueError("整板检测进行中，请在当前组记录结果。")
                if not any(
                    t["name"] == d["name"] and t["kind"] == "manual"
                    for t in bench.tests
                ):
                    raise ValueError("只能人工判定查看或听音项目。")
                detail = d.get("detail", "").strip()[:2000]
                if not detail or d["state"] not in ("通过", "失败", "跳过"):
                    raise ValueError("请填写实测现象或跳过原因。")
                bench.result(d["name"], d["state"], "人工记录 · " + detail)
            elif op == "note":
                note = d["text"].strip()[:2000]
                if not note:
                    raise ValueError("备注为空。")
                bench.log("NOTE", "操作员", note)
            elif op == "echo":
                if d["enable"]:
                    port = int(d["port"])
                    if not 1024 <= port <= 65535:
                        raise ValueError("请选择 1024–65535 端口。")
                    await echo.start(d["host"], port)
                else:
                    await echo.stop()
            else:
                raise web.HTTPNotFound()
        return web.json_response({"ok": True})

    app.add_routes(ai.routes())
    app.add_routes(routes)
    app.router.add_static("/static/", ROOT / "web")

    async def startup(app):
        if monitor_usb:
            auto.task = asyncio.create_task(auto.watch())

    app.on_startup.append(startup)

    async def close(app):
        if auto.task:
            auto.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await auto.task
        await echo.stop()
        await bench.stop()
        await bench.disconnect()
        bench.db.close()

    app.on_cleanup.append(close)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT)
    args = parser.parse_args()
    app = make_app(args.data_dir)

    async def launch(app):
        if not args.no_browser:
            asyncio.get_running_loop().call_later(
                0.6, webbrowser.open, f"http://127.0.0.1:{args.port}"
            )

    app.on_startup.append(launch)
    web.run_app(app, host="127.0.0.1", port=args.port, access_log=None)
