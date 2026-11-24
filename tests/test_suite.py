import asyncio
import unittest
import serial
from bench import Bench
from profiles import BUILTINS
from suite import Suite, make_plan


class SuiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.b = Bench(":memory:")
        self.s = Suite(self.b)

    async def asyncTearDown(self):
        await self.b.stop()
        await self.b.disconnect()
        self.b.db.close()

    def test_board_scope(self):
        profile = BUILTINS["正点原子 · DNESP32S3B BOX"]
        full = make_plan(profile, "full")
        fast = make_plan(profile, "quick")
        self.assertEqual(len(full["groups"]), 5)
        self.assertEqual(sum(len(g["items"]) for g in full["groups"]), 19)
        self.assertEqual(sum(len(g["items"]) for g in fast["groups"]), 7)
        self.assertFalse(full["automatic"])

    async def test_mixed_group_and_stale_confirm(self):
        self.b.add_profile(
            {
                "name": "mixed",
                "tests": [
                    dict(
                        name="ping",
                        kind="serial",
                        step="ping",
                        tx="ping",
                        expect="ping",
                    ),
                    dict(name="led", kind="manual", step="look"),
                    dict(name="speaker", kind="manual", step="listen"),
                ],
            }
        )
        self.b.ser = serial.serial_for_url("loop://", timeout=0)
        self.b.reader = asyncio.create_task(self.b.read_loop())
        self.b.bind("fixture")
        await self.b.select_profile("mixed")
        self.assertTrue(self.b.ser.is_open)
        self.s.start("full")
        for i in range(100):
            if self.s.future:
                break
            await asyncio.sleep(0.01)
        state = self.s.state()
        self.assertEqual(state["outcomes"]["ping"]["state"], "通过")
        self.assertNotIn("led", state["outcomes"])
        data = dict(
            suite=state["id"],
            index=0,
            marks={
                "led": dict(state="失败", detail="did not light"),
                "speaker": dict(state="跳过", detail="not fitted"),
            },
        )
        with self.assertRaises(ValueError):
            self.s.confirm(dict(data, index=1))
        self.s.confirm(data)
        with self.assertRaises(ValueError):
            self.s.confirm(data)
        await self.b.job
        saved = self.s.history(self.b.run)[0]
        self.assertEqual(saved["status"], "已完成")
        self.assertEqual(saved["outcomes"]["led"]["state"], "失败")
        self.assertEqual(len(saved["outcomes"]), 3)

    async def test_disconnected_automatic_and_cancel(self):
        self.b.new("DNESP32S3B BOX · BoardBenchProbe 固件")
        self.s.start("full")
        await asyncio.sleep(0)
        self.assertEqual(len(self.s.current["outcomes"]), 7)
        self.assertTrue(
            all(x["state"] == "跳过" for x in self.s.current["outcomes"].values())
        )
        await self.b.stop()
        self.assertEqual(self.s.state()["status"], "已停止")
        self.assertEqual(len(self.b.report(self.b.run)["results"]), 7)
