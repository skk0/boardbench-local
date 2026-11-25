"""Backup, flashing and restore against a fake esptool loader: NVS stays untouched, restore is exact."""

import contextlib
import hashlib
import json
import re
import tempfile
import unittest
import zlib
from pathlib import Path

import firmware as fw
import flasher
from aiohttp.test_utils import TestClient, TestServer
from app import make_app
from bench import Bench
from test_firmware import MB, image, merged

MAC = (0xF4, 0x12, 0xFA, 0x00, 0x00, 0x01)


class FakeESP:
    """The esptool stub-loader calls the flasher uses, over an in-memory 4 MB flash.
    It has no erase_flash/eFuse methods: calling one would fail the test."""

    CHIP_NAME, IMAGE_CHIP_ID, IS_STUB, FLASH_WRITE_SIZE = "ESP32-S3", 9, True, 0x4000
    secure_download_mode = False

    def __init__(self, flash, mac=MAC):
        self.flash, self.mac, self.writes = flash, mac, []

    def flash_id(self):
        return 0x16 << 16 | 0x40EF  # 4 MB

    def get_chip_description(self):
        return "ESP32-S3 (QFN56) (revision v0.2)"

    def get_chip_revision(self):
        return 2

    def read_mac(self):
        return self.mac

    def get_secure_boot_enabled(self):
        return 0

    def get_flash_encryption_enabled(self):
        return False

    def read_flash(self, offset, length, progress_fn=None):
        if progress_fn:
            progress_fn(length, length)
        return bytes(self.flash[offset:offset + length])

    def flash_defl_begin(self, size, compsize, offset):
        start, end = fw.align_down(offset), fw.align_up(offset + size)
        self.flash[start:end] = b"\xff" * (end - start)  # the stub erases whole sectors first
        self.cursor, self.inflate = offset, zlib.decompressobj()
        self.writes.append((offset, size))

    def flash_defl_block(self, data, seq, timeout):
        out = self.inflate.decompress(data)
        self.flash[self.cursor:self.cursor + len(out)] = out
        self.cursor += len(out)

    def flash_defl_finish(self, reboot=False, timeout=None):
        pass

    def flash_md5sum(self, addr, size):
        return hashlib.md5(self.flash[addr:addr + size]).hexdigest()


def board_flash(nvs=b"WIFI=home;VOLUME=1"):
    """What is on the board before flashing: an older app, real settings in NVS."""
    data = bytearray(merged(app=image(version="1.0.0")))
    data[0x9000:0x9000 + len(nvs)] = nvs
    data[0x120000:0x120010] = b"OLD-OTA1-CONTENT"
    return data + b"\xff" * (4 * MB - len(data))


class FlasherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.b = Bench(Path(self.temp.name) / "history.sqlite3")
        self.flash = board_flash()
        self.esp = FakeESP(self.flash)
        self.boot_lines = ["I (311) app_init: App version:      2.6.0"]

        @contextlib.contextmanager
        def session(port, emit):
            yield self.esp

        self.f = flasher.Flasher(self.b, session=session,
                                 watch=lambda port, seconds, emit: dict(lines=self.boot_lines, drops=0))

    async def asyncTearDown(self):
        self.b.db.close()
        self.temp.cleanup()

    async def prepare(self, data=None, name="m.bin"):
        self.f.open(name=name, data=data or merged())
        await self.f.inspect("/dev/fake")
        self.assertFalse(fw.blocked(self.f.plan), self.f.plan["checks"])

    async def test_merged_flash_keeps_nvs_backs_up_and_verifies(self):
        before = bytes(self.flash)
        await self.prepare()
        await self.f.flash("/dev/fake")
        image_data = merged()
        self.assertEqual(self.flash[0x9000:0xD000], before[0x9000:0xD000], "settings area untouched")
        self.assertEqual(self.flash[:0x9000], image_data[:0x9000])
        self.assertEqual(self.flash[0xD000:len(image_data)], image_data[0xD000:])
        self.assertFalse(any(o < 0xD000 and 0x9000 < o + s for o, s in self.esp.writes))
        self.assertEqual(self.f.result["state"], "ok")
        self.assertEqual(self.f.step, 7)
        folder = Path(self.f.done_backup["folder"])
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["mac"], "f4:12:fa:00:00:01")
        self.assertEqual(manifest["before"]["version"], "1.0.0")
        for r in manifest["regions"]:
            blob = (folder / r["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(blob).hexdigest(), r["sha256"])
            self.assertEqual(blob, before[r["offset"]:r["offset"] + r["size"]])
        nvs = next(r for r in manifest["regions"] if r["offset"] == 0x9000)
        self.assertFalse(nvs["restore"])
        self.assertIn(nvs["sha256"], (folder / "SHA256SUMS.txt").read_text(encoding="utf-8"))

        # Settings change after flashing; restore brings back the old firmware but not old settings.
        self.flash[0x9000:0x9004] = b"NEW!"
        after_flash_nvs = bytes(self.flash[0x9000:0xD000])
        self.boot_lines = ["I (311) app_init: App version:      1.0.0"]
        await self.f.restore("/dev/fake", manifest["id"])
        self.assertEqual(self.f.result["state"], "ok")
        self.assertEqual(bytes(self.flash[:0x9000]), before[:0x9000])
        self.assertEqual(bytes(self.flash[0xD000:]), before[0xD000:])
        self.assertEqual(bytes(self.flash[0x9000:0xD000]), after_flash_nvs)

    async def test_full_backup_is_listed_in_history(self):
        await self.prepare()
        await self.f.flash("/dev/fake", full_backup=True)
        manifest = json.loads((Path(self.f.done_backup["folder"]) / "manifest.json").read_text(encoding="utf-8"))
        full = Path(self.f.done_backup["folder"]) / manifest["full"]["file"]
        self.assertEqual(full.stat().st_size, 4 * MB)
        self.assertEqual(self.f.backups()[0]["id"], manifest["id"])

    async def test_app_only_flash_switches_boot_to_new_app(self):
        self.flash[0xD000:0xD020] = b"\x01" * 32  # board was booting ota_1
        await self.prepare(image(), "app.bin")
        await self.f.flash("/dev/fake")
        self.assertTrue(fw.blank(self.flash[0xD000:0xF000]), "boot choice reset to the first app slot")
        self.assertEqual(fw.esp_image(bytes(self.flash[0x20000:]))["desc"]["version"], "2.6.0")
        self.assertEqual(bytes(self.flash[0x9000:0x9012]), b"WIFI=home;VOLUME=1")

    async def test_refuses_blocked_unconfirmed_or_swapped_board(self):
        self.f.open(name="m.bin", data=merged(app=image(chip_id=0), boot=image(app=False, chip_id=0)))
        await self.f.inspect("/dev/fake")
        with self.assertRaisesRegex(ValueError, "检查没有通过"):
            await self.f.flash("/dev/fake")
        self.f.open(name="m.bin", data=merged(app=image(board="atk-dnesp32s3-box")))
        self.b.components.identity["board"] = dict(value="life-box", log_id=1)
        self.b.ser = object()  # pretend monitoring saw the board's own name
        self.assertEqual(self.f.reported_board("/dev/fake")["value"], "life-box")
        self.b.ser = None
        self.b.log("RX", "串口", "I (5120) Board: UUID=12345678-1234-1234-1234-123456789abc SKU=life-box")
        self.b.bind("/dev/fake")
        await self.f.inspect("/dev/fake")
        with self.assertRaisesRegex(ValueError, "再确认"):
            await self.f.flash("/dev/fake")
        untouched = bytes(self.flash)
        self.esp.mac = (0, 0, 0, 0, 0, 2)
        with self.assertRaisesRegex(ValueError, "不一样"):
            await self.f.flash("/dev/fake", confirm_board=True)
        self.assertEqual(bytes(self.flash), untouched)

    async def test_stop_before_writing_leaves_board_unchanged(self):
        await self.prepare()
        untouched = bytes(self.flash)
        read = self.esp.read_flash

        def read_then_stop(*args, **kw):
            self.f.cancel = True  # what request_stop() sets while the backup is running
            return read(*args, **kw)

        self.esp.read_flash = read_then_stop
        with self.assertRaisesRegex(ValueError, "已按你的要求在写入前停止。板子还没被改动"):
            await self.f.flash("/dev/fake")
        self.assertEqual(bytes(self.flash), untouched)
        self.f.step = 3
        self.f.request_stop()
        self.assertTrue(self.f.cancel)
        self.f.step = 4
        with self.assertRaisesRegex(ValueError, "请等它完成"):
            self.f.request_stop()

    async def test_restore_refuses_other_board_or_tampered_backup(self):
        await self.prepare()
        await self.f.flash("/dev/fake")
        key = self.f.done_backup["id"]
        self.esp.mac = (0, 0, 0, 0, 0, 2)
        with self.assertRaisesRegex(ValueError, "另一块板子"):
            await self.f.restore("/dev/fake", key)
        self.esp.mac = MAC
        folder = Path(self.f.done_backup["folder"])
        part = next(p for p in folder.glob("0x*.bin") if not p.name.startswith("0x009000"))
        part.write_bytes(b"tampered" + part.read_bytes()[8:])
        with self.assertRaisesRegex(ValueError, "校验值对不上"):
            await self.f.restore("/dev/fake", key)
        with self.assertRaisesRegex(ValueError, "无效"):
            self.f.load_backup("../etc")

    async def test_boot_failure_is_reported_in_plain_words(self):
        await self.prepare()
        self.boot_lines = ["rst:0xc (RTC_SW_CPU_RST),boot:0x8", "rst:0xc (RTC_SW_CPU_RST),boot:0x8"]
        await self.f.flash("/dev/fake")
        self.assertEqual(self.f.result["state"], "fail")
        self.assertIn("反复重启", self.f.result["text"])

    def test_never_erases_chip_or_touches_efuses(self):
        source = Path(flasher.__file__).read_text(encoding="utf-8") + Path(fw.__file__).read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""|#.*', "", source, flags=re.S)
        for forbidden in ("erase_flash", "erase_region", "espefuse", "burn_", "write_efuse", "erase_all"):
            self.assertNotIn(forbidden, code)


class FlashRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(TestServer(make_app(Path(self.temp.name), monitor_usb=False)))
        await self.client.start_server()
        self.token = (await (await self.client.get("/api/setup")).json())["token"]

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_open_by_path_and_by_upload(self):
        path = Path(self.temp.name) / "merged-binary.bin"
        path.write_bytes(merged())
        headers = {"X-BoardBench": self.token}
        state = await (await self.client.post("/api/flash/open", json={"path": str(path)}, headers=headers)).json()
        self.assertEqual(state["firmware"]["kind"], "merged")
        upload = await self.client.post("/api/flash/open", data=image(), headers={
            **headers, "Content-Type": "application/octet-stream", "X-File-Name": "xiaozhi%20app.bin"})
        state = await upload.json()
        self.assertEqual((state["firmware"]["kind"], state["firmware"]["source"]), ("app", "xiaozhi app.bin"))
        bad = await self.client.post("/api/flash/open", data=b"nope", headers={
            **headers, "Content-Type": "application/octet-stream"})
        self.assertEqual(bad.status, 400)
        self.assertIn("看不出", (await bad.json())["error"])
        self.assertEqual((await self.client.post("/api/flash/open", json={"path": str(path)})).status, 403)


if __name__ == "__main__":
    unittest.main()


class WatchBootTests(unittest.TestCase):
    def test_waits_for_port_and_counts_drops_after_output(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import serial

        script = [[b"rst:0x15 (USB_UART_CHIP_RESET),boot:0x8\r\nI (900) Display: re", b"ady\r\n", OSError()],
                  [b"I (950) again\r\n", OSError()], [b"I (990) third\r\n"]]
        listed = iter([[], [], ["/dev/b"]] + [["/dev/b"]] * 10000)

        class Port:
            def __init__(self, **kw):
                self.chunks = script.pop(0) if script else []
                self.dtr = self.rts = False

            def open(self):
                pass

            def read(self, n):
                if not self.chunks:
                    return b""
                item = self.chunks.pop(0)
                if isinstance(item, Exception):
                    raise serial.SerialException("gone")
                return item

            def close(self):
                pass

        with patch.object(flasher.list_ports, "comports", lambda: [SimpleNamespace(device=d) for d in next(listed)]), \
                patch.object(flasher.serial, "Serial", Port):
            seen = flasher.watch_boot("/dev/b", 0.6, lambda line: None, wait=5, settle=0)
        self.assertEqual(seen["drops"], 2)
        self.assertIn("I (900) Display: ready", seen["lines"])
        self.assertEqual(fw.boot_verdict(seen["lines"], drops=seen["drops"])["state"], "fail")
        with patch.object(flasher.list_ports, "comports", lambda: []):
            self.assertIsNone(flasher.watch_boot("/dev/b", 1, lambda line: None, wait=0.3, settle=0))
