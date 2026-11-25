"""Flashing mode: check the board, back up, write with verify, reset and watch it boot, restore.

Monitoring never comes here. Every step that touches the board runs as an explicit job, and
the board is talked to only through esptool's own loader classes (no other tools). This module
never erases the whole chip and never writes eFuses; by default it never writes the settings
(NVS) area either -- firmware.assess() already cut it out of the plan.
"""

import asyncio
import contextlib
import hashlib
import io
import json
import re
import time
import uuid
import zlib
from datetime import datetime, timezone

import serial
from esptool.cmds import detect_chip, detect_flash_size
from esptool.loader import DEFAULT_TIMEOUT, ERASE_WRITE_TIMEOUT_PER_MB, timeout_per_mb
from esptool.util import NotImplementedInROMError, flash_size_bytes, pad_to
from serial.tools import list_ports

import firmware
from bench import open_without_reset
from device_identity import XIAOZHI

STEPS = ["选文件", "识别和检查", "看清会改哪些地方", "备份", "刷写", "校验", "开机确认"]
BOOT_WATCH = 15


class Lines(io.TextIOBase):
    """Forward esptool's console output, line by line."""

    def __init__(self, emit):
        self.emit, self.buffer = emit, ""

    def write(self, text):
        self.buffer += text.replace("\r", "\n")
        *done, self.buffer = self.buffer.split("\n")
        for line in done:
            if line.strip():
                self.emit(line.strip())
        return len(text)


@contextlib.contextmanager
def esp_session(port, emit):
    """Download-mode connection through esptool: resets into the ROM loader, runs the stub,
    always hard-resets on the way out so the board boots normally again."""
    with contextlib.redirect_stdout(Lines(emit)):
        esp = detect_chip(port, 115200, "default_reset", connect_attempts=7)
        try:
            if not esp.secure_download_mode:
                esp = esp.run_stub()
                with contextlib.suppress(NotImplementedInROMError):
                    esp.change_baud(460800)
                esp.flash_set_parameters(flash_size_bytes(detect_flash_size(esp) or "4MB"))
            yield esp
        finally:
            with contextlib.suppress(Exception):
                esp.hard_reset()
            with contextlib.suppress(Exception):
                esp._port.close()


def read_board(esp):
    if esp.CHIP_NAME == "ESP8266":
        raise ValueError("暂不支持给 ESP8266 刷固件")
    info = dict(chip=esp.CHIP_NAME, chip_id=esp.IMAGE_CHIP_ID, secure_download=bool(esp.secure_download_mode))
    if info["secure_download"]:
        return info
    size = detect_flash_size(esp)
    if not size:
        raise ValueError("读不出板子的闪存大小，可能是闪存芯片接触不良。")
    info.update(
        description=esp.get_chip_description(),
        revision=esp.get_chip_revision(),
        mac=":".join(f"{b:02x}" for b in esp.read_mac()),
        flash_size=flash_size_bytes(size),
        secure_boot=bool(esp.get_secure_boot_enabled()),
        flash_encryption=bool(esp.get_flash_encryption_enabled()),
        table=esp.read_flash(firmware.PT_OFFSET, firmware.PT_SIZE),
    )
    with contextlib.suppress(firmware.FirmwareError):  # a blank chip has no table: nothing installed
        parts = firmware.parse_partitions(info["table"])
        otadata = next((p for p in parts if p["type"] == 1 and p["subtype"] == 0), None)
        slot = firmware.boot_slot(parts, esp.read_flash(otadata["offset"], 2 * firmware.SECTOR) if otadata else b"")
        if slot:  # board names sit in the first ~50 KB of a xiaozhi app
            info["installed"] = firmware.installed(esp.read_flash(slot["offset"], min(slot["size"], 0x10000)), slot)
    return info


def block_timeout(esp, size):
    """esptool's timeout for one compressed block (the stub erases while it inflates)."""
    if esp.IS_STUB:
        size += (-(-size // (32 * 1024)) + 1) * 64 * 1024
    return timeout_per_mb(ERASE_WRITE_TIMEOUT_PER_MB, size)


def write_region(esp, offset, data, progress):
    """Compressed write exactly like esptool's write_flash, then compare the chip's MD5."""
    data = pad_to(data, 4)
    packed = zlib.compress(data, 9)
    esp.flash_defl_begin(len(data), len(packed), offset)
    inflate, seq, done, timeout = zlib.decompressobj(), 0, 0, DEFAULT_TIMEOUT
    for i in range(0, len(packed), esp.FLASH_WRITE_SIZE):
        block = packed[i:i + esp.FLASH_WRITE_SIZE]
        size = len(inflate.decompress(block))
        if not esp.IS_STUB:
            timeout = block_timeout(esp, size)
        esp.flash_defl_block(block, seq, timeout=timeout)
        if esp.IS_STUB:
            timeout = block_timeout(esp, size)
        seq += 1
        done += size
        progress(done)
    if esp.IS_STUB:
        esp.flash_defl_finish(reboot=False, timeout=timeout)
    return len(data)


def verify_region(esp, offset, data):
    data = pad_to(data, 4)
    return esp.flash_md5sum(offset, len(data)) == hashlib.md5(data).hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def watch_boot(port, seconds, emit, wait=20, settle=1.5):
    """After the reset: wait for the port to come back, open it without resetting and read the log.

    Returns None when the board never came back, else the lines plus how many times the USB
    device dropped after it had started talking (each such drop is a reboot)."""
    time.sleep(settle)  # the reset takes the USB device off the bus; don't grab the old node
    deadline = time.monotonic() + wait
    lines, handle, pending, drops, end = [], None, b"", 0, None
    try:
        while time.monotonic() < (end or deadline):
            if not handle:
                if port in {p.device for p in list_ports.comports()}:
                    try:
                        handle = serial.Serial(port=None, baudrate=115200, timeout=0.1)
                        handle.port = port
                        open_without_reset(handle)
                        end = end or time.monotonic() + seconds
                    except (OSError, serial.SerialException):
                        handle = None
                if not handle:
                    time.sleep(0.2)
                continue
            try:
                pending += handle.read(4096)
            except (OSError, serial.SerialException):
                handle.close()
                handle = None
                drops += bool(lines)
                continue
            *done, pending = pending.split(b"\n")
            for raw in done:
                line = raw.decode("utf-8", "replace").rstrip("\r")
                lines.append(line)
                emit(line)
    finally:
        if handle:
            handle.close()
    if end is None:
        return None
    if pending:
        lines.append(pending.decode("utf-8", "replace"))
    return dict(lines=lines, drops=drops)


class Flasher:
    def __init__(self, bench, session=esp_session, watch=watch_boot):
        self.b = bench
        self.session = session
        self.watch = watch
        self.fw = self.board = self.plan = self.result = self.done_backup = None
        self.factory_reset = False
        self.step = 0
        self.progress = None
        self.cancel = False
        self.loop = None

    # ---------------------------------------------------------------- state

    @property
    def root(self):
        return self.b.backups

    def state(self):
        fw = self.fw
        board = self.board and {k: v for k, v in self.board.items() if k != "table"}
        if board and self.board.get("table") is not None:
            with contextlib.suppress(firmware.FirmwareError):
                board["partitions"] = firmware.parse_partitions(self.board["table"])
        plan = self.plan and dict(
            {k: v for k, v in self.plan.items() if k != "writes"},
            writes=[{k: v for k, v in w.items() if k != "data"} for w in self.plan["writes"]],
            blocked=bool(firmware.blocked(self.plan)),
            needs_confirm=any(c["confirm"] for c in self.plan["checks"]),
        )
        return dict(
            steps=STEPS, step=self.step, progress=self.progress, result=self.result,
            factory_reset=self.factory_reset, running=bool(self.b.job and self.b.taskname.startswith("刷固件")),
            firmware=fw and dict(source=fw["source"], kind=fw["kind"], settings=fw["settings"],
                                 files=[dict(offset=r["offset"], size=len(r["data"]), label=r["label"]) for r in fw["regions"]]),
            board=board, plan=plan, backup=self.done_backup, backups=self.backups(), backup_dir=str(self.root),
        )

    def say(self, text, level="INFO"):
        """Log from the worker thread (SQLite lives on the event-loop thread)."""
        self.loop.call_soon_threadsafe(self.b.log, level, "刷固件", text)

    def raw(self, text):
        self.loop.call_soon_threadsafe(self.b.log, "INFO", "esptool", text)

    def set_progress(self, text, done=0, total=0):
        self.progress = dict(text=text, done=done, total=total)

    # ---------------------------------------------------------------- choosing

    def open(self, path=None, name="", data=None, loaded=None):
        if self.b.job:
            raise ValueError("请等当前任务结束再换固件。")
        fw = loaded or (firmware.load(path) if path else firmware.load_bytes(name, data))
        self.fw, self.board, self.plan, self.result, self.done_backup = fw, None, None, None, None
        self.step, self.progress, self.factory_reset = 1, None, False
        self.b.log("INFO", "刷固件", f"已选固件 · {fw['source']}")

    def close(self):
        if self.b.job:
            raise ValueError("刷写进行中，不能关闭。")
        self.fw = self.board = self.plan = self.result = self.done_backup = None
        self.step, self.progress = 0, None

    def request_stop(self):
        if self.step >= 4:
            raise ValueError("正在往板子里写，中途停下会让板子开不了机，请等它完成（一般几分钟）。")
        self.cancel = True
        self.b.log("WARN", "刷固件", "收到停止请求，会在开始写入前停下。")

    def set_factory(self, value):
        if self.b.job:
            raise ValueError("请等当前任务结束。")
        self.factory_reset = bool(value)
        if self.fw and self.board:
            self.plan = firmware.assess(self.fw, self.board, self.factory_reset)

    def reported_board(self, port):
        """The board name the firmware printed at boot (xiaozhi "SKU=..."), from this or earlier sessions."""
        entry = self.b.components.identity.get("board") if self.b.ser else None
        if entry:
            return dict(value=entry["value"], log_id=entry["log_id"])
        row = self.b.db.execute(
            "SELECT l.id,l.message FROM logs l JOIN runs r ON r.id=l.run WHERE l.level='RX' AND r.port=? "
            "AND l.message LIKE '%SKU=%' ORDER BY l.id DESC LIMIT 1", (port,)).fetchone()
        if row and (m := XIAOZHI.fullmatch(re.sub(r"\x1b\[[0-9;]*m", "", row[1]).strip())):
            return dict(value=m[1], log_id=row[0])
        return None

    # ---------------------------------------------------------------- jobs

    async def _thread(self, fn, *args):
        """Run blocking esptool work; a stop request only takes effect between safe points."""
        self.loop = asyncio.get_running_loop()
        work = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            self.cancel = True
            with contextlib.suppress(Exception):
                await work
            raise

    async def inspect(self, port):
        if not self.fw:
            raise ValueError("请先选固件文件。")
        reported = self.reported_board(port)
        await self.b.disconnect()
        self.b.bind(port)
        self.board = self.plan = self.result = self.done_backup = None
        self.cancel = False
        self.step = 1
        self.set_progress("正在连接板子并读取芯片信息（板子会重启一次）…")
        try:
            board = await self._thread(self._inspect, port)
        except Exception as exc:
            self.progress = None
            self.result = dict(state="fail", text="读不到板子信息：" + plain_error(exc))
            raise ValueError(self.result["text"]) from exc
        board["reported_board"] = reported
        self.board = board
        self.plan = firmware.assess(self.fw, board, self.factory_reset)
        self.step = 2
        self.progress = None
        bad = firmware.blocked(self.plan)
        self.b.log("WARN" if bad else "INFO", "刷固件",
                   "检查完成 · " + ("不能刷：" + bad[0]["detail"] if bad else "可以刷，请看清会改哪些地方"))

    def _inspect(self, port):
        with self.session(port, self.raw) as esp:
            return read_board(esp)

    async def flash(self, port, confirm_board=False, full_backup=False):
        plan = self.plan
        if not plan or not self.board:
            raise ValueError("请先完成“识别和检查”。")
        if firmware.blocked(plan):
            raise ValueError("检查没有通过，不能刷：" + firmware.blocked(plan)[0]["detail"])
        if any(c["confirm"] for c in plan["checks"]) and confirm_board is not True:
            raise ValueError("固件和板子型号对不上，需要你再确认一次才能刷。")
        await self.b.disconnect()
        self.b.bind(port)
        self.cancel = False
        self.result = self.done_backup = None
        self.step = 3
        try:
            backup = await self._thread(self._flash, port, plan, full_backup)
        except Exception as exc:
            self.progress = None
            touched = self.step >= 4
            self.result = dict(state="fail", text="刷写没有完成：" + plain_error(exc) +
                               ("。板子原来的内容已经备份，可以点“恢复刷写前的样子”。" if touched else "。板子还没被改动。"))
            raise ValueError(self.result["text"]) from exc
        self.step = 6
        self.set_progress("等板子重新开机，查看开机信息…")
        await self.boot_check(port, (plan["firmware"] or {}).get("desc"))
        self._finish_manifest(backup)

    def _flash(self, port, plan, full_backup):
        with self.session(port, self.raw) as esp:
            board = read_board(esp)
            if board.get("mac") != self.board.get("mac") or board.get("table") != self.board.get("table"):
                raise ValueError("板子和刚才检查的不一样（换了板子或被改过），请重新检查")
            self.step = 3
            backup = self._backup(esp, plan, full_backup)
            if self.cancel:
                raise ValueError("已按你的要求在写入前停止")
            self.step = 4
            total = sum(w["size"] for w in plan["writes"])
            written = 0
            for w in plan["writes"]:
                old = backup["_data"].get(w["offset"])
                if w["blank"] and old is not None and firmware.blank(old):
                    self.say(f"{firmware.hexaddr(w['offset'])} {w['where']} 已经是空的，不用再擦。")
                    written += w["size"]
                    continue
                self.say(f"写入 {firmware.hexaddr(w['offset'])} · {w['where']} · {w['size'] >> 10} KB")
                base = written
                written += write_region(esp, w["offset"], w["data"], lambda n, w=w, base=base: self.set_progress(
                    f"正在写入 {w['where']}…", base + n, total))
            self.step = 5
            for i, w in enumerate(plan["writes"]):
                self.set_progress(f"正在校验 {w['where']}…", i, len(plan["writes"]))
                if not verify_region(esp, w["offset"], w["data"]):
                    raise ValueError(f"校验没通过：{w['where']}（{firmware.hexaddr(w['offset'])}）写进去的内容不对")
            self.say("全部写入并校验通过，正在重启板子。")
            backup.pop("_data")
            return backup

    def _backup(self, esp, plan, full_backup):
        """Read every range that will change (plus partition table and settings) before writing anything."""
        key = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        folder = self.root / ("flash-" + key)
        folder.mkdir(parents=True)
        desc = (plan["firmware"] or {}).get("desc") or {}
        manifest = dict(id=folder.name, created=now(), chip=self.board["chip"], mac=self.board["mac"],
                        flash_size=self.board["flash_size"], complete=False,
                        reason=f"刷写 {desc.get('project', '')} {desc.get('version', '')} 之前".strip(),
                        firmware=desc, before=None, regions=[], full=None)
        total = sum(r["end"] - r["start"] for r in plan["backup"]) + (self.board["flash_size"] if full_backup else 0)
        done = 0
        data_by_offset = {}
        for r in plan["backup"]:
            size = r["end"] - r["start"]
            data = esp.read_flash(r["start"], size, lambda n, t, base=done: self.set_progress(
                "正在备份板子原来的内容…", base + n, total))
            done += size
            name = f"{firmware.hexaddr(r['start'])}-{firmware.hexaddr(r['end'])}.bin"
            (folder / name).write_bytes(data)
            manifest["regions"].append(dict(offset=r["start"], size=size, file=name, sha256=sha(data), restore=r["restore"]))
            for w in plan["writes"]:
                if r["start"] <= w["offset"] and w["offset"] + w["size"] <= r["end"]:
                    data_by_offset[w["offset"]] = data[w["offset"] - r["start"]:w["offset"] - r["start"] + w["size"]]
        boot_app = next((w for w in plan["writes"] if w["where"].startswith(("应用", "出厂应用"))), None)
        if boot_app and (old := data_by_offset.get(boot_app["offset"])):
            with contextlib.suppress(firmware.FirmwareError):
                manifest["before"] = (firmware.esp_image(old) or {}).get("desc")
        if full_backup:
            size = self.board["flash_size"]
            data = esp.read_flash(0, size, lambda n, t, base=done: self.set_progress(
                "正在备份整块闪存…", base + n, total))
            (folder / "full-flash.bin").write_bytes(data)
            manifest["full"] = dict(file="full-flash.bin", size=size, sha256=sha(data))
        self._write_manifest(folder, manifest)
        self.done_backup = dict(id=manifest["id"], folder=str(folder))
        self.say(f"备份完成 · {folder}")
        manifest["_data"] = data_by_offset
        return manifest

    @staticmethod
    def _write_manifest(folder, manifest):
        clean = {k: v for k, v in manifest.items() if not k.startswith("_")}
        (folder / "manifest.json").write_text(json.dumps(clean, ensure_ascii=False, indent=2))
        sums = [f"{r['sha256']}  {r['file']}" for r in clean["regions"]]
        if clean.get("full"):
            sums.append(f"{clean['full']['sha256']}  {clean['full']['file']}")
        (folder / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n")

    def _finish_manifest(self, backup):
        backup["complete"] = True
        backup["result"] = self.result
        self._write_manifest(self.root / backup["id"], backup)

    async def boot_check(self, port, expected):
        self.loop = asyncio.get_running_loop()
        seen = await asyncio.to_thread(self.watch, port, BOOT_WATCH,
                                       lambda line: self.loop.call_soon_threadsafe(self.b.log, "RX", "开机确认", line))
        self.result = firmware.boot_verdict((seen or {}).get("lines", []), expected, came_back=seen is not None,
                                            drops=(seen or {}).get("drops", 0))
        self.step = 7 if self.result["state"] != "fail" else 6
        self.progress = None
        self.b.log({"ok": "PASS", "fail": "ERROR"}.get(self.result["state"], "WARN"), "刷固件", self.result["text"])

    # ---------------------------------------------------------------- backups

    def backups(self):
        rows = []
        for path in sorted(self.root.glob("flash-*/manifest.json"), reverse=True)[:20]:
            with contextlib.suppress(OSError, ValueError):
                m = json.loads(path.read_text())
                rows.append(dict(id=m["id"], created=m["created"], reason=m["reason"], mac=m["mac"],
                                 complete=m.get("complete", False), before=m.get("before"), folder=str(path.parent),
                                 size=sum(r["size"] for r in m["regions"]), full=bool(m.get("full"))))
        return rows

    def load_backup(self, key):
        if not re.fullmatch(r"flash-[0-9a-z-]{1,40}", key or ""):
            raise ValueError("备份编号无效。")
        folder = self.root / key
        manifest = json.loads((folder / "manifest.json").read_text())
        data = {}
        for r in manifest["regions"]:
            if not r["restore"]:
                continue
            blob = (folder / r["file"]).read_bytes()
            if len(blob) != r["size"] or sha(blob) != r["sha256"]:
                raise ValueError(f"备份文件 {r['file']} 和记录的校验值对不上，可能被改过，不能用来恢复。")
            data[r["offset"]] = blob
        if not data:
            raise ValueError("这个备份里没有需要写回的内容。")
        return manifest, data

    async def restore(self, port, key):
        manifest, data = self.load_backup(key)
        await self.b.disconnect()
        self.b.bind(port)
        self.cancel = False
        self.result = None
        self.step = 4
        try:
            await self._thread(self._restore, port, manifest, data)
        except Exception as exc:
            self.progress = None
            self.result = dict(state="fail", text="恢复没有完成：" + plain_error(exc) + "。可以再试一次。")
            raise ValueError(self.result["text"]) from exc
        self.step = 6
        self.set_progress("等板子重新开机，查看开机信息…")
        await self.boot_check(port, manifest.get("before"))
        if self.result["state"] != "fail":
            self.result["text"] = "已恢复到刷写前的样子。" + self.result["text"].replace("新固件", "原来的固件")

    def _restore(self, port, manifest, data):
        with self.session(port, self.raw) as esp:
            mac = ":".join(f"{b:02x}" for b in esp.read_mac())
            if mac != manifest["mac"]:
                raise ValueError("这个备份来自另一块板子，不能写到这块板子上")
            total = sum(len(v) for v in data.values())
            done = 0
            for offset, blob in sorted(data.items()):
                self.say(f"写回 {firmware.hexaddr(offset)} · {len(blob) >> 10} KB")
                base = done
                done += write_region(esp, offset, blob, lambda n, base=base: self.set_progress("正在写回备份…", base + n, total))
            self.step = 5
            for offset, blob in sorted(data.items()):
                if not verify_region(esp, offset, blob):
                    raise ValueError(f"校验没通过：{firmware.hexaddr(offset)} 写回的内容不对")
            self.say("备份已全部写回并校验通过，正在重启板子。")


def plain_error(exc):
    """esptool's messages are for engineers; keep them but lead with what to do."""
    message = str(exc).strip() or exc.__class__.__name__
    if "Failed to connect" in message or "No serial data received" in message:
        return ("连不上板子的下载模式。请确认 USB 线插在板子的 USB 口上；还不行就按住 BOOT 键再按一下 RESET 键，"
                "松开后重试（" + message[:80] + "）")
    if "could not open port" in message or "Resource busy" in message or "Permission denied" in message:
        return "串口被别的程序占用了，请关掉串口监视器或其他刷机软件再试"
    return message
