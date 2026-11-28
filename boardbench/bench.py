"""Local sessions, device transport and repeatable tests."""

import asyncio
import codecs
import contextlib
import errno
import json
import os
import re
import shutil
import hashlib
from pathlib import Path
import uuid
import sqlite3
import sys
import time
from datetime import datetime, timezone

import select
import socket
import serial
from serial.tools import list_ports
from boardbench.devices.usb_screen import SerialReplyFilter, UsbScreen
from boardbench.checks.smart import evaluate
from boardbench.devices.device_identity import usb_model
from boardbench.devices.monitor import Components, RESET_WINDOW
from boardbench.checks.endurance import Endurance
from boardbench.checks.replies import ReplyMatch
from boardbench.checks.profiles import BUILTINS, validate


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def open_without_reset(handle, windows=None):
    """Open a configured pyserial port without ever pulsing the auto-reset lines.

    ESP32 boards reset while DTR is off and RTS is on: the Espressif USB-Serial/JTAG
    port (VID 0x303A) and the usual two-transistor circuit on UART bridges share that
    truth table. macOS/Linux raise both lines when the port opens and pyserial then
    writes DTR before RTS, so clearing both before open() passes through exactly that
    reset state. Here POSIX goes (1,1) -> (1,0) -> (0,0); Windows applies both initial
    states together through the DCB at open, starting from (0,0).
    """
    if windows is None:
        windows = os.name == "nt"
    if windows:
        handle.dtr = handle.rts = False
        handle.open()
        return handle
    handle.dtr = handle.rts = True
    handle.open()
    try:
        handle.rts = False
        handle.dtr = False
    except OSError as exc:  # ptys and some adapters have no modem lines, like pyserial's own open()
        if exc.errno not in (errno.EINVAL, errno.ENOTTY):
            handle.close()
            raise
    return handle


def valid_flash_probe(lines):
    text = "\n".join(lines)
    manufacturer = re.search(r"^Manufacturer: ([0-9a-fA-F]{2})$", text, re.M)
    device = re.search(r"^Device: ([0-9a-fA-F]{4})$", text, re.M)
    return bool(
        re.search(r"^Chip is ESP", text, re.M)
        and manufacturer
        and device
        and int(manufacturer[1], 16) not in (0, 255)
        and int(device[1], 16) not in (0, 65535)
        and re.search(r"^Detected flash size: [1-9]\d*(?:KB|MB)$", text, re.M)
    )


class NetworkPort:
    def __init__(self, sock, transport):
        self.sock = sock
        self.transport = transport

    @classmethod
    async def connect(cls, host, port, transport):
        loop = asyncio.get_running_loop()
        typ = socket.SOCK_STREAM if transport == "TCP" else socket.SOCK_DGRAM
        infos = await loop.getaddrinfo(host, port, type=typ)
        error = OSError("设备地址无法解析")
        for family, kind, proto, _, addr in infos:
            sock = socket.socket(family, kind, proto)
            sock.setblocking(False)
            try:
                await asyncio.wait_for(loop.sock_connect(sock, addr), 5)
                return cls(sock, transport)
            except (OSError, asyncio.TimeoutError) as exc:
                sock.close()
                error = exc
        raise ValueError(f"{transport} 连接失败 · {error}")

    @property
    def in_waiting(self):
        return 65535 if select.select([self.sock], [], [], 0)[0] else 0

    def read(self, limit):
        data = self.sock.recv(65535 if self.transport == "UDP" else limit)
        if not data and self.transport == "TCP":
            raise ConnectionError("对端已关闭 TCP 连接")
        return data

    def write(self, data):
        return self.sock.send(data)

    def close(self):
        self.sock.close()


class Bench:
    def __init__(self, path):
        self.db_path = None if str(path) == ":memory:" else Path(path)
        self.storage_hold = ""
        self.backups = Path(path).parent / "backups"
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS smart_reports(id INTEGER PRIMARY KEY,run INTEGER,at TEXT,reason TEXT,rows TEXT);
        CREATE INDEX IF NOT EXISTS smart_reports_run ON smart_reports(run,id);
        CREATE TABLE IF NOT EXISTS profiles(name TEXT PRIMARY KEY, tests TEXT);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started TEXT, profile TEXT, tests TEXT, port TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS backups(id TEXT PRIMARY KEY, run INTEGER, size INTEGER, sha256 TEXT);
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, run INTEGER, at TEXT, level TEXT, source TEXT, message TEXT, raw TEXT);
        CREATE INDEX IF NOT EXISTS run_logs ON logs(run,id);
        CREATE TABLE IF NOT EXISTS capture_info(run INTEGER PRIMARY KEY,settings TEXT);
        CREATE TABLE IF NOT EXISTS results(run INTEGER, name TEXT, state TEXT, detail TEXT, at TEXT, PRIMARY KEY(run,name));
        """)
        self.ser = self.reader = self.job = self.waiter = self.proc = None
        self.taskname = ""
        self.connection_id = 0
        self.transport = "串口"
        self.usb = ""
        self.baud = 115200
        self.framing = "8N1"
        self.network_host = ""
        self.network_port = 0
        self.rx = self.tx = 0
        self.opened_at = 0.0
        self.lost = None
        self.reset_on_open = False
        self.endurance = Endurance(self.db)
        self.usb_screen = UsbScreen()
        self.usb_identification = None
        self.usb_fields = {}
        self.components = Components(self.db)
        previous = self.db.execute(
            "SELECT profile FROM runs WHERE port != '离线故障包' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.new(
            previous[0]
            if previous and previous[0] in self.profiles()
            else "通用串口设备"
        )

    def profiles(self):
        return {
            **BUILTINS,
            **{
                r[0]: json.loads(r[1])
                for r in self.db.execute("SELECT * FROM profiles")
            },
        }

    def add_profile(self, data):
        name, tests = validate(data)
        if name in self.profiles():
            raise ValueError("配置名已存在，请给新版本换一个名称。")
        self.db.execute(
            "INSERT INTO profiles VALUES(?,?)",
            (name, json.dumps(tests, ensure_ascii=False)),
        )
        self.db.commit()

    async def select_profile(self, profile):
        if self.job or profile not in self.profiles():
            raise ValueError("请先等当前任务做完（或点停止），再换板子型号。")
        previous = self.usb if self.ser else ""
        if self.reader:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader
            self.reader = None
        self.new(profile, keep_connection=True)
        if previous:
            self.bind(previous)
            self.log("INFO", self.transport, "沿用当前设备连接 · " + previous)
            self.reader = asyncio.create_task(self.read_loop())

    def new(self, profile, keep_connection=False):
        if (self.ser and not keep_connection) or self.job:
            raise ValueError("请先停止当前任务、断开板子，再开新的记录。")
        if keep_connection and self.ser and self.current_rx_log_id:
            self.save_smart("切换检测配置")
        self.current_rx_log_id = None
        self.connection_rx = 0
        self.endurance.stop("会话切换")
        self.components.reset()
        self.usb_screen.reset()
        self.capture_key = None
        self.tests = self.profiles()[profile]
        self.profile = profile
        if not keep_connection or not self.ser:
            self.usb_identification = None
        self.usb_fields.clear()
        self.components.use_profile(profile)
        self.run = self.db.execute(
            "INSERT INTO runs(started,profile,tests) VALUES(?,?,?)",
            (stamp(), profile, json.dumps(self.tests, ensure_ascii=False)),
        ).lastrowid
        self.usb = ""
        self.rx = self.tx = 0
        self.log("INFO", "会话", "开始测试 · " + profile)

        if keep_connection and self.ser and self.transport == "串口":
            self.record_usb_identity()
            self.capture_settings()

    @property
    def device_identity(self):
        return {**self.usb_fields, **self.components.identity}

    def record_usb_identity(self):
        self.usb_fields.clear()
        if not self.usb_identification:
            self.log("INFO", "设备识别", "板型待识别 · USB 端口未提供已知的板型标识，等待读取设备日志。")
            return
        key, value, source = self.usb_identification
        message = (f"板型 · {value}（{source}）" if key == "board" else
                   f"USB 转串口 · {value}（{source}）。板型待识别，等待读取设备日志。")
        evidence = self.log("INFO", "设备识别", message)
        self.usb_fields[key] = dict(value=value, source=source, log_id=evidence)

    def capture_settings(self):
        settings = {"连接": self.transport}
        if self.transport == "串口":
            settings.update(波特率=str(self.baud), 格式=self.framing)
        for key, label in (("board", "固件板型"), ("firmware", "固件"), ("chip", "芯片"), ("version", "固件版本")):
            if entry := self.components.identity.get(key):
                settings[label] = entry["value"]
        for key, label in (("board", "USB 板型标识"), ("bridge", "USB 转串口")):
            if entry := self.usb_fields.get(key):
                settings[label] = entry["value"]
        if settings != self.capture_key:
            self.db.execute("INSERT OR REPLACE INTO capture_info VALUES(?,?)",
                            (self.run,json.dumps(settings,ensure_ascii=False)))
            self.capture_key = settings

    def log(self, level, source, message, raw=""):
        at = stamp()
        row = self.db.execute(
            "INSERT INTO logs(run,at,level,source,message,raw) VALUES(?,?,?,?,?,?)",
            (self.run, at, level, source, str(message), raw),
        )
        if level == "RX":
            self.current_rx_log_id = row.lastrowid
            notices = []
            reboot = self.components.observe(self.db, self.run, row.lastrowid, at, message, notices)
            self.db.executemany(
                "INSERT INTO logs(run,at,level,source,message,raw) VALUES(?,?,'INFO','设备识别',?,'')",
                [(self.run, at, f"{notice} · 原文 L{row.lastrowid}") for notice in notices],
            )
            self.capture_settings()
            self.endurance.observe(self, message, row.lastrowid, reboot)
            if reboot:
                self.usb_screen.reset()
                self.connection_id += 1
        self.db.commit()
        if (level == "RX" and reboot and self.transport == "串口" and self.ser
                and not self.reset_on_open and time.monotonic() - self.opened_at < RESET_WINDOW):
            self.reset_on_open = True
            self.log("WARN", "USB", "板子在刚连上时重启了一次（收到了开机信息）。不会因此反复重连。")
        return row.lastrowid

    def result(self, name, state, detail):
        self.db.execute(
            "INSERT OR REPLACE INTO results VALUES(?,?,?,?,?)",
            (self.run, name, state, detail, stamp()),
        )
        self.log(
            {"通过": "PASS", "失败": "ERROR", "跳过": "WARN"}[state],
            "检测",
            f"{name} · {state} · {detail}",
        )

    def rows(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, params)]

    def smart_state(self):
        return dict(run=self.run, connected=bool(self.ser), rows=evaluate(self))

    def save_smart(self, reason):
        if not self.ser:
            raise ValueError("请先连上板子，再保存体检结果。")
        self.check_space()
        at = stamp()
        rows = evaluate(self)
        cursor = self.db.execute(
            "INSERT INTO smart_reports(run,at,reason,rows) VALUES(?,?,?,?)",
            (self.run, at, reason, json.dumps(rows, ensure_ascii=False)),
        )
        self.db.commit()
        return dict(id=cursor.lastrowid, at=at, run=self.run)

    def smart_history(self, run):
        reports = self.rows(
            "SELECT id,at,reason,rows FROM smart_reports WHERE run=? ORDER BY id", (run,)
        )
        for report in reports:
            report["rows"] = json.loads(report["rows"])
        return reports

    def report(self, run, include_logs=True):
        rows = self.rows("SELECT * FROM runs WHERE id=?", (run,))
        if not rows:
            raise ValueError("找不到这条记录。")
        info = rows[0]
        info["tests"] = json.loads(info["tests"])
        info["results"] = self.rows(
            "SELECT name,state,detail,at FROM results WHERE run=? ORDER BY at", (run,)
        )
        info["backups"] = self.rows(
            "SELECT id,size,sha256 FROM backups WHERE run=?", (run,)
        )
        if include_logs:
            info["logs"] = self.rows(
                "SELECT id,at,level,source,message,raw FROM logs WHERE run=? ORDER BY id",
                (run,),
            )
        return info

    @staticmethod
    def ports():
        return [
            dict(
                path=p.device,
                label=p.description,
                vid=p.vid,
                pid=p.pid,
                serial=p.serial_number,
            )
            for p in list_ports.comports()
            if "Bluetooth-Incoming" not in p.device and "debug-console" not in p.device
        ]

    def select(self, port):
        device = next((p for p in self.ports() if p["path"] == port), None)
        if device is None:
            raise ValueError("这个 USB 端口不见了：请检查数据线有没有插好，然后点“重新找端口”。")
        if self.usb and port != self.usb:
            raise ValueError("这次记录用的是另一块板子。换板子前请先断开，软件会新开一条记录，免得两块板子的结果混在一起。")

        return device

    def bind(self, port):
        self.usb = port
        self.db.execute("UPDATE runs SET port=? WHERE id=?", (port, self.run))
        self.db.commit()

    def check_space(self):
        if not self.db_path:
            return
        if shutil.disk_usage(self.db_path.parent).free < 1024**3:
            raise ValueError("电脑硬盘剩余空间不到 1 GB，已暂停记录。请清理一些空间。")
        size = sum(
            p.stat().st_size
            for p in (self.db_path, Path(str(self.db_path) + "-wal"))
            if p.is_file()
        )
        if size >= 256 * 1024**2:
            raise ValueError(
                "测试历史达到 256 MiB，已暂停采集。可在停机后归档数据目录并重新启动。"
            )
        self.storage_hold = ""

    def connect(self, port, baud, framing="8N1"):
        if self.ser or self.job:
            raise ValueError("已经连着板子，或者有任务正在进行。")
        self.check_space()
        device = self.select(port)
        if not 300 <= baud <= 4000000:
            raise ValueError("速度（波特率）要在 300–4000000 之间，不确定就填 115200。")
        if framing not in ("8N1", "8E1", "8O1", "8N2", "7E1", "7O1"):
            raise ValueError("不支持这个数据格式，一般选 8N1。")
        handle = serial.Serial(
            port=None,
            baudrate=baud,
            bytesize=int(framing[0]),
            parity=framing[1],
            stopbits=int(framing[2]),
            timeout=0,
            write_timeout=0.5,
        )
        handle.port = port
        open_without_reset(handle)
        self.opened_at = time.monotonic()
        self.lost = None
        self.reset_on_open = False
        self.current_rx_log_id = None
        self.connection_rx = 0
        self.components.reset()
        self.usb_screen.reset()
        self.ser = handle
        self.connection_id += 1
        self.transport = "串口"
        self.bind(port)
        self.baud = baud
        self.framing = framing
        self.log("INFO", "串口", f"已连接 {port} · {baud} {framing} · 开始监听，当前接收 0 字节")
        self.usb_identification = usb_model(device)
        self.record_usb_identity()
        self.capture_settings()
        self.reader = asyncio.create_task(self.read_loop())

    async def network(self, transport, host, port):
        self.check_space()
        host = host.strip()
        if transport not in ("TCP", "UDP") or not host or not 1 <= port <= 65535:
            raise ValueError("请填正确的板子地址和端口号（1–65535）。")
        address = f"{transport.lower()}://{host}:{port}"
        if self.ser or self.job or (self.usb and self.usb != address):
            raise ValueError("请先断开现在的连接；换板子时软件会新开一条记录。")
        self.current_rx_log_id = None
        self.connection_rx = 0
        self.components.reset()
        self.usb_screen.reset()
        self.ser = await NetworkPort.connect(host, port, transport)
        self.usb_identification = None
        self.usb_fields.clear()
        self.connection_id += 1
        self.transport = transport
        self.network_host = host
        self.network_port = port
        self.bind(address)
        self.capture_settings()
        self.log(
            "INFO",
            transport,
            f"{address} · 本机端口 {self.ser.sock.getsockname()[1]} · "
            + ("连接成功" if transport == "TCP" else "已设置对端，等待报文"),
        )
        self.reader = asyncio.create_task(self.read_loop())

    async def read_loop(self):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = bytearray()
        last = time.monotonic()

        next_check = 0
        replies = SerialReplyFilter()

        def output(raw):
            display = decoder.decode(raw)
            if self.transport == "串口" and self.usb_screen.observe(display.rstrip("\r\n")):
                return
            self.log("RX", self.transport, display.rstrip("\r\n"), raw.hex(" "))

        try:
            while self.ser:
                if time.monotonic() >= next_check:
                    try:
                        self.check_space()
                    except ValueError as exc:
                        self.storage_hold = str(exc)
                        self.log("WARN", "存储", self.storage_hold)
                        self.close_port()
                        break
                    next_check = time.monotonic() + 2
                count = self.ser.in_waiting
                if count and len(pending) < 8192:
                    data = self.ser.read(min(count, 8192))
                    self.rx += len(data)
                    self.connection_rx += len(data)
                    # On a screen-capable port, screenshot payloads are separate from command replies.
                    reply = replies.feed(data) if self.transport == "串口" else data
                    if reply:
                        self.feed(reply)
                    pending.extend(data)
                    last = time.monotonic()
                for _ in range(128):
                    if b"\n" not in pending and len(pending) < 4096:
                        break
                    end = pending.find(b"\n") + 1 or 4096
                    end = min(end, 4096)
                    output(bytes(pending[:end]))
                    del pending[:end]
                if (
                    pending
                    and len(pending) < 4096
                    and b"\n" not in pending
                    and time.monotonic() - last > 0.15
                ):
                    if prefix := replies.flush_prefix():
                        self.feed(prefix)
                    output(bytes(pending))
                    pending.clear()
                await asyncio.sleep(0.025)
        except (OSError, serial.SerialException) as exc:
            if self.transport == "串口":
                self.lost = dict(port=self.usb, after=time.monotonic() - self.opened_at,
                                 rom=self.reset_on_open)
            self.log("ERROR", self.transport, "读取中断 · " + str(exc))
            self.close_port()
        finally:
            if pending:
                output(bytes(pending))

    def feed(self, data):
        if self.waiter:
            future, matcher, recent = self.waiter
            if future.done():
                return
            recent.extend(data)
            del recent[:-2048]
            reply = matcher.feed(data, datagram=self.transport == "UDP")
            if reply:
                detail = recent.hex(" ") if matcher.hex else recent.decode("utf-8", "replace")
                if reply == "失败":
                    future.set_exception(ValueError("设备报告失败 · " + detail))
                else:
                    future.set_result(detail)

    def close_port(self):
        self.usb_screen.reset()
        self.endurance.stop("连接中断")
        if self.ser:
            try:
                if self.current_rx_log_id and not self.storage_hold:
                    self.save_smart("连接结束前的最后观测")
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.storage_hold = "智能检测快照未保存 · " + str(exc)
            finally:
                self.ser.close()
                self.ser = None
            self.log("INFO", self.transport, "连接已关闭")
        if self.waiter and not self.waiter[0].done():
            self.waiter[0].set_exception(ValueError("串口已断开"))

    async def disconnect(self):
        self.close_port()
        if self.reader:
            await self.reader
            self.reader = None

    def send(self, data):
        if not self.ser:
            raise ValueError("请先连上板子。")
        if not 1 <= len(data) <= 8192:
            raise ValueError("每次发送限 1–8192 字节。")
        n = self.ser.write(data)
        self.tx += n
        self.log(
            "TX",
            self.transport,
            data[:n].decode("utf-8", "replace").rstrip("\r\n"),
            data[:n].hex(" "),
        )
        if n != len(data):
            raise ValueError(f"只写出 {n}/{len(data)} 字节。")

    def start(self, name, fn):
        if self.job:
            raise ValueError("已经有任务在做了，请等它做完或先点停止。")

        async def runner():
            try:
                await fn()
            except asyncio.CancelledError:
                self.log("WARN", "任务", name + " · 已取消")
                raise
            except Exception as exc:
                self.log("ERROR", "任务", name + " · " + str(exc))
            finally:
                self.job = None
                self.taskname = ""

        self.log("INFO", "任务", name + " · 开始")
        self.taskname = name
        self.job = asyncio.create_task(runner())

    def skip_if_absent(self, test):
        part = self.components.last.get(test.get("part"), {})
        if self.ser and part.get("status") == "固件声明未安装":
            detail = f"固件声明未安装 · {test['part']} · L{part['log_id']}"
            self.result(test["name"], "跳过", detail)
            return dict(state="跳过", detail=detail)
        return None

    async def cases(self, items):
        for test in items:
            if self.skip_if_absent(test):
                continue
            name = test["name"]
            self.log(
                "INFO",
                "检测",
                f"{name} · 等待 {test['expect']!r} · {test.get('timeout', 5)} 秒",
            )
            future = asyncio.get_running_loop().create_future()
            encode = bytes.fromhex if test.get("encoding") == "hex" else str.encode
            ending = {"CRLF": b"\r\n", "LF": b"\n", "CR": b"\r", "none": b""}
            suffix = ending[
                test.get("ending", "none" if test.get("encoding") == "hex" else "CRLF")
            ]
            self.waiter = (future, ReplyMatch(test), bytearray())
            try:
                self.send(encode(test["tx"]) + suffix)
                reply = await asyncio.wait_for(future, test.get("timeout", 5))
                self.result(name, "通过", "收到匹配应答 · " + reply.strip())
            except asyncio.TimeoutError:
                self.result(name, "失败", "等待设备应答超时")
            except asyncio.CancelledError:
                self.result(name, "跳过", "操作员取消")
                raise
            except Exception as exc:
                self.result(name, "失败", str(exc))
                if not self.ser:
                    break
            finally:
                self.waiter = None

    @staticmethod
    def worker():
        return (
            [sys.executable, "--esp-worker"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-u", "-m", "esptool"]
        )

    async def backup(self, port):
        self.check_space()
        await self.disconnect()
        self.bind(port)
        self.backups.mkdir(parents=True, exist_ok=True)
        key = uuid.uuid4().hex
        partial = self.backups / (key + ".part")
        process = await asyncio.create_subprocess_exec(
            *self.worker(),
            "--port",
            port,
            "--baud",
            "460800",
            "read_flash",
            "--no-progress",
            "0",
            "ALL",
            str(partial),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:

            async def collect():
                async for line in process.stdout:
                    self.log("INFO", "备份", line.decode("utf-8", "replace").strip())
                return await process.wait()

            code = await asyncio.wait_for(collect(), 1200)
            if code or not partial.exists() or not partial.stat().st_size:
                raise ValueError("没读出闪存内容，请看右边记录里 esptool 的原始输出。")

            def checksum():
                h = hashlib.sha256()
                with partial.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(chunk)
                return h.hexdigest()

            digest = checksum()
            size = partial.stat().st_size
            partial.replace(self.backups / (key + ".bin"))
            self.db.execute(
                "INSERT INTO backups VALUES(?,?,?,?)", (key, self.run, size, digest)
            )
            self.result("Flash 完整备份", "通过", f"{size} 字节 · SHA-256 {digest}")
        except asyncio.TimeoutError:
            self.result("Flash 完整备份", "失败", "20 分钟超时")
        except (OSError, ValueError) as exc:
            self.result("Flash 完整备份", "失败", str(exc))
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            partial.unlink(missing_ok=True)

    async def probe(self, port):
        await self.disconnect()
        self.bind(port)
        self.log("INFO", "ESP ROM", "进入下载模式，读取芯片与 Flash 标识，结束后复位。")
        self.proc = await asyncio.create_subprocess_exec(
            *self.worker(),
            "--port",
            port,
            "--no-stub",
            "flash_id",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        lines = []

        async def collect():
            async for line in self.proc.stdout:
                s = line.decode("utf-8", "replace").strip()
                if s:
                    self.log("INFO", "ESP ROM", s)
                    lines.append(s)
            return await self.proc.wait()

        try:
            rc = await asyncio.wait_for(collect(), 35)
            evidence = " | ".join(
                s
                for s in lines
                if any(
                    x in s
                    for x in (
                        "Chip is",
                        "Manufacturer:",
                        "Device:",
                        "flash size:",
                        "MAC:",
                    )
                )
            )
            self.result(
                "ESP 芯片与 Flash",
                "通过" if rc == 0 and valid_flash_probe(lines) else "失败",
                evidence or "读取失败，检查端口、BOOT 下载模式和上方原始输出。",
            )
        except asyncio.TimeoutError:
            self.result("ESP 芯片与 Flash", "失败", "35 秒超时，检查下载模式。")
        finally:
            if self.proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
                await self.proc.wait()
            self.proc = None

    async def stop(self):
        if self.job:
            task = self.job
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self.job = None
            self.taskname = ""
