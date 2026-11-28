"""Component observations from device output. USB enumeration never proves peripheral health."""

import asyncio
import json
import re
import time

from boardbench.devices.device_identity import USB_BOARDS, identify
from boardbench.checks.smart import key_cycles

RESET_WINDOW = 5    # seconds after opening in which a vanished port or ROM banner means "we reset it"
USB_BRIDGES = {(0x303A, 0x1001), (0x10C4, 0xEA60), (0x1A86, 0x7523),
               (0x1A86, 0x55D4), (0x1A86, 0x55D3), (0x0403, 0x6001)}
MAX_RESETS = 3      # drops right after opening before auto-connect gives up on a device
STABLE_AFTER = 10   # seconds a connection must survive to clear earlier drops

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
BOX_PARTS = tuple(PARTS)
PARTS.update({
    "modem": "蜂窝通信模块", "session": "语音连接", "weather": "天气服务",
    "temperature": "温度", "humidity": "湿度", "pressure": "气压",
})
STATES = {
    "sample": "有读数",
    "ack": "有应答",
    "idle": "未启用",
    "error": "设备报错",
    "missing": "无应答",
    "absent": "固件声明未安装",
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
    "ml307board": "modem", "ml307atmodem": "modem",
    "mqtt": "session", "websocket": "session", "deskweather": "weather",
    "esp32camera": "camera", "es7210": "audio",

    "sdmmc": "sd",
    "sdspi": "sd",
    "i2c": "i2c",
    "i2c.master": "i2c",
    "qmi8658": "imu",
    "esp32_camera": "camera",
}


# ESP-IDF hello_world's chip-info printf ends with the queried Flash capacity.
# Earlier fields list chip features, not the connection state of its radios.
CHIP_FLASH = re.compile(
    r"This is esp[0-9a-z-]+ chip with [1-9]\d? CPU core\(s\), "
    r"[A-Za-z0-9/,. ()-]{0,96}, silicon revision v\d+\.\d+, "
    r"([1-9]\d{0,5})\s*MB (external|embedded) flash", re.I
)
PSRAM_FOUND = re.compile(r"PSRAM found(?:, inputBufferSize: [0-9]{1,10} bytes)?", re.I)
WIFI_EVENT = re.compile(r"Wi-?Fi[ :]+(connected|disconnected|connection failed)(?:, please wait\.{3}|[.!])?", re.I)


def plain_component(text):
    flash = CHIP_FLASH.fullmatch(text)
    if flash:
        location = "外置" if flash[2].lower() == "external" else "内置"
        return "flash", "固件日志", f"{flash[1]} MB {location} Flash（固件上报）", False, ""
    if PSRAM_FOUND.fullmatch(text):
        return "psram", "固件日志", "已检测到 PSRAM，容量未上报", False, ""
    wifi = WIFI_EVENT.fullmatch(text)
    if wifi:
        value = {"connected": "已连接", "disconnected": "已断开", "connection failed": "连接失败"}[wifi[1].lower()]
        return "wifi", "固件日志", f"Wi-Fi {value}（固件上报）", False, ""
    if text == "Start WiFi Connect!":
        return "wifi", "固件日志", "正在连接 Wi-Fi", False, ""
    return None


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
        model = data.get("model")
        model = model.strip() if isinstance(model, str) and model.isprintable() and len(model) <= 80 else ""
        return part, STATES[data["status"]], str(data.get("value", ""))[:400], data["status"] != "absent", model
    match = TAG.match(plain)
    if match:
        tag = match["tag"].lower()
        part = TAGS.get(tag)
        if tag == "esp_netif_handlers" and plain[match.end():].lstrip().lower().startswith("sta ip:"):
            part = "wifi"
        if part == "modem" and re.search(r"\b(?:IMEI|IMSI|ICCID)\b", plain, re.I):
            return None
        if part:
            model = ""
            if tag == "camera" and match["level"] == "I":
                # esp32-camera reports this exact line after a successful sensor probe.
                sensor = re.fullmatch(r"Detected ([A-Z]{2,6}[0-9]{2,6}[A-Z0-9]*) camera", plain[match.end():].strip())
                if sensor:
                    model = sensor[1]
            return (
                part,
                "固件报错" if match["level"] == "E" else "固件日志",
                plain[:400],
                False,
                model,
            )
    return plain_component(plain) if len(plain) <= 256 else None


class Components:
    def __init__(self, db):
        db.executescript("""
        CREATE TABLE IF NOT EXISTS component_events(run INTEGER,part TEXT,log INTEGER PRIMARY KEY);
        CREATE INDEX IF NOT EXISTS component_run ON component_events(run,part,log);
        """)
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS cadence(profile TEXT,part TEXT,mode TEXT,seconds INTEGER,
                   PRIMARY KEY(profile,part))""")
        self.profile = ""
        self.rules = {}
        self.last = {}
        self.identity = {}

    def use_profile(self, profile):
        self.profile = profile
        self.rules = {row[0]: (row[1], row[2]) for row in self.db.execute(
            "SELECT part,mode,seconds FROM cadence WHERE profile=?", (profile,)
        )}

    def set_rule(self, part, mode, seconds):
        if not isinstance(part, str) or not re.fullmatch(r"[a-z0-9_.-]{1,40}", part):
            raise ValueError("元件编号无效。")
        if mode not in ("auto", "periodic", "event", "once"):
            raise ValueError("请选择上报方式。")
        if mode == "periodic" and (type(seconds) is not int or not 1 <= seconds <= 86400):
            raise ValueError("时间要在 1–86400 秒之间。")
        if mode == "auto":
            self.db.execute("DELETE FROM cadence WHERE profile=? AND part=?", (self.profile, part))
        else:
            self.db.execute("INSERT OR REPLACE INTO cadence VALUES(?,?,?,?)",
                            (self.profile, part, mode, seconds if mode == "periodic" else 0))
        self.db.commit()
        self.use_profile(self.profile)

    def timeout(self, part):
        mode, seconds = self.rules.get(part, ("auto", 0))
        if mode in ("event", "once"):
            return None
        if mode == "periodic":
            return seconds * 3
        return 90 if part == "power" else 8

    def periodic(self, part, row):
        mode = self.rules.get(part, ("auto",))[0]
        if row.get("status") == "固件声明未安装" or mode in ("event", "once"):
            return False
        return mode == "periodic" or row.get("periodic", False)

    def reset(self):
        self.last.clear()
        self.identity.clear()

    def observe(self, db, run, log_id, at, line, notices):
        identity = identify(line)
        reboot = bool(identity and identity[0] == "ROM 启动标识")
        if reboot:
            self.reset()
        if identity:
            prior = self.identity.copy()
            source, fields = identity
            # A new startup banner supersedes earlier application declarations.
            rom_chip = self.identity.get("chip", {})
            if source not in ("ESP-IDF 应用信息", "小智格式的板型声明", "诊断固件板型声明"):
                self.identity.clear()
            if "chip" not in fields and rom_chip.get("source") == "ROM 启动标识":
                self.identity["chip"] = rom_chip
            for key, value in fields.items():
                self.identity[key] = dict(value=value, source=source, log_id=log_id)
            for key, label in (("board", "板型"), ("chip", "主控")):
                entry = self.identity.get(key)
                if entry and entry.get("value") != prior.get(key, {}).get("value"):
                    notices.append(f"{label} · {entry['value']}（{entry['source']}）")
        parsed = decode(line)
        if not parsed:
            return reboot
        part, state, value, periodic, model = parsed
        if part == "mcu" and state == "有读数":
            chip = re.fullmatch(r"(ESP32(?:-[A-Za-z0-9-]+)?) \| CPU [0-9]+ MHz \| uptime [0-9]+ ms", value)
            if chip and self.identity.get("chip", {}).get("value") != chip[1]:
                self.identity["chip"] = dict(value=chip[1], source="主控遥测", log_id=log_id)
                notices.append(f"主控 · {chip[1]}（主控遥测）")
        if part not in self.last and len(self.last) >= 128:
            return
        now = time.monotonic()
        previous = self.last.get(part, {})
        if model and (model != previous.get("model") or (state == "固件声明未安装") != (previous.get("status") == "固件声明未安装")):
            notices.append(f"{PARTS.get(part, part)} · {model}（{state}）")
        if not model and state != "固件声明未安装":
            model = previous.get("model", "")
        cycles = {}
        if part == "buttons":
            timeout = self.timeout(part)
            if model != previous.get("model", "") or (timeout and now - previous.get("tick", now) > timeout) or state == "固件声明未安装":
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
            model=model,
            **cycles,
        )
        db.execute("INSERT INTO component_events VALUES(?,?,?)", (run, part, log_id))
        return reboot

    def states(self, profile, connected):
        names = BOX_PARTS if profile in {"正点原子 · DNESP32S3B BOX", "DNESP32S3B BOX · BoardBenchProbe 固件"} else ()
        rows = []
        for part in dict.fromkeys([*names, *self.last]):
            last = self.last.get(part, {})
            status = last.get("status", "未上报")
            timeout = self.timeout(part)
            if not connected:
                status = "连接断开" if last else "等待设备"
            elif last and timeout and self.periodic(part, last) and time.monotonic() - last["tick"] > timeout:
                status = "上报超时"
            rows.append(
                dict(
                    part=part,
                    name="能源遥测" if part == "power" else PARTS.get(part, part),
                    model=last.get("model", ""),
                    cadence=self.rules.get(part, ("auto", 0)), timeout=timeout,
                    expected=part in names and last.get("status") != "固件声明未安装",
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
        self.message = "等你插上板子"
        row = self.b.db.execute(
            "SELECT value FROM preferences WHERE key='usb_identity'"
        ).fetchone()
        self.db = self.b.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS remembered_usb(identity TEXT PRIMARY KEY,
                         name TEXT NOT NULL, baud INTEGER, framing TEXT)""")
        if row and (old := json.loads(row[0])):
            self.db.execute("INSERT OR IGNORE INTO remembered_usb VALUES(?,?,?,?)",
                            (json.dumps(old["identity"]), "", old["baud"], old["framing"]))
        self.db.execute("DELETE FROM preferences WHERE key='usb_identity'")
        self.db.commit()
        self.online = []
        self.previous_paths = None
        self.inserted = set()
        self.next_try = {}
        self.current_key = None
        self.resets = 0
        self.gave_up = None

    def enable(self, value):
        self.enabled = bool(value)
        self.ignored.clear()
        self.next_try.clear()
        self.resets, self.gave_up = 0, None
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
        self.current_key = None
        self.resets, self.gave_up = 0, None
        if ident and sum(self.identity(p) == ident for p in devices) == 1:
            self.current_key = json.dumps(ident)
            self.db.execute("""INSERT INTO remembered_usb VALUES(?,?,?,?) ON CONFLICT(identity)
                            DO UPDATE SET baud=excluded.baud,framing=excluded.framing""",
                            (json.dumps(ident), "", baud, framing))
            self.db.commit()

    def known(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM remembered_usb ORDER BY name,identity")]

    def describe(self, ports):
        saved = {r["identity"]: r for r in self.known()}
        ids = [json.dumps(self.identity(p)) for p in ports]
        return [dict(p, saved=saved.get(key) if ids.count(key) == 1 else None)
                for p,key in zip(ports,ids)]

    def rename(self, identity, name):
        if not isinstance(name, str) or len(name.strip()) > 64:
            raise ValueError("名字最多 64 个字。")
        if not self.db.execute("UPDATE remembered_usb SET name=? WHERE identity=?", (name.strip(), identity)).rowcount:
            raise ValueError("这块板子没有唯一的序列号，不能记住。")
        self.db.commit()

    def note_drop(self):
        """Count a port that vanished right after we opened it: opening it reset the board."""
        lost, self.b.lost = self.b.lost, None
        if self.b.ser and time.monotonic() - self.b.opened_at > STABLE_AFTER:
            self.resets = 0
        if not lost or lost["after"] >= STABLE_AFTER:
            if lost:
                self.resets = 0
            return
        if lost["after"] >= RESET_WINDOW and not lost["rom"]:
            return
        self.resets += 1
        if self.resets >= MAX_RESETS:
            self.gave_up = self.current_key
            self.message = ("板子每次连上都会自己重启，已停止自动连接，免得它一直重启。"
                            "可以点“连接”手动再试；还不行就换到板子上标着 UART 的 USB 口。")
            self.b.log("WARN", "USB", self.message)
            return
        wait = 1.5 * 2 ** self.resets
        self.next_try[lost["port"]] = time.monotonic() + wait
        self.message = f"板子刚连上就断开了（多半是重启了），{wait:.0f} 秒后再连"
        self.b.log("WARN", "USB", f"{lost['port']} 打开 {lost['after']:.1f} 秒后断开，"
                   f"可能是打开串口时板子重启了。等 {wait:.0f} 秒再连，第 {self.resets} 次。")

    async def check(self):
        ports = await asyncio.to_thread(self.b.ports)
        self.online = ports
        paths = {p["path"] for p in ports}
        added = paths - self.previous_paths if self.previous_paths is not None else set()
        self.inserted.intersection_update(paths)
        self.inserted.update(added)
        self.previous_paths = paths
        self.ignored.intersection_update(paths)
        async with self.lock:
            self.note_drop()
            if not self.enabled or self.b.ser or self.b.job:
                return
            if self.b.storage_hold:
                self.message = self.b.storage_hold
                return
            saved = {r["identity"]: r for r in self.known()}
            matches = [(p, saved[ident]) for p in ports
                       if (ident := json.dumps(self.identity(p))) in saved]
            if not matches:
                usb = [p for p in ports if p.get("vid") is not None]
                # Unrelated OS pseudo-ports must not prevent detecting a USB insertion.
                if len(usb) == 1:
                    device = usb[0]
                    ident = self.identity(device)
                    ids = device["vid"], device["pid"]
                    if (ids in USB_BRIDGES or ids in USB_BOARDS) and (ident or device["path"] in self.inserted):
                        key = json.dumps(ident) if ident else "port:" + device["path"]
                        matches = [(device, dict(identity=key, baud=115200, framing="8N1"))]
                if not matches:
                    if len(usb) > 1:
                        self.message = "发现多个 USB 串口，请在「板子状态」选择要识别的端口。"
                    elif usb:
                        self.message = "该 USB 端口缺少可自动匹配的标识，请在「板子状态」选择端口。"
                    elif ports:
                        self.message = "未发现可自动识别的 USB 串口，可在「板子状态」查看端口。"
                    else:
                        self.message = "尚未发现 USB 串口，请插入板子的 USB 数据线。"
                    return
            if len(matches) > 1:
                self.message = "发现多个匹配的 USB 串口，请在「板子状态」选择要识别的端口。"
                return
            device, settings = matches[0]
            path = device["path"]
            if path in self.ignored:
                self.message = "你手动断开了；拔插一次或点“连接”会再连上"
                return
            if self.gave_up == settings["identity"]:
                return
            if time.monotonic() < self.next_try.get(path, 0):
                return
            try:
                same = self.identity(device) is not None and self.current_key == settings["identity"]
                profile = self.b.profile if same else "通用串口设备"
                # The same board coming back (e.g. after a reset) keeps its session.
                if (self.b.usb and not (same and self.b.usb == path)) or profile != self.b.profile:
                    await self.b.select_profile(profile)
                self.b.connect(path, settings["baud"], settings["framing"])
                self.current_key = settings["identity"]
                self.message = "已自动连上 " + path
                self.b.log("INFO", "USB", "已自动打开 USB 串口，正在监听设备输出。")
            except (ValueError, OSError) as exc:
                self.next_try[path] = time.monotonic() + 15
                self.message = "没连上（" + str(exc)[:60] + "），15 秒后再试"
                self.b.log("WARN", "USB", f"{path} · {exc}")

    async def watch(self):
        while True:
            try:
                await self.check()
            except (OSError, ValueError) as exc:
                self.message = "找 USB 端口时出错 · " + str(exc)
            await asyncio.sleep(1.5)
