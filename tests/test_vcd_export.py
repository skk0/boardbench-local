import json
import unittest
from datetime import datetime, timedelta, timezone

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from boardbench.bench import Bench
from boardbench.reports.vcd_export import routes


class VcdTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.b = Bench(":memory:")
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.b.db.execute(
            "UPDATE runs SET started=? WHERE id=?", (self.start.isoformat(), self.b.run)
        )
        self.b.db.execute("DELETE FROM logs WHERE run=?", (self.b.run,))
        app = web.Application()
        app.add_routes(routes(self.b))
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.path = f"/api/replay/{self.b.run}/keys.vcd"

    async def asyncTearDown(self):
        await self.client.close()
        self.b.db.close()

    def record(self, ms, message, level="RX", source="串口"):
        at = (self.start + timedelta(milliseconds=ms)).isoformat()
        return self.b.db.execute(
            "INSERT INTO logs(run,at,level,source,message,raw) VALUES(?,?,?,?,?,?)",
            (self.b.run, at, level, source, message, ""),
        ).lastrowid

    def sample(self, ms, value, **kwargs):
        return self.record(
            ms,
            json.dumps(dict(bb=1, part="buttons", status="sample", value=value)),
            **kwargs,
        )

    async def test_require_real_received_triple(self):
        self.sample(1, "K0=DOWN K1=UP K2=UP", level="TX")
        self.sample(2, "K0=DOWN")
        self.sample(3, "K0=DOWN K1=UP K2=UP", source="操作员")
        for method in (self.client.head, self.client.get):
            response = await method(self.path)
            self.assertEqual(response.status, 422)
            self.assertEqual(response.headers["X-BoardBench-Error"], "no_key_samples")
            self.assertEqual((await method("/api/replay/77777/keys.vcd")).status, 404)
        self.sample(5, "K0=UP K1=UP K2=UP")
        response = await self.client.head(self.path)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), b"")

    async def test_time_levels_gaps_and_close(self):
        for i in range(300):
            self.record(i, "waiting")
        self.sample(500, "K0=DOWN K1=UP K2=UP")
        self.sample(1000, "K0=UP K1=UP K2=UP")
        self.sample(900, "K0=UP K1=DOWN K2=UP")
        self.sample(11000, "K0=UP K1=UP K2=UP")
        self.record(14000, "连接已关闭", level="INFO")
        self.sample(15000, "K0=DOWN K1=DOWN K2=DOWN")
        self.record(16000, "已连接 COM3", level="INFO")
        self.sample(17000, "K0=DOWN K1=DOWN K2=DOWN")
        upper = self.record(18000, "tail")
        self.sample(19000, "K0=UP K1=UP K2=UP")
        response = await self.client.get(self.path + f"?upper={upper}")
        self.assertEqual(response.status, 200)
        text = await response.text()
        self.assertIn("$timescale 1 ms $end", text)
        trace = text.split("$dumpvars\nxa\nxb\nxc\n$end\n", 1)[1]
        self.assertEqual(
            trace,
            "#500\n0a\n1b\n1c\n#1000\n1a\n0b\n#9000\nxa\nxb\nxc\n"
            "#11000\n1a\n1b\n1c\n#14000\nxa\nxb\nxc\n#17000\n0a\n0b\n0c\n#18000\n",
        )

    async def test_snapshot_boundary_and_malformed_samples(self):
        first = self.sample(10, "K0=UP K1=UP K2=DOWN")
        self.sample(1000, "K0=UP K1=UP")
        self.record(1050, "noise")
        text = await (await self.client.get(self.path)).text()
        self.assertIn("#1000\nxa\nxb\nxc\n#1050\n", text)
        for bad in ("oops", "-1", "3.14", "9" * 20, str(first + 100)):
            self.assertEqual(
                (await self.client.head(self.path + "?upper=" + bad)).status, 400
            )
        response = await self.client.get(self.path + "?upper=0")
        self.assertEqual((await response.json())["code"], "no_key_samples")
