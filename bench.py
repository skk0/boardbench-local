"""Local sessions, device transport and repeatable tests."""

import asyncio
import codecs
import contextlib
import json
import re
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
from profiles import BUILTINS, validate


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


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
        self.backups = Path(path).parent / "backups"
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS profiles(name TEXT PRIMARY KEY, tests TEXT);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started TEXT, profile TEXT, tests TEXT, port TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS backups(id TEXT PRIMARY KEY, run INTEGER, size INTEGER, sha256 TEXT);
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, run INTEGER, at TEXT, level TEXT, source TEXT, message TEXT, raw TEXT);
        CREATE INDEX IF NOT EXISTS run_logs ON logs(run,id);
        CREATE TABLE IF NOT EXISTS results(run INTEGER, name TEXT, state TEXT, detail TEXT, at TEXT, PRIMARY KEY(run,name));
        """)
        self.ser = self.reader = self.job = self.waiter = self.proc = None
        self.taskname = ""
        self.transport = "串口"
        self.usb = ""
        self.baud = 115200
        self.framing = "8N1"
        self.network_host = ""
        self.network_port = 0
        self.rx = self.tx = 0
        self.new("通用串口设备")

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
            raise ValueError("请先结束当前任务，并选择有效配置。")
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
            raise ValueError("请先停止任务并断开设备，再开始新的测试。")
        self.tests = self.profiles()[profile]
        self.profile = profile
        self.run = self.db.execute(
            "INSERT INTO runs(started,profile,tests) VALUES(?,?,?)",
            (stamp(), profile, json.dumps(self.tests, ensure_ascii=False)),
        ).lastrowid
        self.usb = ""
        self.rx = self.tx = 0
        self.log("INFO", "会话", "开始测试 · " + profile)

    def log(self, level, source, message, raw=""):
        self.db.execute(
            "INSERT INTO logs(run,at,level,source,message,raw) VALUES(?,?,?,?,?,?)",
            (self.run, stamp(), level, source, str(message), raw),
        )
        self.db.commit()

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

    def report(self, run):
        rows = self.rows("SELECT * FROM runs WHERE id=?", (run,))
        if not rows:
            raise ValueError("没有这次测试。")
        info = rows[0]
        info["tests"] = json.loads(info["tests"])
        info["results"] = self.rows(
            "SELECT name,state,detail,at FROM results WHERE run=? ORDER BY at", (run,)
        )
        info["backups"] = self.rows(
            "SELECT id,size,sha256 FROM backups WHERE run=?", (run,)
        )
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
        if port not in {p["path"] for p in self.ports()}:
            raise ValueError("串口已离线，请刷新设备并检查 USB 数据线。")
        if self.usb and port != self.usb:
            raise ValueError("换设备请先新建测试，避免混入上一块板的结果。")

    def bind(self, port):
        self.usb = port
        self.db.execute("UPDATE runs SET port=? WHERE id=?", (port, self.run))
        self.db.commit()

    def connect(self, port, baud, framing="8N1"):
        if self.ser or self.job:
            raise ValueError("串口或检测任务正在使用中。")
        self.select(port)
        if not 300 <= baud <= 4000000:
            raise ValueError("波特率超出范围。")
        if framing not in ("8N1", "8E1", "8O1", "8N2", "7E1", "7O1"):
            raise ValueError("不支持此串口格式")
        handle = serial.Serial(
            port=None,
            baudrate=baud,
            bytesize=int(framing[0]),
            parity=framing[1],
            stopbits=int(framing[2]),
            timeout=0,
            write_timeout=0.5,
        )
        handle.dtr = handle.rts = False
        handle.port = port
        handle.open()
        self.ser = handle
        self.transport = "串口"
        self.bind(port)
        self.baud = baud
        self.framing = framing
        self.log("INFO", "串口", f"已连接 {port} · {baud} {framing}")
        self.reader = asyncio.create_task(self.read_loop())

    async def network(self, transport, host, port):
        host = host.strip()
        if transport not in ("TCP", "UDP") or not host or not 1 <= port <= 65535:
            raise ValueError("设备主机名与端口无效。")
        address = f"{transport.lower()}://{host}:{port}"
        if self.ser or self.job or (self.usb and self.usb != address):
            raise ValueError("请结束当前连接，换设备时先新建测试。")
        self.ser = await NetworkPort.connect(host, port, transport)
        self.transport = transport
        self.network_host = host
        self.network_port = port
        self.bind(address)
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

        def output(raw):
            display = decoder.decode(raw)
            self.log("RX", self.transport, display.rstrip("\r\n"), raw.hex(" "))

        try:
            while self.ser:
                count = self.ser.in_waiting
                if count:
                    data = self.ser.read(min(count, 8192))
                    self.rx += len(data)
                    self.feed(data)
                    pending.extend(data)
                    last = time.monotonic()
                while b"\n" in pending or len(pending) >= 4096:
                    end = pending.find(b"\n") + 1 or 4096
                    end = min(end, 4096)
                    output(bytes(pending[:end]))
                    del pending[:end]
                if pending and time.monotonic() - last > 0.15:
                    output(bytes(pending))
                    pending.clear()
                await asyncio.sleep(0.025)
        except (OSError, serial.SerialException) as exc:
            self.log("ERROR", self.transport, "读取中断 · " + str(exc))
            self.close_port()
        finally:
            if pending:
                output(bytes(pending))

    def feed(self, data):
        if self.waiter:
            future, needle, reject, buf, test_hex = self.waiter
            buf.extend(data)
            if reject and reject in buf and not future.done():
                future.set_exception(
                    ValueError(
                        "设备报告失败 · " + buf.decode("utf-8", "replace")[-1024:]
                    )
                )
            if needle in buf and not future.done():
                future.set_result(
                    buf.hex(" ")[-2048:]
                    if test_hex
                    else buf.decode("utf-8", "replace")[-2048:]
                )
            if len(buf) > 8192:
                del buf[:-4096]

    def close_port(self):
        if self.ser:
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
            raise ValueError("请先连接设备。")
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
            raise ValueError("已有任务运行，可先停止。")

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

    async def cases(self, items):
        for test in items:
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
            self.waiter = (
                future,
                encode(test["expect"]),
                encode(test.get("reject", "")),
                bytearray(),
                test.get("encoding") == "hex",
            )
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
                raise ValueError("Flash 读取失败，查看 esptool 原始输出。")

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
