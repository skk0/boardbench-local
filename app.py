"""BoardBench local control plane."""
import argparse
import asyncio
import csv
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
from bench import Bench
from echo import Echo

ROOT=Path(__file__).resolve().parent
DEFAULT=Path.home()/".boardbench"
def make_app(directory):
    directory.mkdir(parents=True,exist_ok=True)
    bench=Bench(directory/"history.sqlite3")
    echo=Echo(bench)
    token=secrets.token_urlsafe(24)
    lock=asyncio.Lock()

    @web.middleware
    async def policy(req,handler):
        if req.url.host not in {"127.0.0.1","localhost","::1"}:
            raise web.HTTPForbidden()
        if req.method=="POST" and req.headers.get("X-BoardBench")!=token:
            raise web.HTTPForbidden()
        try:
            response=await handler(req)
        except (ValueError,KeyError,TypeError,OSError) as exc:
            bench.log("ERROR","操作",f"{req.path} · {exc}")
            return web.json_response({"error":str(exc)},status=400)
        response.headers["Cache-Control"]="no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="DENY"
        return response
    app=web.Application(middlewares=[policy],client_max_size=256*1024)
    routes=web.RouteTableDef()

    @routes.get("/")
    async def index(req):
        return web.FileResponse(ROOT/"web/index.html")

    @routes.get("/api/setup")
    async def setup(req):
        return web.json_response(dict(token=token,profiles=list(bench.profiles()),ip=Echo.address()))

    @routes.get("/api/ports")
    async def ports(req):
        return web.json_response(await asyncio.to_thread(bench.ports))

    @routes.get("/api/state")
    async def state(req):
        results=bench.rows("SELECT name,state,detail,at FROM results WHERE run=?",(bench.run,))
        logs=bench.rows("SELECT * FROM (SELECT * FROM logs WHERE run=? ORDER BY id DESC LIMIT 350) ORDER BY id",(bench.run,))
        return web.json_response(dict(run=bench.run,profile=bench.profile,tests=bench.tests,results=results,connected=bool(bench.ser),
            port=bench.usb,baud=bench.baud,job=bench.taskname,rx=bench.rx,tx=bench.tx,logs=logs,echo=echo.url,frames=echo.frames))

    @routes.get("/api/history")
    async def history(req):
        return web.json_response(bench.rows("SELECT id,started,profile,port FROM runs ORDER BY id DESC"))

    @routes.get("/api/template")
    async def template(req):
        name=req.query.get("name",bench.profile)
        return web.json_response(dict(name=name,tests=bench.profiles()[name]),
            headers={"Content-Disposition":'attachment; filename="board-profile.json"'})

    @routes.get("/api/report/{run}/{fmt}")
    async def report(req):
        data=bench.report(int(req.match_info["run"]))
        fmt=req.match_info["fmt"]
        if fmt=="json":
            body=json.dumps(data,ensure_ascii=False,indent=2)
            mime="application/json"
        elif fmt=="csv":
            f=io.StringIO()
            w=csv.writer(f)
            w.writerow(["UTC time","level","source","message","hex"])
            for row in data["logs"]:
                # Prevent spreadsheet applications from executing data received from devices.
                fields=[row[k] for k in ("at","level","source","message","raw")]
                w.writerow(["'"+v if v.lstrip().startswith(("=","+","-","@")) else v for v in fields])
            body="\ufeff"+f.getvalue()
            mime="text/csv"
        elif fmt=="txt":
            byname={r["name"]:r for r in data["results"]}
            names=list(dict.fromkeys([t["name"] for t in data["tests"]]+list(byname)))
            rows=[f"BoardBench #{data['id']} · {data['profile']} · {data['port']}",data["started"],"","检测结果"]
            for name in names:
                r=byname.get(name,dict(state="未测",detail=""))
                rows.append(f"{r['state']}  {name}  {r['detail']}")
            rows+=["","事件日志 · UTC"]+[f"{r['at']} [{r['level']}] {r['source']} | {r['message']}" for r in data["logs"]]
            body="\n".join(rows)
            mime="text/plain"
        else:
            raise web.HTTPNotFound()
        return web.Response(text=body,content_type=mime,headers={"Content-Disposition":f'attachment; filename="boardbench-{data["id"]}.{fmt}"'})

    @routes.post("/api/{op}")
    async def command(req):
        d=await req.json()
        op=req.match_info["op"]
        async with lock:
            if d.get("run")!=bench.run:
                raise ValueError("测试会话已更新，请刷新页面。")
            if op=="new":
                if echo.runner:
                    raise ValueError("请先停止局域网回声。")
                bench.new(d["profile"])
            elif op=="import":
                bench.add_profile(d["data"])
            elif op=="connect":
                bench.connect(d["port"],int(d["baud"]),d.get("framing","8N1"))
            elif op=="disconnect":
                await bench.stop()
                await bench.disconnect()
            elif op=="probe":
                port=d["port"]
                bench.select(port)
                bench.start("ESP ROM 探测",lambda:bench.probe(port))
            elif op=="send":
                if bench.job:
                    raise ValueError("检测执行中，稍后再发送。")
                if d.get("hex"):
                    data=bytes.fromhex(d["text"])
                else:
                    data=(d["text"]+{"CRLF":"\r\n","LF":"\n","CR":"\r","无":""}[d["ending"]]).encode()
                bench.send(data)
            elif op=="cases":
                items=[t for t in bench.tests if t["kind"]=="serial" and (d.get("name")==t["name"] or d.get("all") is True)]
                if not items or not bench.ser:
                    raise ValueError("需要已连接串口和可执行命令的测试配置。")
                bench.start("串口应答测试",lambda:bench.cases(items))
            elif op=="stop":
                await bench.stop()
            elif op=="mark":
                if not any(t["name"]==d["name"] and t["kind"]=="manual" for t in bench.tests):
                    raise ValueError("只能人工判定查看或听音项目。")
                detail=d.get("detail","").strip()[:2000]
                if not detail or d["state"] not in ("通过","失败","跳过"):
                    raise ValueError("请填写实测现象或跳过原因。")
                bench.result(d["name"],d["state"],"人工记录 · "+detail)
            elif op=="note":
                note=d["text"].strip()[:2000]
                if not note:
                    raise ValueError("备注为空。")
                bench.log("NOTE","操作员",note)
            elif op=="echo":
                if d["enable"]:
                    port=int(d["port"])
                    if not 1024<=port<=65535:
                        raise ValueError("请选择 1024–65535 端口。")
                    await echo.start(d["host"],port)
                else:
                    await echo.stop()
            else:
                raise web.HTTPNotFound()
        return web.json_response({"ok":True})
    app.add_routes(routes)
    app.router.add_static("/static/",ROOT/"web")
    async def close(app):
        await echo.stop()
        await bench.stop()
        await bench.disconnect()
        bench.db.close()
    app.on_cleanup.append(close)
    return app

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--no-browser",action="store_true")
    parser.add_argument("--data-dir",type=Path,default=DEFAULT)
    args=parser.parse_args()
    app=make_app(args.data_dir)
    async def launch(app):
        if not args.no_browser:
            asyncio.get_running_loop().call_later(.6,webbrowser.open,f"http://127.0.0.1:{args.port}")
    app.on_startup.append(launch)
    web.run_app(app,host="127.0.0.1",port=args.port,access_log=None)
