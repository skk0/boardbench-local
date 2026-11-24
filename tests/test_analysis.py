import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient
from unittest.mock import AsyncMock, patch

from app import make_app
from ai_analysis import Analysis, normalize
from bench import Bench


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.client = TestClient(TestServer(make_app(Path(self.dir.name))))
        self.requests = []
        provider = web.Application()

        async def chat(req):
            payload = await req.json()
            self.requests.append((req.headers.get("Authorization"), payload))
            if payload["model"] == "broken":
                return web.Response(status=401, text="private-detail")
            msg = payload["messages"][-1]["content"]
            answer = "OK"
            if msg != "Reply OK.":
                snap = json.loads(msg)
                answer = json.dumps(
                    {
                        "findings": [
                            {
                                "kind": "观察",
                                "text": "日志中有失败记录",
                                "refs": [snap["events"][0]["ref"]],
                            },
                            {
                                "kind": "观察",
                                "text": "未经验证的结论",
                                "refs": ["L999999"],
                            },
                        ],
                        "next": ["核对原始日志"],
                    }
                )
            return web.json_response({"choices": [{"message": {"content": answer}}]})

        async def models(req):
            return web.json_response({"data": [{"id": "local-test"}]})

        provider.router.add_post("/custom/v1/chat/completions", chat)
        provider.router.add_get("/custom/v1/models", models)
        self.provider = TestServer(provider)
        await self.provider.start_server()
        await self.client.start_server()
        self.base = str(self.provider.make_url("/custom/v1"))
        self.token = (await (await self.client.get("/api/setup")).json())["token"]
        self.run = (await (await self.client.get("/api/state")).json())["run"]

    async def asyncTearDown(self):
        await self.client.close()
        await self.provider.close()
        self.dir.cleanup()

    async def post(self, path, **data):
        return await self.client.post(
            "/api/" + path, json=data, headers={"X-BoardBench": self.token}
        )

    async def config(self, model="local-test", key="fixture-only"):
        r = await self.post(
            "ai/settings",
            base=self.base + "/chat/completions",
            model=model,
            key=key,
            remember=False,
        )
        self.assertEqual(r.status, 200)
        self.assertNotIn(key or "unused-marker", await r.text())

    async def test_immutable_preview_evidence_and_history(self):
        await self.config()
        await self.post(
            "note",
            run=self.run,
            text='ERR sample password="wifi-private" MAC 12:34:56:78:9a:bc',
        )
        p = await (
            await self.post(
                "ai/preview", run=self.run, focus="fault", question="故障原因？"
            )
        ).json()
        self.assertFalse(self.requests)
        self.assertNotIn("wifi-private", str(p))
        self.assertNotIn("12:34:56:78:9a:bc", str(p))
        await self.post("note", run=self.run, text="after-preview")
        await self.post("new", run=self.run, profile="ESP32 系列")
        a = await (await self.post("ai/analyze", ticket=p["ticket"])).json()
        self.assertEqual(self.requests[0][0], "Bearer fixture-only")
        self.assertEqual(
            json.loads(self.requests[0][1]["messages"][-1]["content"]), p["snapshot"]
        )
        self.assertNotIn("after-preview", str(a))
        self.assertEqual(a["run"], self.run)
        self.assertTrue(a["answer"]["findings"][1]["missing"])
        self.assertEqual(a["answer"]["findings"][1]["kind"], "待确认")
        ref = a["answer"]["findings"][0]["refs"][0]
        self.assertEqual(
            (await self.client.get(f"/api/ai/evidence/{self.run}/{ref[1:]}")).status,
            200,
        )
        hist = await (await self.client.get(f"/api/report/{self.run}/json")).json()
        self.assertEqual(len(hist["ai_analyses"]), 1)
        self.assertEqual(hist["results"], [])
        self.assertEqual(
            (await self.post("ai/analyze", ticket=p["ticket"])).status, 400
        )

    async def test_connection_and_key_binding(self):
        await self.config()
        self.assertEqual(await (await self.post("ai/models")).json(), ["local-test"])
        self.assertEqual((await self.post("ai/test")).status, 200)
        self.assertEqual(self.requests[-1][0], "Bearer fixture-only")
        await self.config("broken")
        err = await self.post("ai/test")
        self.assertEqual(err.status, 400)
        self.assertNotIn("private-detail", await err.text())
        r = await self.post(
            "ai/settings",
            base=self.base + "/different",
            model="new",
            key="",
            remember=False,
        )
        self.assertFalse((await r.json())["has_key"])
        self.assertFalse(
            any(
                b"fixture-only" in f.read_bytes()
                for f in Path(self.dir.name).glob("*.sqlite3*")
            )
        )

    async def test_vault_and_clear(self):
        b = Bench(":memory:")
        ai = Analysis(b)
        vault = {}

        async def fake(op, base, *args):
            if op == "set_password":
                vault[base] = args[0]
            if op == "delete_password":
                vault.pop(base, None)
            if op == "get_password":
                return vault.get(base)

        try:
            with patch.object(ai, "vault", side_effect=fake):
                await ai.save(
                    dict(base=self.base, model="dev", key="test", remember=True)
                )
                self.assertEqual(vault[self.base], "test")
                ai.session_key = ""
                self.assertEqual((await ai.credentials())[-1], "test")
                await ai.save(dict(base=self.base, model="dev", clear=True))
                self.assertEqual(vault, {})
        finally:
            b.db.close()

    def test_url_and_unstructured_reply(self):
        self.assertEqual(normalize(self.base + "/chat/completions/"), self.base)
        with self.assertRaises(ValueError):
            normalize("https://example.com/v1?api_key=secret")
        self.assertTrue(
            Analysis.interpret("plain response", {"events": [], "results": []})[
                "warning"
            ]
        )
