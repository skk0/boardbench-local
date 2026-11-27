import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from boardbench.bench import Bench
from boardbench.reports import replay


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

    async def test_next_issue_crosses_pages_and_respects_session_and_cutoff(self):
        b = self.bench
        path = f"/api/replay/{b.run}/next-issue"
        for _ in range(255):
            b.log("RX", "串口", "info: zero errors")
        b.log("RX", "串口", "W (143) unknown_driver: dropped sample")
        b.log("RX", "串口", '{"bb":1,"part":"imu","status":"missing","value":"no ack"}')
        upper = b.current_rx_log_id
        b.log("ERROR", "任务", "later")
        first = await (await self.client.get(path, params={"upper": str(upper)})).json()
        self.assertEqual(first["event"]["issue_level"], "WARN")
        self.assertEqual(len(first["context"]), 7)
        self.assertEqual(first["context"][-1]["id"], upper)
        second = await (
            await self.client.get(
                path, params={"after": str(first["event"]["id"]), "upper": str(upper)}
            )
        ).json()
        self.assertEqual(
            (second["event"]["id"], second["event"]["issue_level"]), (upper, "ERROR")
        )
        last = await (
            await self.client.get(
                path, params={"after": str(upper), "upper": str(upper)}
            )
        ).json()
        self.assertIsNone(last["event"])
        self.assertEqual(b.tx, 0)

    async def test_search_bidirectional_literal_and_scoped_reference(self):
        b = self.bench
        path = f"/api/replay/{b.run}/search"

        async def search(term, **params):
            response = await self.client.get(path, params={"q": term, **params})
            self.assertEqual(response.status, 200)
            return await response.json()

        b.log("RX", "UART0", "Boot [A%_]")
        first_id = b.current_rx_log_id
        for i in range(260):
            b.log("RX", "串口", f"sample {i}")
        b.log("RX", "串口", "\x1b[33mBOOT [a%_]\x1b[0m")
        last_id = b.current_rx_log_id
        b.log("RX", "串口", "boot beyond cutoff")
        b.new("通用串口设备")
        b.log("RX", "串口", "other session")
        first = await search("[a%_]", upper=str(last_id))
        self.assertEqual(first["event"]["id"], first_id)
        second = await search("boot", after=str(first_id), upper=str(last_id))
        self.assertEqual(second["event"]["id"], last_id)
        self.assertEqual(len(second["context"]), 6)
        self.assertEqual(second["context"][-1]["id"], last_id)
        prev = await search("BOOT", after=str(last_id), direction="previous")
        self.assertEqual(prev["event"]["id"], first_id)
        self.assertIsNone((await search("boot", after=str(last_id), upper=str(last_id)))["event"])
        self.assertIsNone((await search(f"L{b.current_rx_log_id}"))["event"])
        self.assertIsNone((await search(f"L{last_id}", upper=str(first_id)))["event"])
        self.assertEqual((await search(f"L{last_id}"))["event"]["id"], last_id)
        self.assertEqual((await search("uart0"))["event"]["id"], first_id)
        self.assertEqual((await search("rx"))["event"]["id"], first_id)
        for invalid in (" ", "a" * 161, "#L" + "1" * 22):
            self.assertEqual((await self.client.get(path, params={"q": invalid})).status, 400)
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
