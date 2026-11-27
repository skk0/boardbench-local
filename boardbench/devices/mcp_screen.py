"""Read the actual display via a Xiaozhi device's advertised MCP screenshot tool."""

import asyncio
import secrets

from aiohttp import web

from boardbench.devices.screen_mirror import MAX_FRAME, jpeg_dimensions


class McpScreen:
    def __init__(self, send, upload_base, modern=False):
        self.modern = modern
        self.send = send
        self.upload_base = upload_base
        self.supported = None
        self.sequence = 0
        self.pending = {}
        self.ticket = None
        self.pixels = None
        self.closed = False

    def close(self):
        self.closed = True
        self.ticket = None
        for future in [*self.pending.values(), self.pixels]:
            if future is not None and not future.done():
                future.set_result(None)

    def receive(self, packet):
        if not isinstance(packet, dict) or type(packet.get("id")) is not int:
            return
        future = self.pending.get(packet["id"])
        if future is not None and not future.done():
            future.set_result(packet)

    async def rpc(self, method, params):
        if self.closed:
            raise ValueError("设备会话已断开，请重新连接。")
        if self.modern:
            params = {**params, "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "BoardBench", "version": "1"},
            }}
        self.sequence += 1
        request_id = self.sequence
        reply = self.pending[request_id] = asyncio.get_running_loop().create_future()
        try:
            await self.send("mcp", payload=dict(jsonrpc="2.0", id=request_id, method=method, params=params))
            response = await asyncio.wait_for(reply, 7)
        finally:
            self.pending.pop(request_id, None)
        if response is None:
            raise ValueError("设备会话已断开，请重新连接。")
        result = response.get("result")
        if "error" in response or not isinstance(result, dict) or result.get("isError"):
            raise ValueError("设备未完成截图协议请求，请核对固件和屏幕状态。")
        return result

    async def discover(self):
        if not self.modern:
            await self.rpc("initialize", dict(protocolVersion="2024-11-05", capabilities={},
                                              clientInfo=dict(name="BoardBench", version="1")))
        seen = set()
        params = {"withUserTools": True}
        for _ in range(64):
            page = await self.rpc("tools/list", params)
            tools = page.get("tools")
            if not isinstance(tools, list):
                raise ValueError("固件返回的工具列表无效。")
            if any(isinstance(tool, dict) and tool.get("name") == "self.screen.snapshot" for tool in tools):
                return True
            cursor = page.get("nextCursor")
            if not cursor:
                return False
            if not isinstance(cursor, str) or cursor in seen or len(cursor) > 512:
                break
            seen.add(cursor)
            params["cursor"] = cursor
        raise ValueError("设备的截图能力列表未能完整读取，请重新连接。")

    async def capture(self):
        if self.supported is None:
            self.supported = await asyncio.wait_for(self.discover(), 25)
        if not self.supported:
            raise ValueError("当前固件未提供屏幕截图工具。若无局域网截图接口，需要适配板端显示驱动或开启截图功能。")
        self.ticket = secrets.token_urlsafe(24)
        self.pixels = asyncio.get_running_loop().create_future()
        try:
            await self.rpc("tools/call", dict(name="self.screen.snapshot", arguments={
                "url": self.upload_base + "/" + self.ticket, "quality": 65,
            }))
            pixels = await asyncio.wait_for(self.pixels, 4)
            if pixels is None:
                raise ValueError("设备会话已断开，请重新连接。")
            return pixels
        finally:
            self.ticket = None
            self.pixels = None

    async def accept(self, request):
        ticket = request.match_info["ticket"]
        if self.ticket is None or ticket != self.ticket:
            raise web.HTTPNotFound()
        self.ticket = None
        waiting = self.pixels

        async def read():
            multipart = await request.multipart()
            part = await multipart.next()
            if part is None or getattr(part, "name", None) != "file":
                raise ValueError("需要截图文件字段。")
            data = bytearray()
            while chunk := await part.read_chunk(16384):
                if len(data) + len(chunk) > MAX_FRAME:
                    raise web.HTTPRequestEntityTooLarge(max_size=MAX_FRAME, actual_size=len(data) + len(chunk))
                data.extend(chunk)
            jpeg_dimensions(data)
            return bytes(data)

        try:
            data = await asyncio.wait_for(read(), 5)
        except (ValueError, AssertionError, asyncio.TimeoutError):
            raise web.HTTPBadRequest(text="截图无效或超时") from None
        if waiting is None or waiting is not self.pixels or waiting.done():
            raise web.HTTPGone()
        waiting.set_result(data)
        return web.json_response({"ok": True})
