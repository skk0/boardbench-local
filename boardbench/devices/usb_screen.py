"""Passive capability discovery and bounded, request-correlated USB screen frames."""

import asyncio
import re
import secrets
import time
import zlib

from aiohttp import web

from boardbench.devices.lbsnap import LbSnapDecoder
from boardbench.devices.screen_mirror import jpeg_dimensions


class SerialReplyFilter:
    """Keep screenshot lines out of test replies while streaming unframed binary replies immediately."""

    prefixes = (b"BBS1 ", b"LBSNAP:")

    def __init__(self):
        self.prefix = b""
        self.mode = "start"

    def feed(self, data):
        output = bytearray()
        for block in data.splitlines(keepends=True):
            if self.mode == "start":
                block = self.prefix + block
                self.prefix = b""
                if block.startswith(self.prefixes):
                    self.mode = "screen"
                elif any(prefix.startswith(block) for prefix in self.prefixes):
                    self.prefix = block
                    continue
                else:
                    self.mode = "reply"
            if self.mode == "reply":
                output.extend(block)
            if block.endswith(b"\n"):
                self.mode = "start"
        return output

    def flush_prefix(self):
        prefix, self.prefix = self.prefix, b""
        if prefix:
            self.mode = "reply"
        return prefix


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
        self.snap = LbSnapDecoder()
        self.snap_command = False
        self.snap_push = False
        self.latest = None
        self.request_protocol = ""

    def protocol(self):
        if self.capability == "JPEG" and (self.request_protocol == "BBS1" or time.monotonic() - self.seen < 12):
            return "BBS1"
        if self.snap_command:
            return "snap"
        if self.snap_push:
            return "LBSNAP"
        return ""

    def state(self, connected):
        if not connected:
            return {"status": "offline", "hint": "用数据线连接板子的 USB 串口。首次使用请选择实际端口。"}
        protocol = self.protocol()
        hints = {
            "BBS1": "已识别 BBS1，可通过 USB 读取板内画面。",
            "snap": "已识别 snap 截图控制台，可通过 USB 读取板内画面。",
            "LBSNAP": "已收到设备推送的 LBSNAP 画面，正在监听后续截图。",
        }
        if protocol:
            return {"status": "ready", "protocol": protocol, "hint": hints[protocol]}
        if self.capability == "NO_CAPTURE":
            return {"status": "unsupported", "hint": "固件报告无法截图。请核对是否有屏幕、显示驱动是否实现截图，以及是否启用了快照。"}
        if self.legacy:
            return {"status": "lan", "hint": f"固件报告局域网截图地址 {self.legacy}。可选设备截图地址并输入板上配对码，或适配 USB 取帧。", "address": self.legacy}
        return {"status": "unknown", "hint": "尚未收到 USB 截图能力声明。只有普通日志时无法判断。已知固件支持 snap 可选择该来源；有网络截图接口可选择设备截图地址。"}

    def observe(self, line):
        if match := re.search(r"\bScreenMirror: Screen address: ([0-9.]+:[0-9]+)", line):
            self.legacy = match[1]
        if "Debug console ready" in line:
            plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
            if re.fullmatch(r"(?:I \(\d+\) )?LifeBoxDisplay: Debug console ready", plain):
                self.snap_command = True
        if line.startswith("LBSNAP:"):
            if line == "LBSNAP:READY snap":
                self.snap_command = True
                return True
            receiving = self.future is not None and not self.future.done() and self.request_protocol in ("snap", "LBSNAP")
            try:
                image = self.snap.consume(line[7:])
                if image is not None:
                    self.snap_push = True
                    if receiving:
                        self.future.set_result(image)
                    else:
                        self.latest = image
            except ValueError as exc:
                self.snap.clear()
                if receiving:
                    self.future.set_exception(ValueError(f"LBSNAP 画面无效：{exc}"))
            return True
        if not line.startswith("BBS1 "):
            return False
        if line in ("BBS1 CAP JPEG", "BBS1 CAP NO_CAPTURE"):
            self.capability = line[9:]
            self.seen = time.monotonic()
            return True
        fields = line.split()
        if len(fields) < 3 or self.future is None or self.future.done() or self.request_protocol != "BBS1" or fields[2] != self.request_id:
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

    async def capture(self, bench, connection, *, manual_snap=False):
        if connection != bench.connection_id or not bench.ser or bench.transport != "串口":
            raise ValueError("USB 连接已变化，正在重新识别。")
        protocol = "snap" if manual_snap else self.protocol()
        if not protocol:
            raise ValueError("尚未识别到可读取的 USB 截图协议。")
        if self.future is not None or bench.waiter:
            raise web.HTTPConflict(text="正在等待串口应答。")
        if protocol == "LBSNAP" and self.latest is not None:
            image, self.latest = self.latest, None
            return image
        self.frame.clear()
        self.request_protocol = protocol
        future = self.future = asyncio.get_running_loop().create_future()
        try:
            if protocol != "LBSNAP":
                if protocol == "BBS1":
                    self.request_id = secrets.token_hex(4)
                    command = f"BBS1 GET {self.request_id}\n".encode()
                else:
                    self.snap.clear()
                    command = b"snap\n"
                n = bench.ser.write(command)
                bench.tx += n
                if n != len(command):
                    raise ValueError("USB 取帧请求未发送完整。")
            return await asyncio.wait_for(future, 24)
        except asyncio.TimeoutError:
            if protocol == "LBSNAP":
                return None  # No new pushed frame during this poll.
            raise ValueError("USB 取帧超时，请核对双向串口、波特率和板端截图实现。") from None
        finally:
            if self.future is future:
                self.future = None
                self.request_id = ""
                self.request_protocol = ""
                self.frame.clear()
