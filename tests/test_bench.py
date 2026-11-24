import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import serial
from aiohttp.test_utils import TestClient, TestServer
from aiohttp import ClientSession
from app import make_app
from bench import Bench
from profiles import validate, BUILTINS


class BenchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.b = Bench(":memory:")

    async def asyncTearDown(self):
        await self.b.stop()
        await self.b.disconnect()
        self.b.db.close()

    def test_builtins_and_import(self):
        for name, tests in BUILTINS.items():
            validate(dict(name=name, tests=tests))
        data = {
            "name": "example",
            "tests": [{"name": "x", "step": "do x", "kind": "manual"}],
        }
        self.b.add_profile(data)
        self.b.new("example")
        self.assertEqual(self.b.tests, data["tests"])
        with self.assertRaises(ValueError):
            self.b.add_profile(data)
        with self.assertRaises(ValueError):
            validate({"name": "a", "tests": [{"kind": "script"}]})

    def test_manual_history(self):
        self.b.result("display", "失败", "observed missing row")
        old = self.b.run
        self.b.new("ESP32 系列")
        self.assertEqual(self.b.report(old)["results"][0]["state"], "失败")
        self.assertEqual(self.b.report(self.b.run)["results"], [])

    async def test_serial_pass_and_timeout(self):
        self.b.ser = serial.serial_for_url("loop://", timeout=0)
        self.b.reader = asyncio.create_task(self.b.read_loop())
        tests = [
            dict(name="loop", tx="PONG", expect="PONG", timeout=1),
            dict(name="silence", tx="PING", expect="missing", timeout=1),
        ]
        await self.b.cases(tests)
        report = self.b.report(self.b.run)
        self.assertEqual([r["state"] for r in report["results"]], ["通过", "失败"])
        self.assertTrue(
            any(
                r["level"] == "RX" and r["raw"] == "50 4f 4e 47 0d 0a"
                for r in report["logs"]
            )
        )

    async def test_abort(self):
        self.b.ser = serial.serial_for_url("loop://", timeout=0)
        self.b.start(
            "run",
            lambda: self.b.cases([dict(name="a", tx="A", expect="Z", timeout=30)]),
        )
        await asyncio.sleep(0.02)
        await self.b.stop()
        self.assertEqual(self.b.report(self.b.run)["results"][0]["state"], "跳过")

    async def test_tcp_and_disconnect(self):
        async def serve(reader, writer):
            data = await reader.readline()
            writer.write(b"ACK " + data)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        async with server:
            await self.b.network("TCP", "127.0.0.1", server.sockets[0].getsockname()[1])
            await self.b.cases([dict(name="tcp", tx="HELLO", expect="ACK", timeout=2)])
            await asyncio.wait_for(self.b.reader, 2)
            self.assertIsNone(self.b.ser)
        self.assertEqual(self.b.report(self.b.run)["results"][0]["state"], "通过")

    async def test_udp_hex(self):
        loop = asyncio.get_running_loop()

        class Reply(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                self.transport = transport

            def datagram_received(self, data, addr):
                if data == b"\x01\x02":
                    self.transport.sendto(b"\xff\x05", addr)

        transport, _ = await loop.create_datagram_endpoint(
            Reply, local_addr=("127.0.0.1", 0)
        )
        try:
            await self.b.network(
                "UDP", "127.0.0.1", transport.get_extra_info("sockname")[1]
            )
            await self.b.cases(
                [
                    dict(
                        name="udp",
                        encoding="hex",
                        tx="01 02",
                        expect="ff 05",
                        timeout=2,
                    )
                ]
            )
            self.assertEqual(self.b.report(self.b.run)["results"][0]["state"], "通过")
        finally:
            transport.close()

    async def test_device_error_reply(self):
        self.b.ser = serial.serial_for_url("loop://", timeout=0)
        self.b.reader = asyncio.create_task(self.b.read_loop())
        await self.b.cases(
            [
                dict(
                    name="bad",
                    tx="ERR fixture",
                    expect="OK PONG",
                    reject="ERR ",
                    timeout=1,
                )
            ]
        )
        row = self.b.report(self.b.run)["results"][0]
        self.assertEqual(row["state"], "失败")
        self.assertIn("ERR fixture", row["detail"])

    async def test_backup_exact_bytes(self):
        import hashlib
        import sys

        with tempfile.TemporaryDirectory() as d:
            self.b.backups = Path(d)
            script = Path(d) / "fixture.py"
            script.write_text(
                "import sys;from pathlib import Path;Path(sys.argv[-1]).write_bytes(b'firmware-fixture')"
            )
            with patch.object(
                self.b, "worker", return_value=[sys.executable, str(script)]
            ):
                await self.b.backup("fixture-port")
            entry = self.b.report(self.b.run)["backups"][0]
            self.assertEqual(
                entry["sha256"], hashlib.sha256(b"firmware-fixture").hexdigest()
            )
            self.assertEqual(
                (Path(d) / (entry["id"] + ".bin")).read_bytes(), b"firmware-fixture"
            )
            self.assertFalse(list(Path(d).glob("*.part")))

    @unittest.skipIf(os.name == "nt", "Unix PTY")
    async def test_real_pty(self):
        import pty

        master, slave = pty.openpty()
        try:
            device = os.ttyname(slave)
            with patch.object(self.b, "ports", return_value=[dict(path=device)]):
                self.b.connect(device, 115200)
            self.b.send(b"TEST\n")
            self.assertEqual(os.read(master, 5), b"TEST\n")
            os.write(master, b"line-a\nline-b\n")
            await asyncio.sleep(0.15)
            await self.b.disconnect()
            rx = [
                r["message"]
                for r in self.b.report(self.b.run)["logs"]
                if r["level"] == "RX"
            ]
            self.assertEqual(rx, ["line-a", "line-b"])
        finally:
            os.close(master)
            os.close(slave)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(TestServer(make_app(Path(self.temp.name))))
        await self.client.start_server()
        self.token = (await (await self.client.get("/api/setup")).json())["token"]
        self.run = (await (await self.client.get("/api/state")).json())["run"]

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def post(self, op, **d):
        return await self.client.post(
            "/api/" + op,
            json={"run": self.run, **d},
            headers={"X-BoardBench": self.token},
        )

    async def test_reports_and_errors(self):
        self.assertEqual((await self.client.post("/api/note", json={})).status, 403)
        self.assertEqual(
            (await self.post("note", run=-1, text="wrong run")).status, 400
        )
        self.assertEqual(
            (await self.post("connect", port="missing", baud=115200)).status, 400
        )
        self.assertEqual((await self.post("note", text="=SUM(1)")).status, 200)
        await self.post("mark", name="供电与启动", state="通过", detail="saw LED")
        for fmt, needle in [("txt", "未测"), ("json", "saw LED"), ("csv", "'=SUM(1)")]:
            r = await self.client.get(f"/api/report/{self.run}/{fmt}")
            self.assertIn(needle, await r.text())
        self.assertEqual((await self.client.get("/api/report/999/txt")).status, 400)

    async def test_local_audio_roundtrip(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            p = s.getsockname()[1]
        self.assertEqual(
            (await self.post("echo", enable=True, host="127.0.0.1", port=p)).status, 200
        )
        url = (await (await self.client.get("/api/state")).json())["echo"]
        async with ClientSession(headers={"Device-Id": "bench-test"}) as session:
            ota = await session.post(url, json={"board": {"type": "fixture"}})
            ws = await session.ws_connect((await ota.json())["websocket"]["url"])
            await ws.send_json(
                {
                    "type": "hello",
                    "version": 1,
                    "audio_params": {
                        "channels": 1,
                        "sample_rate": 16000,
                        "frame_duration": 20,
                        "format": "opus",
                    },
                }
            )
            self.assertEqual((await ws.receive_json(timeout=3))["type"], "hello")
            await ws.send_json({"type": "listen", "state": "start"})
            data = bytes.fromhex("f8fffe")
            await ws.send_bytes(data)
            await ws.send_json({"type": "listen", "state": "stop"})
            self.assertEqual((await ws.receive_json(timeout=3))["state"], "start")
            self.assertEqual(
                (await ws.receive_json(timeout=3))["state"], "sentence_start"
            )
            self.assertEqual(await ws.receive_bytes(timeout=3), data)
            self.assertEqual((await ws.receive_json(timeout=3))["state"], "stop")
            await ws.close()
        await self.post("echo", enable=False)
        report = await (await self.client.get(f"/api/report/{self.run}/json")).json()
        self.assertEqual(report["results"][0]["name"], "局域网音频往返")
        self.assertTrue(any("握手完成" in x["message"] for x in report["logs"]))
