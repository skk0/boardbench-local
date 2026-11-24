import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from bench import Bench
import replay


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bench = Bench(":memory:")
        app = web.Application()
        app.add_routes(replay.routes(self.bench))
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.addCleanup(self.bench.db.close)

    async def test_pagination_is_frozen_and_scoped(self):
        b = self.bench
        run = b.run
        b.log("INFO", "串口", "已连接 /dev/tty · 115200 8N1")
        b.log(
            "RX", "串口", '{"bb":1,"part":"imu","status":"sample","value":"a[g] 0 0 1"}'
        )
        b.log("RX", "串口", "连接已关闭")
        b.log("INFO", "串口", "连接已关闭")
        path = f"/api/replay/{run}/events"
        first = await (await self.client.get(path, params={"limit": "2"})).json()
        self.assertTrue(first["more"])
        self.assertEqual(first["events"][-1]["boundary"], "connect")
        b.log("NOTE", "人工", "later")
        b.new("通用串口设备")
        tail = await (
            await self.client.get(
                path,
                params={
                    "upper": str(first["upper"]),
                    "after": str(first["events"][-1]["id"]),
                    "limit": "5",
                },
            )
        ).json()
        rows = tail["events"]
        self.assertFalse(tail["more"])
        self.assertEqual(len(rows), 3)
        self.assertEqual((rows[0]["part"], rows[0]["status"]), ("imu", "有读数"))
        self.assertIsNone(rows[1]["boundary"])
        self.assertEqual(rows[2]["boundary"], "disconnect")
        self.assertEqual(rows[-1]["id"], first["upper"])
        self.assertEqual(b.tx, 0)

    async def test_invalid_queries_are_local_errors(self):
        path = f"/api/replay/{self.bench.run}/events"
        for params in [
            {"limit": "0"},
            {"limit": "501"},
            {"after": "-3"},
            {"upper": "9" * 30},
        ]:
            with self.subTest(params=params):
                self.assertEqual(
                    (await self.client.get(path, params=params)).status, 400
                )
        self.assertEqual((await self.client.get("/api/replay/999/events")).status, 404)
