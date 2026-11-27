import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestServer

from boardbench.devices.monitor import BOX_PARTS, decode
from boardbench.devices.screen_mirror import destination, install, jpeg_dimensions, read_frame


class ScreenTests(unittest.TestCase):
    def test_explicit_lan_destination(self):
        self.assertEqual(destination("10.8.0.2:8888"), "http://10.8.0.2:8888/screen.jpg")
        for text in ("127.0.0.1", "169.254.169.254", "example.com", "10.0.0.1/test", "10.0.0.1:65536"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                destination(text)

    def test_bad_jpeg(self):
        for body in (b"", b"not a frame", b"\xff\xd8\xff\xd9"):
            with self.assertRaises(ValueError):
                jpeg_dimensions(body)

    def test_component_sources(self):
        self.assertEqual(decode("E (20557) MQTT: Failed to receive server hello")[:2], ("session", "固件报错"))
        self.assertEqual(decode("I (40) DeskWeather: Weather updated")[0], "weather")
        self.assertIsNone(decode("I (40) Ml307Board: ML307 IMEI: 123"))
        self.assertNotIn("modem", BOX_PARTS)


class ScreenAPITests(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        routes = web.RouteTableDef()
        install(routes)
        app.add_routes(routes)
        return app

    async def test_frame_delivery_and_rejection(self):
        with patch("boardbench.devices.screen_mirror.read_frame", new_callable=AsyncMock, return_value=b"fixture") as pull:
            r = await self.client.post("/api/screen/frame", json={"address":"192.168.1.7","code":"012345"})
            self.assertEqual(await r.read(), b"fixture")
            pull.assert_awaited_once_with("http://192.168.1.7:8088/screen.jpg", "012345")
            r = await self.client.post("/api/screen/frame", json={"address":"127.0.0.1"})
            self.assertEqual(r.status, 400)
            self.assertEqual(pull.await_count, 1)

    async def test_receiver_will_not_follow_redirects(self):
        other = web.Application()
        async def redirect(request):
            raise web.HTTPFound("/secret")
        other.router.add_get("/screen.jpg", redirect)
        async with TestServer(other) as server:
            with self.assertRaisesRegex(ValueError, "302"):
                await read_frame(str(server.make_url("/screen.jpg")), "")
