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
import urllib.parse
import webbrowser

from aiohttp import web

from boardbench.bench import Bench
from boardbench.checks.profiles import validate
from boardbench.checks.replies import try_sample
from boardbench.checks.suite import Suite, make_plan
from boardbench.devices import firmware, screen_mirror
from boardbench.devices.echo import Echo
from boardbench.devices.flasher import Flasher
from boardbench.devices.monitor import AutoUSB
from boardbench.reports import faultpack, replay, vcd_export
from boardbench.reports.ai_analysis import Analysis

ROOT = Path(__file__).resolve().parents[1]
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
    flash = Flasher(bench)
    faultpack.install(bench.db)

    @web.middleware
    async def policy(req, handler):
        if req.url.host not in {"127.0.0.1", "localhost", "::1"}:
            raise web.HTTPForbidden()
        if req.method == "POST" and req.headers.get("X-BoardBench") != token:
            raise web.HTTPForbidden()
        try:
            response = await handler(req)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            source = "AI 接口" if req.path.startswith("/api/ai/") else "操作"
            bench.log("ERROR", source, f"{req.path} · {exc}")
            return web.json_response({"error": str(exc)}, status=400)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    app = web.Application(middlewares=[policy], client_max_size=256 * 1024)
    routes = web.RouteTableDef()
    screen_mirror.install(routes, echo.capture_screen, lambda conn: bench.usb_screen.capture(bench, conn))

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
        return web.json_response(auto.describe(await asyncio.to_thread(bench.ports)))

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
                connection_id=bench.connection_id,
                usb_screen=bench.usb_screen.state(bench.ser and bench.transport == "串口"),
                transport=bench.transport,
                port=bench.usb,
                baud=bench.baud,
                framing=bench.framing,
                network_host=bench.network_host,
                network_port=bench.network_port,
                job=bench.taskname,
                suite=suite.state(),
                storage_hold=bench.storage_hold,
                device_identity=bench.components.identity if bench.ser else {},
                components=bench.components.states(bench.profile, bool(bench.ser)),
                auto_usb=auto.enabled,
                usb_message=auto.message,
                remembered_usb=auto.known(),
                endurance=bench.endurance.active and {k:bench.endurance.active[k] for k in ("id","started","minutes","variant")},
                usb_ports=auto.describe(auto.online),
                rx=bench.rx,
                tx=bench.tx,
                logs=logs,
                echo=echo.url,
                echo_checkin=echo.checkin,
                frames=echo.frames,
            )
        )

    @routes.get("/api/smart")
    async def smart(req):
        return web.json_response(bench.smart_state())

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
            data["smart_reports"] = bench.smart_history(data["id"])
            if data["id"] == bench.run and bench.ser:
                data["smart_live"] = bench.smart_state()
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
            smart_reports = list(data["smart_reports"])
            if "smart_live" in data:
                smart_reports.append(dict(reason="导出时的实时观测", at="", **data["smart_live"]))
            labels = {"verified": "已验证", "fault": "异常", "unknown": "证据不足", "observe": "待观察", "absent": "固件声明未安装"}
            for snapshot in smart_reports:
                rows.extend(["", f"智能检测 · {snapshot['reason']}  {snapshot['at']}"])
                for item in snapshot["rows"]:
                    ref = f"  L{item['log_id']}" if item['log_id'] else ""
                    rows.append(f"{labels[item['status']]}  {item['name']}  {item['detail']}{ref}")
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

    @routes.get("/api/flash")
    async def flash_state(req):
        return web.json_response(flash.state())

    @routes.post("/api/flash/open")
    async def flash_open(req):
        if req.content_type == "application/json":
            path = (await req.json()).get("path", "")
            if not isinstance(path, str) or not path.strip():
                raise ValueError("请选择固件文件或文件夹。")
            args = dict(path=path.strip())
        else:
            data = await req.clone(client_max_size=firmware.MAX_ZIP_TOTAL).read()
            name = urllib.parse.unquote(req.headers.get("X-File-Name", "firmware.bin"))[:200]
            args = dict(name=name, data=data)
        async with lock:
            fw = await asyncio.to_thread(firmware.load, args["path"]) if "path" in args else \
                await asyncio.to_thread(firmware.load_bytes, args["name"], args["data"])
            flash.open(loaded=fw)
        return web.json_response(flash.state())

    @routes.get("/api/endurance")
    async def endurance_history(req):
        return web.json_response(bench.endurance.history())

    @routes.get("/api/fault-pack/{run}")
    async def fault_preview(req):
        return web.json_response(await faultpack.excerpt(bench, int(req.match_info["run"])))

    @routes.post("/api/fault-pack/import")
    async def import_pack(req):
        payload = await req.clone(client_max_size=faultpack.LIMIT).json()
        async with lock:
            return web.json_response({"run": faultpack.read_pack(bench, payload)})

    @routes.post("/api/profile/sample")
    async def profile_sample(req):
        data = await req.json()
        _, tests = validate({"name":"试跑","tests":[data["test"]]})
        return web.json_response({"outcome":try_sample(tests[0], data["sample"])})

    @routes.post("/api/{op}")
    async def command(req):
        d = await req.json()
        op = req.match_info["op"]
        async with lock:
            if d.get("run") != bench.run:
                raise ValueError("记录已经换了一条，请刷新页面再试。")
            if op == "new":
                if echo.runner:
                    raise ValueError("请先结束语音回声测试。")
                await bench.select_profile(d["profile"])
            elif op == "import":
                bench.add_profile(d["data"])
            elif op == "cadence":
                bench.components.set_rule(d["part"], d["mode"], d.get("seconds"))
            elif op == "usb-name":
                auto.rename(d["identity"], d["name"])
            elif op == "endurance-start":
                bench.endurance.start(bench, d["variant"], d["minutes"])
            elif op == "endurance-stop":
                bench.endurance.stop()
            elif op == "endurance-reference":
                bench.endurance.baseline(d["record"])
            elif op == "auto-usb":
                auto.enable(d["enabled"] is True)
            elif op == "connect":
                if bench.usb and not bench.ser and not bench.job:
                    if echo.runner:
                        raise ValueError("请先结束语音回声测试再换板子。")
                    await bench.select_profile(bench.profile if bench.usb == d["port"] else "通用串口设备")
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
            elif op == "flash-inspect":
                bench.select(d["port"])
                bench.start("刷固件 · 识别和检查", lambda: flash.inspect(d["port"]))
            elif op == "flash-factory":
                flash.set_factory(d["value"] is True)
            elif op == "flash-start":
                bench.select(d["port"])
                bench.start("刷固件 · 备份和刷写", lambda: flash.flash(
                    d["port"], d.get("confirm_board") is True, d.get("full_backup") is True))
            elif op == "flash-restore":
                bench.select(d["port"])
                bench.start("刷固件 · 恢复备份", lambda: flash.restore(d["port"], d["backup"]))
            elif op == "flash-close":
                flash.close()
            elif op == "probe":
                port = d["port"]
                bench.select(port)
                bench.start("ESP ROM 探测", lambda: bench.probe(port))
            elif op == "send":
                if bench.job:
                    raise ValueError("正在自动检查，稍后再发。")
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
            elif op == "smart-report":
                return web.json_response(dict(ok=True, **bench.save_smart("保存当前结果")))
            elif op == "suite-start":
                profile = d.get("profile", bench.profile)
                if profile != bench.profile:
                    if echo.runner:
                        raise ValueError("换板子型号前请先结束语音回声测试。")
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
                    raise ValueError("需要先连上板子，而且检查清单里有能发命令的项目。")
                bench.start("设备应答测试", lambda: bench.cases(items))
            elif op == "stop":
                if bench.taskname.startswith("刷固件"):
                    flash.request_stop()
                else:
                    await bench.stop()
            elif op == "mark":
                if suite.state() and suite.state()["status"] == "运行中":
                    raise ValueError("人工检查正在进行，请在当前这一组里记结果。")
                if not any(
                    t["name"] == d["name"] and t["kind"] == "manual"
                    for t in bench.tests
                ):
                    raise ValueError("只能人工判定查看或听音项目。")
                detail = d.get("detail", "").strip()[:2000]
                if not detail or d["state"] not in ("通过", "失败", "跳过"):
                    raise ValueError("请写一下看到的情况或跳过的原因。")
                bench.result(d["name"], d["state"], "人工记录 · " + detail)
            elif op == "note":
                note = d["text"].strip()[:2000]
                if not note:
                    raise ValueError("备注是空的。")
                bench.log("NOTE", "操作员", note)
            elif op == "echo":
                if d["enable"]:
                    port = int(d["port"])
                    if not 1024 <= port <= 65535:
                        raise ValueError("端口号请选 1024–65535。")
                    await echo.start(d["host"], port)
                else:
                    await echo.stop()
            else:
                raise web.HTTPNotFound()
        return web.json_response({"ok": True})

    app.add_routes(replay.routes(bench))
    app.add_routes(vcd_export.routes(bench))
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--browser", action="store_true", help="open in the system browser")
    parser.add_argument("--desktop-smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT)
    args = parser.parse_args()
    if sys.platform in ("darwin", "win32") and not (args.browser or args.no_browser):
        from boardbench.desktop import run_window, startup_error
        try:
            run_window(args.port, lambda: make_app(args.data_dir, monitor_usb=not args.desktop_smoke), args.desktop_smoke)
        except Exception as exc:
            if args.desktop_smoke:
                raise
            startup_error(str(exc))
            sys.exit(1)
        sys.exit(0)

    app = make_app(args.data_dir)

    async def launch(app):
        if not args.no_browser:
            asyncio.get_running_loop().call_later(
                0.6, webbrowser.open, f"http://127.0.0.1:{args.port}"
            )

    app.on_startup.append(launch)
    web.run_app(app, host="127.0.0.1", port=args.port, access_log=None, print=None)
