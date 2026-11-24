import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import serial

from bench import Bench
from monitor import AutoUSB, decode


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.b = Bench(":memory:")
        self.auto = AutoUSB(self.b, asyncio.Lock())

    async def asyncTearDown(self):
        await self.b.stop()
        await self.b.disconnect()
        self.b.db.close()

    async def test_auto_usb_requires_unique_explicit_identity(self):
        first = dict(
            path="COM7", vid=0x1A86, pid=0x55D3, serial="fixture-123", label="test"
        )
        unknown = dict(first, path="COM8", serial="unknown")
        with (
            patch.object(self.b, "ports", return_value=[first]) as ports,
            patch.object(self.b, "connect") as connect,
        ):
            await self.auto.check()
            connect.assert_not_called()
            self.auto.remember(first["path"], 57600, "8E1")
            ports.return_value = [dict(first, path="COM9")]
            await self.auto.check()
            connect.assert_called_once_with("COM9", 57600, "8E1")
            connect.reset_mock()
            self.auto.ignored.add("COM9")
            await self.auto.check()
            connect.assert_not_called()
            ports.return_value = [first, dict(first, path="COM9")]
            await self.auto.check()
            connect.assert_not_called()
            ports.return_value = [unknown]
            self.auto.remember("COM8", 115200, "8N1")
            await self.auto.check()
            connect.assert_not_called()

    async def test_telemetry_stale_disconnect_and_log_index(self):
        self.b.new("正点原子 · DNESP32S3B BOX")
        self.b.ser = serial.serial_for_url("loop://", timeout=0)
        self.b.reader = asyncio.create_task(self.b.read_loop())
        self.b.ser.write(
            b'{"bb":1,"part":"imu","status":"sample","value":"ax=0.25"}\nE (84) wifi: association failed\n'
        )
        await asyncio.sleep(0.12)
        parts = {p["part"]: p for p in self.b.components.states(self.b.profile, True)}
        self.assertEqual(parts["imu"]["value"], "ax=0.25")
        self.assertEqual(parts["wifi"]["status"], "固件报错")
        self.assertEqual(parts["speaker"]["status"], "未上报")
        self.assertEqual(self.b.tx, 0)
        self.assertEqual(
            self.b.db.execute("SELECT count(*) FROM component_events").fetchone()[0], 2
        )
        self.b.components.last["imu"]["tick"] -= 12
        self.assertEqual(
            next(
                r
                for r in self.b.components.states(self.b.profile, True)
                if r["part"] == "imu"
            )["status"],
            "上报超时",
        )
        await self.b.disconnect()
        self.assertEqual(
            next(
                r
                for r in self.b.components.states(self.b.profile, False)
                if r["part"] == "imu"
            )["status"],
            "连接断开",
        )

    async def test_disk_guard_stops_without_transmitting(self):
        with tempfile.TemporaryDirectory() as folder:
            b = Bench(Path(folder) / "history.sqlite3")
            b.ser = serial.serial_for_url("loop://", timeout=0)
            with patch(
                "bench.shutil.disk_usage", return_value=SimpleNamespace(free=1000)
            ):
                await b.read_loop()
            self.assertIsNone(b.ser)
            self.assertTrue(b.storage_hold)
            self.assertEqual(b.tx, 0)
            b.db.close()

    def test_power_sample_window_and_connection_scope(self):
        def sample():
            self.b.log("RX", "TCP", '{"bb":1,"part":"power","status":"sample","value":{"battery_v":0}}')
            return self.b.current_rx_log_id

        first = sample()
        last = sample()
        comp = self.b.components
        def power():
            return next(r for r in comp.states(self.b.profile, True) if r["part"] == "power")
        self.assertEqual((power()["first_log_id"], power()["log_id"]), (first, last))
        comp.last["power"]["tick"] -= 60
        self.assertEqual(power()["status"], "有读数")
        comp.last["power"]["tick"] -= 31
        self.assertEqual(power()["status"], "上报超时")
        self.b.new("通用串口设备")
        self.assertEqual(sample(), power()["first_log_id"])

    def test_untrusted_serial_strings_are_not_health(self):
        for val in [
            "imu OK probably",
            '{"bb":1,"status":[],"part":"imu"}',
            '{"bb":1,"status":"sample","part":"<script>"}',
        ]:
            self.assertIsNone(decode(val))
