"""Pull one JPEG or PNG framebuffer from a board explicitly selected on the local network."""

import asyncio
import ipaddress
import re
import struct
import zlib

import aiohttp
from aiohttp import web

LAN = tuple(ipaddress.IPv4Network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
MAX_FRAME = 1024 * 1024


def destination(address, path="/screen.jpg"):
    match = re.fullmatch(r"([0-9.]{7,15})(?::([0-9]{1,5}))?", str(address).strip())
    if not match:
        raise ValueError("请输入板子的局域网 IPv4 地址，可附端口号。")
    try:
        ip = ipaddress.IPv4Address(match[1])
    except ValueError:
        raise ValueError("板子 IP 地址的格式有误。") from None
    port = int(match[2] or 8088)
    if not any(ip in network for network in LAN) or not 1 <= port <= 65535:
        raise ValueError("需要局域网内的板子地址和有效端口。")
    if not isinstance(path, str) or len(path) > 128 or not re.fullmatch(
        r"/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+[.](?:jpg|jpeg|png)", path
    ):
        raise ValueError("请输入固件提供的截图路径，例如 /screen.jpg 或 /display/screen.png。")
    return f"http://{ip}:{port}{path}"


def jpeg_dimensions(data):
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("设备没有返回完整的 JPEG 屏幕画面。")
    pos = 2
    while pos + 4 <= len(data) and data[pos] == 255:
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos + 3 > len(data) or data[pos] in (0xD9, 0xDA):
            break
        marker = data[pos]
        size = int.from_bytes(data[pos + 1:pos + 3], "big")
        if size < 2 or pos + 1 + size > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2) and size >= 8:
            height = int.from_bytes(data[pos + 4:pos + 6], "big")
            width = int.from_bytes(data[pos + 6:pos + 8], "big")
            if 0 < width <= 4096 and 0 < height <= 4096 and width * height <= 4_194_304:
                return width, height
            break
        pos += 1 + size
    raise ValueError("JPEG 尺寸无效或超出投屏上限。")


PNG = b"\x89PNG\r\n\x1a\n"


def png_dimensions(data):
    if len(data) < 33 or not data.startswith(PNG) or data[8:16] != b"\x00\x00\x00\x0dIHDR":
        raise ValueError("PNG 文件头无效。")
    width, height = struct.unpack_from(">II", data, 16)
    if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= 4_194_304):
        raise ValueError("PNG 尺寸超出投屏上限。")
    pos, has_pixels = 8, False
    while pos + 12 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        end = pos + 8 + size
        kind = data[pos + 4:pos + 8]
        if end + 4 > len(data) or zlib.crc32(data[pos + 4:end]) != int.from_bytes(data[end:end + 4], "big"):
            break
        if kind in (b"acTL", b"iCCP", b"iTXt", b"zTXt"):
            raise ValueError("请让固件返回无压缩元数据的单帧 PNG。")
        has_pixels |= kind == b"IDAT" and size > 0
        if kind == b"IEND":
            if has_pixels and size == 0 and end + 4 == len(data):
                return width, height
            break
        pos = end + 4
    raise ValueError("设备没有返回完整的 PNG 屏幕画面。")


async def read_frame(url, code):
    headers = {"Accept": "image/jpeg, image/png", "Accept-Encoding": "identity"}
    if code:
        headers["Authorization"] = "Bearer " + code
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6), auto_decompress=False) as client:
        async with client.get(url, headers=headers, allow_redirects=False) as response:
            if response.status in {401, 403, 410}:
                raise ValueError("配对码有误或投屏已关闭，请核对板上的配对码。")
            if response.status == 429:
                raise ValueError("板子正在采集画面，请稍后重试。")
            if response.status != 200:
                raise ValueError(f"板子返回 HTTP {response.status}，请确认固件支持屏幕投屏。")
            if response.content_type not in ("image/jpeg", "image/png"):
                raise ValueError("设备返回的内容不是 JPEG 或 PNG 屏幕画面。")
            frame = bytearray()
            async for chunk in response.content.iter_chunked(16384):
                if len(frame) + len(chunk) > MAX_FRAME:
                    raise ValueError("单帧超过 1 MiB。")
                frame.extend(chunk)
            if response.content_type == "image/png":
                png_dimensions(frame)
            else:
                jpeg_dimensions(frame)
            return bytes(frame)


def install(routes):
    gate = asyncio.Lock()

    @routes.post("/api/screen/frame")
    async def frame(req):
        try:
            data = await req.json()
            if not isinstance(data, dict):
                raise ValueError("投屏参数无效。")
            code = data.get("code", "")
            if not isinstance(code, str) or (code and not re.fullmatch(r"[0-9]{6}", code)):
                raise ValueError("配对码应为六位数字。")
            url = destination(data.get("address", ""), data.get("path", "/screen.jpg"))
            if gate.locked():
                return web.json_response({"error": "正在读取上一帧。"}, status=429)
            async with gate:
                body = await read_frame(url, code)
            return web.Response(body=body, content_type="image/png" if body.startswith(PNG) else "image/jpeg")
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError):
            return web.json_response({"error": "未收到板子画面。请检查 Wi-Fi、板子地址和投屏开关。"}, status=502)
