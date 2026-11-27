import asyncio
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import patch

from boardbench.bench import Bench
from boardbench.devices.monitor import AutoUSB
from boardbench.devices.usb_screen import UsbScreen


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
