import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from shared_gateway import GatewayStore
from web_server import StudioWebServer, clean_api_path, route_allowed

ORIGIN = "https://yichonezhu.github.io"
A = "a" * 32
B = "b" * 32


class FakePool:
    def __init__(self, urls):
        self.urls = urls
    async def ensure(self, user_id, token):
        return self.urls[user_id]
    def get(self, user_id):
        return self.urls.get(user_id)
    async def close(self):
        pass


class WebServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = GatewayStore(self.root)
        self.alice, self.alice_key = self.store.create_user("Alice")
        self.bob, self.bob_key = self.store.create_user("Bob")
        self.workers = []
        urls = {}
        for user, identity in [(self.alice, A), (self.bob, B)]:
            app = web.Application()
            async def projects(request, value=identity):
                return web.json_response([{"id": value, "name": value[0]}])
            async def file(request, value=identity):
                if request.match_info["id"] != value:
                    return web.Response(status=404)
                return web.Response(body=b"0123", status=206 if request.headers.get("Range") else 200,
                                    headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-3/8",
                                             "Accept-Ranges": "bytes", "Content-Disposition": 'attachment; filename="clip.mp4"'})
            app.router.add_get("/api/editor/projects", projects)
            app.router.add_get("/api/jobs/{id}/video", file)
            worker = TestServer(app)
            await worker.start_server()
            self.workers.append(worker)
            urls[user["id"]] = str(worker.make_url("/")).rstrip("/")
        self.broker = StudioWebServer(self.root, self.root / "users", "http://127.0.0.1:8190", [ORIGIN], pool=FakePool(urls))
        self.client = TestClient(TestServer(self.broker.create_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for worker in self.workers:
            await worker.close()
        self.temp.cleanup()

    async def login(self, key):
        response = await self.client.post("/web/login", json={"key": key}, headers={"Origin": ORIGIN})
        self.assertEqual(response.status, 200)
        return await response.json()

    async def test_preflight_and_error_responses_have_cors(self):
        response = await self.client.options("/api/editor/upload", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
        response = await self.client.get("/api/jobs", headers={"Origin": ORIGIN})
        self.assertEqual(response.status, 401)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
        response = await self.client.post("/web/login", json={"key": self.alice_key}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 403)

    async def test_two_users_and_management_routes(self):
        alice, bob = await self.login(self.alice_key), await self.login(self.bob_key)
        for login, identity in [(alice, A), (bob, B)]:
            headers = {"Origin": ORIGIN, "Authorization": "Bearer " + login["token"]}
            response = await self.client.get("/api/editor/projects", headers=headers)
            payload = await response.json()
            self.assertEqual(payload["__h3_web_payload"][0]["id"], identity)
            self.assertIn(f"/api/jobs/{identity}/video", payload["__h3_web_tickets"])
            for endpoint in ["/api/connection", "/api/connection/test", "/api/gateway/users", "/api/comfy/start", "/api/voice/install"]:
                response = await self.client.post(endpoint, json={}, headers=headers)
                self.assertEqual(response.status, 403, endpoint)

    async def test_ticket_range_download_and_cross_user_access(self):
        alice = await self.login(self.alice_key)
        auth = {"Origin": ORIGIN, "Authorization": "Bearer " + alice["token"]}
        payload = await (await self.client.get("/api/editor/projects", headers=auth)).json()
        path = f"/api/jobs/{A}/video"
        ticket = payload["__h3_web_tickets"][path]
        response = await self.client.get(path, params={"ticket": ticket, "download": "1"}, headers={"Origin": ORIGIN, "Range": "bytes=0-3"})
        self.assertEqual(response.status, 206)
        self.assertEqual(await response.read(), b"0123")
        self.assertEqual(response.headers["Content-Range"], "bytes 0-3/8")
        self.assertIn("clip.mp4", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
        response = await self.client.get(f"/api/jobs/{B}/video", params={"ticket": ticket})
        self.assertEqual(response.status, 401)
        response = await self.client.get(f"/api/jobs/{B}/video", headers=auth)
        self.assertEqual(response.status, 404)
        response = await self.client.get("/api/editor/projects", params={"ticket": ticket})
        self.assertEqual(response.status, 401)

    async def test_rotation_disable_and_logout_revoke_tickets(self):
        alice = await self.login(self.alice_key)
        auth = {"Origin": ORIGIN, "Authorization": "Bearer " + alice["token"]}
        payload = await (await self.client.get("/api/editor/projects", headers=auth)).json()
        path = f"/api/jobs/{A}/video"
        ticket = payload["__h3_web_tickets"][path]
        _, new_key = self.store.rotate_user(self.alice["id"])
        self.assertEqual((await self.client.get("/web/session", headers=auth)).status, 401)
        self.assertEqual((await self.client.get(path, params={"ticket": ticket})).status, 401)
        alice = await self.login(new_key)
        auth["Authorization"] = "Bearer " + alice["token"]
        await self.client.post("/web/logout", headers=auth)
        self.assertEqual((await self.client.get("/web/session", headers=auth)).status, 401)
        alice = await self.login(new_key)
        auth["Authorization"] = "Bearer " + alice["token"]
        self.store.find_user(self.alice["id"])["enabled"] = False
        self.store.save_config()
        self.assertEqual((await self.client.get("/web/session", headers=auth)).status, 401)

    async def test_media_ticket_cannot_authorize_writes_or_tampered_routes(self):
        alice = await self.login(self.alice_key)
        auth = {"Origin": ORIGIN, "Authorization": "Bearer " + alice["token"]}
        response = await self.client.post("/web/media-tickets", headers=auth, json={"paths": ["/api/editor/projects"]})
        self.assertEqual(response.status, 400)
        response = await self.client.post("/web/media-tickets", headers=auth, json={"paths": [f"/api/jobs/{A}/video"]})
        ticket = (await response.json())["tickets"][f"/api/jobs/{A}/video"]
        response = await self.client.post(f"/api/jobs/{A}/cancel", params={"ticket": ticket})
        self.assertEqual(response.status, 401)


class RouteTests(unittest.TestCase):
    def test_encoded_paths_and_route_boundaries(self):
        for value in ["/api/jobs/%2e%2e/config", "/api/jobs/../config", "/api//jobs", "/api/jobs%2fabc", "/static/app.js"]:
            with self.assertRaises(ValueError):
                clean_api_path(value)
        self.assertFalse(route_allowed("POST", "/api/connection"))
        self.assertTrue(route_allowed("POST", "/api/keyframes/prepare"))
        self.assertTrue(route_allowed("HEAD", f"/api/jobs/{A}/video"))
