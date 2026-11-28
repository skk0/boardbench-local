import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from boardbench import updates
from boardbench.server import make_app


def release(tag="v1.0.0"):
    asset = "BoardBench-Windows-x64.zip"
    return dict(tag_name=tag, assets=[dict(name=asset, digest="sha256:" + "a" * 64, size=12,
                browser_download_url=f"https://github.com/{updates.REPO}/releases/download/{tag}/{asset}")])


class ReleaseTests(unittest.TestCase):
    def test_version_and_artifact_validation(self):
        self.assertIsNone(updates.select_release(release("v" + updates.VERSION), "Windows-x64"))
        self.assertIsNone(updates.select_release(release("v0.0.9"), "Windows-x64"))
        self.assertEqual(updates.select_release(release(), "Windows-x64")["tag"], "v1.0.0")
        for change in (dict(digest=None), dict(size=0), dict(browser_download_url="https://example.org/a")):
            with self.subTest(change=change), self.assertRaises(ValueError):
                data = release()
                data["assets"][0].update(change)
                updates.select_release(data, "Windows-x64")
        for data in (release("v2.0.0-beta"), {**release(), "draft": True}, {**release(), "assets": []}):
            with self.assertRaises(ValueError):
                updates.select_release(data, "Windows-x64")

    def test_zip_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            archive = directory / "payload.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("../BoardBench.exe", b"MZ")
            for mac in (True, False):
                with self.assertRaises(ValueError):
                    updates.prepare(archive, directory, directory / "old", mac)


class UpdateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_desktop_guards_and_task_lock(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(updates.Updater, "handoff") as launch:
            directory = Path(temp)
            with patch("boardbench.server.installation", return_value=(directory / "BoardBench.exe", "Windows-x64")):
                server = make_app(directory, monitor_usb=False, close_window=lambda: None)
            async with TestClient(TestServer(server)) as client:
                setup = await (await client.get("/api/setup")).json()
                self.assertEqual(setup["desktop_update"]["phase"], "idle")
                self.assertEqual((await client.post("/api/update/install")).status, 403)
                headers = {"X-BoardBench": setup["token"]}
                run = (await (await client.get("/api/state")).json())["run"]
                response = await client.post("/api/suite-start", headers=headers, json={"run":run,"mode":"full"})
                self.assertEqual(response.status, 200)
                self.assertEqual((await client.post("/api/update/install", headers=headers)).status, 400)
                launch.assert_not_called()
        with tempfile.TemporaryDirectory() as temp:
            async with TestClient(TestServer(make_app(Path(temp), False))) as client:
                self.assertIsNone((await (await client.get("/api/setup")).json())["desktop_update"])
                self.assertEqual((await client.get("/api/update")).status, 404)

    async def test_download_verification_and_retry(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("BoardBench.exe", b"MZtest")
        data = buf.getvalue()
        async def payload(req):
            return web.Response(body=data)
        service = web.Application()
        service.router.add_get("/payload", payload)
        async with TestServer(service) as remote:
            with tempfile.TemporaryDirectory() as tmp, patch.object(updates, "open_asset", lambda c, u: c.get(u)):
                directory = Path(tmp)
                updater = updates.Updater(directory / "BoardBench.exe", "Windows-x64")
                updater.release = dict(tag="v1.0.0", url=str(remote.make_url("/payload")),
                                       size=len(data), digest="0" * 64)
                updater.phase = "available"
                updater.start("download")
                with self.assertRaises(ValueError):
                    updater.start("download")
                await updater.task
                self.assertEqual(updater.phase, "available")
                self.assertIn("SHA-256", updater.error)
                self.assertFalse(list(directory.iterdir()))
                updater.release["digest"] = hashlib.sha256(data).hexdigest()
                # Real Authenticode checks run against packaged PE files in desktop CI.
                with patch("boardbench.updates.subprocess.run"):
                    updater.start("download")
                    await updater.task
                self.assertEqual(updater.phase, "ready", updater.error)
                self.assertEqual((updater.stage / "BoardBench.exe").read_bytes(), b"MZtest")
                await updater.stop()
                self.assertFalse(list(directory.iterdir()))
