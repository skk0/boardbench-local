"""Passive capability discovery and bounded, request-correlated USB screen frames."""

import asyncio
import re
import secrets
import time
import zlib

from aiohttp import web

from boardbench.devices.screen_mirror import jpeg_dimensions


class UsbScreen:
    def __init__(self):
        self.reset()

    def reset(self):
        future = getattr(self, "future", None)
        if future is not None and not future.done():
            future.set_exception(ValueError("USB 会话已结束。"))
        self.future = None
        self.request_id = ""
        self.frame = bytearray()
        self.capability = ""
        self.seen = 0
        self.legacy = ""

    def state(self, connected):
        if not connected:
            return {"status": "offline", "hint": "用数据线连接板子的 USB 串口。首次使用请选择实际端口。"}
        if self.capability == "NO_CAPTURE":
            return {"status": "unsupported", "hint": "固件报告无法截图。请核对是否有屏幕、显示驱动是否实现截图，以及是否启用了快照。"}
        if self.capability == "JPEG" and (self.future is not None or time.monotonic() - self.seen < 12):
            return {"status": "ready", "hint": "已识别 USB 屏幕接口，可直接读取板内画面。"}
        if self.legacy:
            return {"status": "lan", "hint": f"固件报告局域网截图地址 {self.legacy}。可选设备截图地址并输入板上配对码，或适配 USB 取帧。", "address": self.legacy}
        return {"status": "unknown", "hint": "尚未收到 USB 截图能力声明。只有普通日志时无法判断；可为固件接入串口截图接口，或使用已有的局域网截图地址。"}

    def observe(self, line):
        if match := re.search(r"\bScreenMirror: Screen address: ([0-9.]+:[0-9]+)", line):
            self.legacy = match[1]
        if not line.startswith("BBS1 "):
            return False
        if line in ("BBS1 CAP JPEG", "BBS1 CAP NO_CAPTURE"):
            self.capability = line[9:]
            self.seen = time.monotonic()
            return True
        fields = line.split()
        if len(fields) < 3 or self.future is None or self.future.done() or fields[2] != self.request_id:
            return True
        try:
            if len(fields) == 5 and fields[1] == "DATA":
                if int(fields[3]) != len(self.frame) or not re.fullmatch(r"(?:[0-9a-f]{2}){1,128}", fields[4]):
                    raise ValueError()
                part = bytes.fromhex(fields[4])
                if len(self.frame) + len(part) > 65536:
                    raise ValueError()
                self.frame.extend(part)
            elif len(fields) == 5 and fields[1] == "END":
                if int(fields[3]) != len(self.frame) or int(fields[4], 16) != zlib.crc32(self.frame):
                    raise ValueError()
                jpeg_dimensions(self.frame)
                self.seen = time.monotonic()
                self.future.set_result(bytes(self.frame))
            else:
                raise ValueError()
        except ValueError:
            self.future.set_exception(ValueError("板端取帧失败或画面不完整，请检查驱动、可用内存和串口速度。"))
        return True

    async def capture(self, bench, connection):
        state = self.state(bench.ser and bench.transport == "串口")
        if connection != bench.connection_id or state["status"] != "ready":
            raise ValueError("USB 连接已变化，正在重新识别。")
        if self.future is not None or bench.waiter:
            raise web.HTTPConflict(text="正在等待串口应答。")
        self.frame.clear()
        self.request_id = secrets.token_hex(4)
        future = self.future = asyncio.get_running_loop().create_future()
        try:
            command = f"BBS1 GET {self.request_id}\n".encode()
            n = bench.ser.write(command)
            bench.tx += n
            if n != len(command):
                raise ValueError("USB 取帧请求未发送完整。")
            return await asyncio.wait_for(future, 24)
        except asyncio.TimeoutError:
            raise ValueError("USB 取帧超时，请核对双向串口、波特率和板端截图实现。") from None
        finally:
            if self.future is future:
                self.future = None
                self.request_id = ""
                self.frame.clear()
