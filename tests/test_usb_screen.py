import asyncio
import base64
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import patch

from boardbench.bench import Bench
from boardbench.devices.monitor import AutoUSB
from boardbench.devices.usb_screen import SerialReplyFilter, UsbScreen


class UsbScreens(unittest.IsolatedAsyncioTestCase):
    async def test_capability_then_request_and_validated_frame(self):
        screen = UsbScreen()
        written = []
        def write(data):
            written.append(data)
            return len(data)
        bench = SimpleNamespace(ser=SimpleNamespace(write=write), transport="串口", tx=0, connection_id=3, waiter=None)
        self.assertFalse(screen.observe("I (1) Board: ESP32-S3"))
        with self.assertRaises(ValueError):
            await screen.capture(bench, 3)
        self.assertFalse(written)
        self.assertTrue(screen.observe("BBS1 CAP NO_CAPTURE"))
        self.assertEqual(screen.state(True)["status"], "unsupported")
        screen.observe("BBS1 CAP JPEG")
        with self.assertRaises(ValueError):
            await screen.capture(bench, 2)
        frame = bytes.fromhex("ffd8 ffc0 000b 08 0040 0080 01 01 1100 ffd9")
        for valid in (True, False):
            task = asyncio.create_task(screen.capture(bench, 3))
            await asyncio.sleep(0)
            key = written[-1].decode().split()[-1]
            screen.observe(f"BBS1 DATA {key} 0 {frame.hex()}")
            crc = zlib.crc32(frame) if valid else 0
            screen.observe(f"BBS1 END {key} {len(frame)} {crc:08x}")
            if valid:
                self.assertEqual(await task, frame)
            else:
                with self.assertRaises(ValueError):
                    await task
        task = asyncio.create_task(screen.capture(bench, 3))
        await asyncio.sleep(0)
        screen.reset()
        with self.assertRaises(ValueError):
            await task
        self.assertEqual(screen.state(True)["status"], "unknown")

    async def test_snap_discovery_manual_and_push(self):
        frame = bytes.fromhex("ffd8 ffc0 000b 08 0040 0080 01 01 1100 ffd9")
        lines = [f"LBSNAP:BEGIN {len(frame)}", "LBSNAP:" + base64.b64encode(frame).decode(), "LBSNAP:END"]
        written = []
        def write(data):
            written.append(data)
            return len(data)
        bench = SimpleNamespace(ser=SimpleNamespace(write=write), transport="串口", tx=0, connection_id=4, waiter=None)
        screen = UsbScreen()
        for announcement in ("I (422) Generic: Debug console ready", "LBSNAP:BEGIN 9999999", "LBSNAP:END"):
            screen.observe(announcement)
        self.assertEqual(screen.state(True)["status"], "unknown")
        for manual, announcement in [(True, ""), (False, "\x1b[32mI (422) LifeBoxDisplay: Debug console ready\x1b[0m"),
                                     (False, "LBSNAP:READY snap")]:
            screen.reset()
            screen.observe(announcement)
            if not manual:
                self.assertEqual(screen.state(True)["protocol"], "snap")
            task = asyncio.create_task(screen.capture(bench, 4, manual_snap=manual))
            await asyncio.sleep(0)
            self.assertEqual(written.pop(), b"snap\n")
            for line in lines:
                self.assertTrue(screen.observe(line))
            self.assertEqual(await task, frame)
        screen.reset()
        for line in lines:
            screen.observe(line)
        self.assertEqual(screen.state(True)["protocol"], "LBSNAP")
        self.assertEqual(await screen.capture(bench, 4), frame)
        task = asyncio.create_task(screen.capture(bench, 4))
        await asyncio.sleep(0)
        for line in [lines[0], "LBSNAP:@@@@"]:
            screen.observe(line)
        with self.assertRaisesRegex(ValueError, "LBSNAP"):
            await task
        self.assertFalse(written)
        screen.reset()
        with self.assertRaises(ValueError):
            await screen.capture(bench, 3, manual_snap=True)
        self.assertFalse(written)

    def test_serial_reply_filter_preserves_binary_and_split_protocol_markers(self):
        replies = SerialReplyFilter()
        data = [b"OK\r", b"\nLB", b"SNAP:BEGIN 40\nBBS", b"1 DATA abc\n", b"\x02\xff", b"\x03\nB", b"ACK"]
        self.assertEqual(b"".join(replies.feed(d) for d in data), b"OK\r\n\x02\xff\x03\nBACK")
        self.assertEqual(replies.feed(b"\nL"), b"\n")
        self.assertEqual(replies.flush_prefix(), b"L")

    async def test_only_new_unique_port_is_opened(self):
        b = Bench(":memory:")
        try:
            auto = AutoUSB(b, asyncio.Lock())
            port = dict(path="COM7", vid=0x303A, pid=0x1001, serial="abc", label="ESP USB")
            with patch.object(b, "ports", return_value=[]) as ports, patch.object(b, "connect") as connect:
                await auto.check()
                ports.return_value = [port]
                await auto.check()
                connect.assert_called_once_with("COM7", 115200, "8N1")
        finally:
            b.db.close()
