import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import faultpack
from bench import Bench
from monitor import AutoUSB
from profiles import validate
from replies import ReplyMatch, try_sample


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.b = Bench(Path(self.dir.name) / "history.sqlite3")
        self.addCleanup(self.b.db.close)
        faultpack.install(self.b.db)

    def test_reply_boundaries_and_examples(self):
        t = {
            "name": "ping",
            "kind": "serial",
            "step": "query",
            "tx": "ping",
            "expect": "OK",
            "success_sample": "OK",
            "failure_sample": "NOT OK",
        }
        validate({"name": "fixture", "tests": [t]})
        m = ReplyMatch(t)
        self.assertIsNone(m.feed(b"NOT OK\r\nO"))
        self.assertIsNone(m.feed(b"K"))
        self.assertEqual(m.feed(b"\n"), "通过")
        self.assertEqual(try_sample(t, "NOT OK"), "未匹配")
        with self.assertRaises(ValueError):
            validate({"name": "fixture", "tests": [dict(t, match="contains")]})
        h = {"expect": "00 ff", "reject": "01 ff", "match": "frame", "encoding": "hex"}
        m = ReplyMatch(h)
        self.assertIsNone(m.feed(bytes.fromhex("ab 00 ff ff 00")))
        self.assertEqual(m.feed(b"\xff"), "通过")
        self.assertEqual(try_sample(h, "01 ff"), "失败")
        self.assertEqual(try_sample(h, "ab 00 ff"), "未匹配")
        m = ReplyMatch(t)
        self.assertIsNone(m.feed(b"n" * 8193))
        self.assertIsNone(m.feed(b"OK\n"))
        self.assertEqual(m.feed(b"OK\n"), "通过")

    def test_slow_event_and_once(self):
        comp = self.b.components
        self.b.log(
            "RX", "串口", '{"bb":1,"part":"temp","status":"sample","value":"23 C"}'
        )

        def status():
            return comp.states(self.b.profile, True)[0]["status"]

        def age(seconds):
            import time

            comp.last["temp"]["tick"] = time.monotonic() - seconds

        age(9)
        self.assertEqual(status(), "上报超时")
        comp.set_rule("temp", "periodic", 30)
        self.assertEqual(status(), "有读数")
        age(91)
        self.assertEqual(status(), "上报超时")
        for mode in ["event", "once"]:
            comp.set_rule("temp", mode, None)
            self.assertEqual(status(), "有读数")
        comp.use_profile("other")
        self.assertEqual(status(), "上报超时")
        comp.use_profile(self.b.profile)
        self.assertEqual(status(), "有读数")

    async def test_device_book_migration_and_ambiguity(self):
        auto = AutoUSB(self.b, asyncio.Lock())
        a = {"path": "P1", "vid": 1, "pid": 2, "serial": "a"}
        b = dict(a, path="P2", serial="b")
        legacy = {"identity": [1, 2, "a"], "baud": 57600, "framing": "8E1"}
        self.b.db.execute(
            "INSERT INTO preferences VALUES('usb_identity',?)", (json.dumps(legacy),)
        )
        auto = AutoUSB(self.b, asyncio.Lock())
        with (
            patch.object(self.b, "ports", return_value=[a, b]) as ports,
            patch.object(self.b, "connect") as connect,
        ):
            auto.remember("P2", 115200, "8N1")
            auto.rename(json.dumps([1, 2, "a"]), "样板")
            await auto.check()
            connect.assert_not_called()
            self.assertEqual(auto.describe([a, b])[0]["saved"]["name"], "样板")
            ports.return_value = [a, dict(a, path="P3")]
            await auto.check()
            self.assertIsNone(auto.describe(ports.return_value)[0]["saved"])
            connect.assert_not_called()
            ports.return_value = [dict(a, path="P9")]
            await auto.check()
            connect.assert_called_once_with("P9", 57600, "8E1")

    async def test_share_archive_atomic_and_not_live(self):
        b = self.b
        live = b.run
        b.log("RX", "串口", "I (2) app_init: Project name: fixture")
        b.log("RX", "串口", "E (3) wifi: password=abcdef ssid=myLAN")
        b.log("WARN", "检测", "device-id=private host=192.168.4.1 a@b.com")
        for i in range(45):
            b.log("INFO", "检测", str(i))
        pack = await faultpack.excerpt(b, live)
        self.assertEqual(pack["settings"]["固件"], "fixture")
        encoded = json.dumps(pack)
        for secret in ["abcdef", "myLAN", "private", "192.168.4.1", "a@b.com"]:
            self.assertNotIn(secret, encoded)
        self.assertNotIn('"raw"', encoded)
        imported = faultpack.read_pack(b, pack)
        self.assertNotEqual(live, imported)
        self.assertEqual(b.run, live)
        self.assertEqual(imported, faultpack.read_pack(b, pack))
        with self.assertRaises(ValueError):
            faultpack.read_pack(b, dict(pack, events=[{"at": "bad"}]))
        self.assertEqual(b.db.execute("SELECT count(*) FROM runs").fetchone()[0], 2)
        self.assertEqual(b.report(imported)["port"], "离线故障包")

    async def test_endurance_metrics_and_boundaries(self):
        b = self.b
        b.ser = True
        service = b.endurance
        service.start(b, "原版/无摄像头", 1)
        with patch("endurance.time.monotonic", side_effect=lambda: tick[0]):
            tick = [service.begin]

            def sample(line):
                b.log("RX", "串口", line)

            sample("ESP-ROM:esp32s3-20210327")
            tick[0] += 0.25
            sample("BoardBenchProbe ready")
            temp = '{"bb":1,"part":"temp","status":"sample","value":"23 C"}'
            sample(temp)
            tick[0] += 15
            sample(temp)
            service.stop()
        r = service.history()[0]
        self.assertEqual((r["boots"], r["startup_ms"], r["gaps"]["temp"]), (1, 250, 15))
        service.baseline(r["id"])
        self.assertTrue(service.history()[0]["reference"])
        service.start(b, "原版/无摄像头", 1)
        service.stop("连接中断")
        with self.assertRaises(ValueError):
            service.baseline(service.history()[0]["id"])
        self.assertFalse(service.active)
        self.assertEqual(b.tx, 0)
