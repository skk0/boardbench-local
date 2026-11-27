"""Recognize explicit identification lines received from the device."""

import re

ROM = re.compile(r"ESP-ROM:esp32(s3|c3|c6)(?:-api[0-9]+)?-[0-9]{8}")
PYTHON = re.compile(
    r"(?P<runtime>MicroPython(?: \(with v2\.0 preview\))?|Adafruit CircuitPython) "
    r"(?P<version>v?[0-9][0-9A-Za-z.+_-]{0,63}) on [0-9]{4}-[0-9]{2}-[0-9]{2}; "
    r"(?P<board>[^;]{1,96}) with (?P<chip>[^;]{1,64})"
)
XIAOZHI = re.compile(
    r"I\s*\([0-9]+\) Board: UUID=[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    r" SKU=([a-zA-Z0-9][a-zA-Z0-9_.-]{0,79})"
)
APP_INFO = re.compile(r"I\s*\(\d+\) app_init: (Project name|App version):\s+([A-Za-z0-9][A-Za-z0-9.+_/-]{0,79})")
COLOR = re.compile(r"\x1b\[[0-9;]*m")


def identify(line):
    if len(line) > 512:
        return None
    line = COLOR.sub("", line).strip()
    if not line.isprintable():
        return None
    rom = ROM.fullmatch(line)
    if rom:
        return "ROM 启动标识", {"chip": "ESP32-" + rom[1].upper()}
    banner = PYTHON.fullmatch(line)
    if banner:
        return "解释器启动标识", {
            "firmware": banner["runtime"] + " " + banner["version"],
            "board": banner["board"], "chip": banner["chip"],
        }
    app = APP_INFO.fullmatch(line)
    if app:
        key = "firmware" if app[1] == "Project name" else "version"
        return "ESP-IDF 应用信息", {key: app[2]}
    sku = XIAOZHI.fullmatch(line)
    if sku:
        return "小智格式的板型声明", {"board": sku[1]}
    if line == "BoardBenchProbe ready":
        return "诊断固件启动标识", {"firmware": "BoardBenchProbe"}
    return None
