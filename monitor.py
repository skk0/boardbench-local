"""Component observations from device output. USB enumeration never proves peripheral health."""

import asyncio
import json
import re
import time

from smart import key_cycles

PARTS = {
    "mcu": "主控",
    "flash": "Flash",
    "sram": "片内 SRAM",
    "psram": "PSRAM",
    "wifi": "Wi-Fi",
    "ble": "蓝牙 BLE",
    "buttons": "功能按键",
    "imu": "六轴传感器",
    "light": "环境光",
    "eeprom": "EEPROM",
    "sd": "TF 卡",
    "lcd": "LCD 屏幕",
    "touch": "触摸",
    "audio": "音频芯片",
    "mic": "麦克风",
    "speaker": "扬声器",
    "led": "双色 LED",
    "buzzer": "蜂鸣器",
    "camera": "摄像头",
    "usb": "USB 接口",
    "expansion": "PH2.0 扩展",
    "i2c": "I²C 总线",
}
STATES = {
    "sample": "有读数",
    "ack": "有应答",
    "idle": "未启用",
    "error": "设备报错",
    "missing": "无应答",
    "unavailable": "未提供读数",
    "control": "控制状态",
}
TAG = re.compile(r"^(?P<level>[EWIDV])\s*\(\d+\)\s+(?P<tag>[A-Za-z0-9_.-]+):")
TAGS = {
    "boot": "mcu",
    "cpu_start": "mcu",
    "app_init": "mcu",
    "wifi": "wifi",
    "wifistation": "wifi",
    "wifi_station": "wifi",
    "wifi_init": "wifi",
    "esp_psram": "psram",
    "octal_psram": "psram",
    "spi_flash": "flash",
    "audiocodec": "audio",
    "audioservice": "audio",
    "es8311": "audio",
    "display": "lcd",
    "lcddisplay": "lcd",
    "esp_lcd": "lcd",
    "camera": "camera",
    "sdmmc": "sd",
    "sdspi": "sd",
    "i2c": "i2c",
    "i2c.master": "i2c",
    "qmi8658": "imu",
    "esp32_camera": "camera",
}


def decode(line):
    plain = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
    if plain.startswith("{") and len(plain) <= 4096:
        try:
            data = json.loads(plain)
        except (ValueError, RecursionError):
            return None
        if not isinstance(data, dict) or data.get("bb") != 1:
            return None
        part = data.get("part")
        if (
            not isinstance(part, str)
            or not re.fullmatch(r"[a-z0-9_.-]{1,40}", part)
            or not isinstance(data.get("status"), str)
            or data.get("status") not in STATES
        ):
            return None
        return part, STATES[data["status"]], str(data.get("value", ""))[:400], True
    match = TAG.match(plain)
    if match:
        tag = match["tag"].lower()
        part = TAGS.get(tag)
        if tag == "esp_netif_handlers" and plain[match.end():].lstrip().lower().startswith("sta ip:"):
            part = "wifi"
        if part:
            return (
                part,
                "固件报错" if match["level"] == "E" else "固件日志",
                plain[:400],
                False,
            )
    return None


class Components:
    def __init__(self, db):
        db.executescript("""
        CREATE TABLE IF NOT EXISTS component_events(run INTEGER,part TEXT,log INTEGER PRIMARY KEY);
        CREATE INDEX IF NOT EXISTS component_run ON component_events(run,part,log);
        """)
        self.last = {}

    def observe(self, db, run, log_id, at, line):
        parsed = decode(line)
        if not parsed:
            return
        part, state, value, periodic = parsed
        if part not in self.last and len(self.last) >= 128:
            return
        now = time.monotonic()
        cycles = {}
        if part == "buttons":
            previous = self.last.get(part, {})
            if now - previous.get("tick", now) > 8:
                previous = {}
            cycles = key_cycles(previous, state, value, log_id)
        self.last[part] = dict(
            part=part,
            status=state,
            value=value,
            at=at,
            log_id=log_id,
            first_log_id=self.last.get(part, {}).get("first_log_id", log_id),
            tick=now,
            periodic=periodic,
            **cycles,
        )
        db.execute("INSERT INTO component_events VALUES(?,?,?)", (run, part, log_id))

    def states(self, profile, connected):
        names = list(PARTS) if "BOX" in profile else ["mcu", "flash", "sram", "usb"]
        rows = []
        for part in dict.fromkeys([*names, *self.last]):
            last = self.last.get(part, {})
            status = last.get("status", "未上报")
            if not connected:
                status = "连接断开" if last else "等待设备"
            elif last and last["periodic"] and time.monotonic() - last["tick"] > (90 if part == "power" else 8):
                status = "上报超时"
            rows.append(
                dict(
                    part=part,
                    name="能源遥测" if part == "power" else PARTS.get(part, part),
                    status=status,
                    value=last.get("value", ""),
                    at=last.get("at", ""),
                    log_id=last.get("log_id"),
                    first_log_id=last.get("first_log_id"),
                )
            )
        return rows


class AutoUSB:
    def __init__(self, bench, lock):
        self.b = bench
        self.lock = lock
        self.b.db.execute(
            "CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY,value TEXT)"
        )
        row = self.b.db.execute(
            "SELECT value FROM preferences WHERE key='auto_usb'"
        ).fetchone()
        self.enabled = row is None or row[0] == "1"
        self.ignored = set()

        self.task = None
        self.message = "等待 USB 设备"
        row = self.b.db.execute(
            "SELECT value FROM preferences WHERE key='usb_identity'"
        ).fetchone()
        self.trusted = json.loads(row[0]) if row else None
        self.online = []
        self.next_try = {}

    def enable(self, value):
        self.enabled = bool(value)
        self.ignored.clear()
        self.next_try.clear()
        self.b.db.execute(
            "INSERT OR REPLACE INTO preferences VALUES('auto_usb',?)",
            ("1" if value else "0",),
        )
        self.b.db.commit()

    @staticmethod
    def identity(p):
        sn = (p.get("serial") or "").strip()
        if (
            p.get("vid") is None
            or p.get("pid") is None
            or not sn
            or sn.lower() in ("none", "unknown", "n/a")
            or set(sn) == {"0"}
        ):
            return None
        return [p["vid"], p["pid"], sn]

    def remember(self, path, baud, framing):
        devices = self.b.ports()
        current = next((p for p in devices if p["path"] == path), {})
        ident = self.identity(current)
        if ident and sum(self.identity(p) == ident for p in devices) == 1:
            self.trusted = dict(identity=ident, baud=baud, framing=framing)
        else:
            self.trusted = None
        self.b.db.execute(
            "INSERT OR REPLACE INTO preferences VALUES('usb_identity',?)",
            (json.dumps(self.trusted),),
        )
        self.b.db.commit()

    async def check(self):
        ports = await asyncio.to_thread(self.b.ports)
        self.online = ports
        paths = {p["path"] for p in ports}
        self.ignored.intersection_update(paths)
        async with self.lock:
            if not self.enabled or self.b.ser or self.b.job:
                return
            if self.b.storage_hold:
                self.message = self.b.storage_hold
                return
            if not self.trusted:
                self.message = "首次使用请选串口并点连接" if ports else "等待 USB 设备"
                return
            matches = [p for p in ports if self.identity(p) == self.trusted["identity"]]
            if len(matches) != 1:
                self.message = (
                    "发现重复设备标识，请手动选择" if matches else "等待已识别的板子"
                )
                return
            path = matches[0]["path"]
            if path in self.ignored:
                self.message = "监视已手动断开"
                return
            if time.monotonic() < self.next_try.get(path, 0):
                return
            try:
                if self.b.usb:
                    await self.b.select_profile(self.b.profile)
                self.b.connect(path, self.trusted["baud"], self.trusted["framing"])
                self.message = "已自动连接 " + path
                self.b.log("INFO", "USB", "已按 USB 标识重新连接，只监听板端输出。")
            except (ValueError, OSError) as exc:
                self.next_try[path] = time.monotonic() + 15
                self.message = "连接失败，15 秒后重试"
                self.b.log("WARN", "USB", f"{path} · {exc}")

    async def watch(self):
        while True:
            try:
                await self.check()
            except (OSError, ValueError) as exc:
                self.message = "枚举失败 · " + str(exc)
            await asyncio.sleep(1.5)
