"""Optional LAN-only Xiaozhi audio echo endpoint. Audio stays in memory."""

import asyncio
import json
import secrets
import socket
from aiohttp import web, WSMsgType


class Echo:
    def __init__(self, bench):
        self.bench = bench
        self.runner = self.ws = None
        self.url = self.device = ""
        self.frames = 0
        self.checkin = None

    @staticmethod
    def address():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(("192.0.2.1", 9))
                return s.getsockname()[0]
            except OSError:
                return "127.0.0.1"

    async def start(self, host, port):
        if self.runner:
            raise ValueError("回声监听已启动。")
        import ipaddress

        ip = ipaddress.IPv4Address(host)
        if not (ip.is_private and not ip.is_unspecified and not ip.is_multicast):
            raise ValueError("请选择本机的局域网 IPv4 地址。")
        key = secrets.token_urlsafe(14)
        self.base = f"http://{ip}:{port}"
        self.route = f"/device/{key}"
        app = web.Application(client_max_size=128 * 1024)
        app.router.add_post(self.route + "/ota/", self.ota)
        app.router.add_get(self.route + "/ws", self.socket)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, str(ip), port).start()
        except BaseException:
            await runner.cleanup()
            raise
        self.runner = runner
        self.url = self.base + self.route + "/ota/"
        self.device = ""
        self.frames = 0
        self.bench.log("INFO", "小智", "局域网回声监听已启动 · " + self.base)

    async def ota(self, request):
        try:
            payload = await request.json()
        except (ValueError, RecursionError):
            raise web.HTTPBadRequest(text="签到信息需要 JSON 对象")
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="签到信息需要 JSON 对象")
        board = payload.get("board", {})
        app = payload.get("application", {})
        if not isinstance(board, dict) or not isinstance(app, dict):
            raise web.HTTPBadRequest(text="board 和 application 需要是 JSON 对象")
        device = request.headers.get("Device-Id", "")
        if len(device) > 128:
            raise web.HTTPBadRequest(text="设备标识过长")
        if self.device and self.device != device:
            raise web.HTTPConflict(text="测试会话已绑定其他设备")
        self.device = device
        fields = {}
        for label, value in (
            ("板型", board.get("type")), ("设备名称", board.get("name")),
            ("芯片", payload.get("chip_model_name")), ("应用", app.get("name")),
            ("固件版本", app.get("version")),
        ):
            if isinstance(value, str) and 0 < len(value) <= 96 and value.isprintable() and value.strip():
                fields[label] = value.strip()
        summary = " · ".join(f"{label} {value}" for label, value in fields.items())
        log_id = self.bench.log("INFO", "小智", "设备签到 · " + (summary or "未提供型号信息"))
        self.checkin = dict(run=self.bench.run, log_id=log_id, fields=fields)
        return web.json_response(
            {
                "websocket": {
                    "url": self.base.replace("http:", "ws:") + self.route + "/ws",
                    "version": 1,
                    "token": "",
                }
            }
        )

    async def socket(self, request):
        if self.ws or (self.device and request.headers.get("Device-Id") != self.device):
            raise web.HTTPConflict()
        ws = web.WebSocketResponse(max_msg_size=65536, heartbeat=25)
        self.ws = ws
        sid = secrets.token_hex(8)
        frames = []
        ready = False
        recording = False
        ms = 60

        async def emit(kind, **fields):
            await ws.send_json(dict(type=kind, session_id=sid, **fields))

        async def play():
            nonlocal recording
            recording = False
            count = len(frames)
            if not count:
                self.bench.log("WARN", "小智", "录音结束，收到 0 帧。")
                return
            await emit("tts", state="start")
            await emit("tts", state="sentence_start", text="板卡测试台 · 录音回放")
            for frame in frames:
                await ws.send_bytes(frame)
                await asyncio.sleep(ms / 1000)
            await emit("tts", state="stop")
            frames.clear()
            self.bench.result(
                "局域网音频往返",
                "通过",
                f"收到并回送 {count} 个 Opus 帧；声音质量请在外设面板确认。",
            )

        try:
            await ws.prepare(request)
            self.bench.log("INFO", "小智", "WebSocket 已连接 · " + str(request.remote))
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    m = json.loads(message.data)
                    kind = m.get("type")
                    if kind == "hello":
                        a = m.get("audio_params", {})
                        ms = a.get("frame_duration", 60)
                        if (
                            m.get("version", 1) != 1
                            or a.get("format") != "opus"
                            or ms not in (10, 20, 40, 60)
                            or a.get("sample_rate")
                            not in (8000, 12000, 16000, 24000, 48000)
                            or a.get("channels", 1) != 1
                        ):
                            raise ValueError(
                                "当前适配器需要 WebSocket v1、单声道 Opus。"
                            )
                        await emit(
                            "hello", version=1, transport="websocket", audio_params=a
                        )
                        ready = True
                        self.bench.log(
                            "INFO",
                            "小智",
                            f"握手完成 · Opus {a['sample_rate']} Hz · {ms} ms/帧",
                        )
                    elif kind == "listen" and ready:
                        state = m.get("state")
                        self.bench.log("INFO", "小智", "listen · " + str(state))
                        if state == "start":
                            frames.clear()
                            recording = True
                        elif state == "stop" and recording:
                            await play()
                    elif kind == "abort":
                        frames.clear()
                        recording = False
                        self.bench.log("WARN", "小智", "设备中断本轮录音")
                elif message.type == WSMsgType.BINARY and ready and recording:
                    if not 0 < len(message.data) <= 4096:
                        raise ValueError("音频帧长度异常")
                    frames.append(message.data)
                    self.frames += 1
                    if len(frames) * ms >= 3000:
                        await play()
        except (ValueError, TypeError, ConnectionError, AttributeError) as exc:
            self.bench.log("ERROR", "小智", "回声连接中断 · " + str(exc))
        finally:
            await ws.close()
            self.ws = None
            self.bench.log("INFO", "小智", "WebSocket 已断开")
        return ws

    async def stop(self):
        if self.ws is not None:
            await self.ws.close()
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
            self.url = ""
            self.checkin = None
            self.device = ""
            self.bench.log("INFO", "小智", "局域网回声监听已关闭")
