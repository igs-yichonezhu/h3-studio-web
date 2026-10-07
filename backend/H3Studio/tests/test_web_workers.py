"""Full Studio workers, isolated filesystems, no model/GPU execution."""
import io
import tempfile
import unittest
import asyncio
import json
from unittest.mock import patch
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
import aiohttp
import av
import numpy as np
from fractions import Fraction
from shared_gateway import GatewayStore
from web_server import StudioWebServer, origin_value
from web_worker import gateway_session_factory

ORIGIN = "https://yichonezhu.github.io"


class WorkerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        engine = web.Application()
        async def handle(request):
            if request.path == "/queue":
                return web.json_response({"queue_running": [], "queue_pending": []})
            if request.path == "/system_stats":
                return web.json_response({"system": {}, "devices": []})
            return web.json_response({})
        engine.router.add_route("*", "/{tail:.*}", handle)
        self.engine = TestServer(engine)
        await self.engine.start_server()
        self.store = GatewayStore(self.root / "host")
        self.a, self.a_key = self.store.create_user("Alice")
        self.b, self.b_key = self.store.create_user("Bob")
        self.broker = StudioWebServer(self.root / "host", self.root / "workers",
                                     str(self.engine.make_url("/")).rstrip("/"), [ORIGIN])
        self.client = TestClient(TestServer(self.broker.create_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.engine.close()
        self.temp.cleanup()

    async def login(self, key):
        response = await self.client.post("/web/login", json={"key": key}, headers={"Origin": ORIGIN})
        self.assertIn(response.status, (200, 201), (await response.text())[:500])
        token = (await response.json())["token"]
        return {"Origin": ORIGIN, "Authorization": "Bearer " + token}

    async def json(self, method, path, headers, **kwargs):
        response = await self.client.request(method, path, headers=headers, **kwargs)
        detail = (await response.text())[:500]
        if response.status >= 500:
            detail += "\n" + "\n".join(log.read_text(errors="replace")[-2500:] for log in (self.root / "workers").glob("*/worker.log"))
        self.assertTrue(200 <= response.status < 300, detail)
        return (await response.json())["__h3_web_payload"]

    async def test_projects_upload_archive_export_and_user_isolation(self):
        alice = await self.login(self.a_key)
        bob = await self.login(self.b_key)
        # Workers reject even same-machine callers without the broker secret.
        worker_url = self.broker.pool.get(self.a["id"])
        async with aiohttp.ClientSession() as client:
            self.assertEqual((await client.get(worker_url + "/api/editor/projects")).status, 401)
        worker_pid = self.broker.pool.workers[self.a["id"]].process.pid
        await self.login(self.a_key)
        self.assertEqual(self.broker.pool.workers[self.a["id"]].process.pid, worker_pid)
        project = await self.json("POST", "/api/editor/projects", alice, json={"name": "Alice project"})
        project = project.get("project", project)
        alice_projects = await self.json("GET", "/api/editor/projects", alice)
        bob_projects = await self.json("GET", "/api/editor/projects", bob)
        self.assertIn(project["id"], [value["id"] for value in alice_projects.get("projects", alice_projects) if isinstance(value, dict)] if isinstance(alice_projects, dict) else [value["id"] for value in alice_projects])
        self.assertEqual(bob_projects.get("projects", bob_projects) if isinstance(bob_projects, dict) else bob_projects, [])
        response = await self.client.get("/api/editor/projects/" + project["id"], headers=bob)
        self.assertEqual(response.status, 404)
        image = io.BytesIO()
        Image.new("RGB", (64, 48), (40, 120, 70)).save(image, format="PNG")
        upload = aiohttp.FormData()
        upload.add_field("file", image.getvalue(), filename="test.png", content_type="image/png")
        media = await self.json("POST", "/api/editor/upload", alice, data=upload)
        media = media.get("media", media)
        self.assertTrue((self.root / "workers" / self.a["id"] / "data" / "editor").is_dir())
        self.assertTrue((self.root / "workers" / self.b["id"] / "data" / "editor").is_dir())
        self.assertEqual((await self.client.get(f"/api/editor/media/{media['id']}/file", headers=bob)).status, 404)
        response = await self.client.get(f"/api/editor/media/{media['id']}/file", headers=alice)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), image.getvalue())
        response = await self.client.get(f"/api/editor/projects/{project['id']}/archive", headers=alice)
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.read()).startswith(b"PK"))
        # A real tiny MP4 exercises the complete CPU editor export and Range path.
        fixture = self.root / "fixture.mp4"
        with av.open(str(fixture), mode="w") as container:
            stream = container.add_stream("libx264", rate=24)
            stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
            stream.sample_aspect_ratio = Fraction(1, 1)
            for frame_index in range(24):
                frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), frame_index * 6, dtype=np.uint8), format="rgb24")
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        upload = aiohttp.FormData()
        upload.add_field("file", fixture.read_bytes(), filename="fixture.mp4", content_type="video/mp4")
        clip = await self.json("POST", "/api/editor/upload", alice, data=upload)
        clip = clip.get("media", clip)
        project.update(width=360, height=360, clips=[{"id": "c" * 32, "media_id": clip["id"], "in": 0, "out": 1, "volume": 1}])
        await self.json("PUT", "/api/editor/projects/" + project["id"], alice, json=project)
        job = await self.json("POST", "/api/editor/projects/" + project["id"] + "/exports", alice)
        for attempt in range(100):
            job = await self.json("GET", "/api/editor/exports/" + job["id"], alice)
            if job["status"] in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.1)
        self.assertEqual(job["status"], "completed", job.get("error"))
        response = await self.client.get(f"/api/editor/exports/{job['id']}/file", headers={**alice, "Range": "bytes=0-15"})
        self.assertEqual(response.status, 206)
        self.assertEqual(len(await response.read()), 16)
        self.assertEqual((await self.client.get(f"/api/editor/exports/{job['id']}/file", headers=bob)).status, 404)
        _, refreshed_key = self.store.rotate_user(self.a["id"])
        self.assertEqual((await self.client.get("/web/session", headers=alice)).status, 401)
        alice = await self.login(refreshed_key)
        self.assertEqual(self.broker.pool.workers[self.a["id"]].process.pid, worker_pid)
        self.assertEqual((await self.json("GET", "/api/editor/projects/" + project["id"], alice))["name"], "Alice project")
        # Connection remains read-only and cannot be changed to another user's token.
        self.assertEqual((await self.client.post("/api/connection", headers=alice, json={"mode": "local"})).status, 403)

    async def test_custom_gateways_use_their_own_real_worker_configuration(self):
        self.broker.remote_auth = self.broker.allow_custom_gateways = True
        keys = ['h3g_DEMO_GATEWAY_A_ONLY', 'h3g_DEMO_GATEWAY_B_ONLY']
        targets = []
        for key in keys:
            async def handle(request, value=key):
                if request.headers.get('Authorization') != 'Bearer ' + value:
                    return web.Response(status=401)
                if request.path == '/queue':
                    return web.json_response({'running': [], 'pending': []})
                if request.path == '/system_stats':
                    return web.json_response({'system': {}, 'devices': [{'name': value}]})
                return web.json_response({})
            app = web.Application()
            app.router.add_route('*', '/{tail:.*}', handle)
            gateway = TestServer(app)
            await gateway.start_server()
            self.addAsyncCleanup(gateway.close)
            targets.append(str(gateway.make_url('/')).rstrip('/'))
        logins = []
        with patch('web_server.custom_gateway_url', side_effect=origin_value):
            for key, target in zip(keys, targets):
                response = await self.client.post('/web/login', headers={'Origin': ORIGIN}, json={'key': key, 'gateway_url': target})
                self.assertEqual(response.status, 200, await response.text())
                logins.append(await response.json())
            response = await self.client.post('/web/login', headers={'Origin': ORIGIN}, json={'key': keys[0], 'gateway_url': targets[1]})
            self.assertEqual(response.status, 401)
        self.assertNotEqual(logins[0]['user']['id'], logins[1]['user']['id'])
        for info, key, target in zip(logins, keys, targets):
            configuration = json.loads((self.root / 'workers' / info['user']['id'] / 'config.json').read_text())
            self.assertEqual(configuration['base_url'], target)
            self.assertEqual(configuration['remote_access_token'], key)
            headers = {'Origin': ORIGIN, 'Authorization': 'Bearer ' + info['token']}
            connection = await self.json('GET', '/api/connection', headers)
            self.assertEqual(connection['base_url'], target)
        a = {'Origin': ORIGIN, 'Authorization': 'Bearer ' + logins[0]['token']}
        b = {'Origin': ORIGIN, 'Authorization': 'Bearer ' + logins[1]['token']}
        project = await self.json('POST', '/api/editor/projects', a, json={'name': 'Gateway A only'})
        self.assertEqual((await self.client.get('/api/editor/projects/' + project['id'], headers=b)).status, 404)
        with self.assertRaises(RuntimeError):
            await self.broker.pool.ensure(logins[0]['user']['id'], keys[0], gateway_url=targets[1])

    async def test_worker_capacity_rejects_new_identity_before_spawning(self):
        self.broker.pool.max_workers = 1
        alice = await self.login(self.a_key)
        response = await self.client.post('/web/login', headers={'Origin': ORIGIN}, json={'key': self.b_key})
        self.assertEqual(response.status, 503)
        self.assertEqual(len(self.broker.pool.workers), 1)
        self.assertFalse((self.root / 'workers' / self.b['id'] / 'config.json').exists())
        self.assertEqual((await self.client.get('/api/editor/projects', headers=alice)).status, 200)


class GatewayTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_and_websocket_gateway_redirects_never_reach_destination(self):
        reached = []
        async def destination(request):
            reached.append(request.path)
            return web.Response(text='should not be reached')
        target = web.Application()
        target.router.add_route('*', '/{tail:.*}', destination)
        target_server = TestServer(target)
        await target_server.start_server()
        self.addAsyncCleanup(target_server.close)
        async def redirect(request):
            return web.Response(status=302, headers={'Location': str(target_server.make_url('/private'))})
        gateway = web.Application()
        gateway.router.add_route('*', '/{tail:.*}', redirect)
        gateway_server = TestServer(gateway)
        await gateway_server.start_server()
        self.addAsyncCleanup(gateway_server.close)
        factory = gateway_session_factory(str(gateway_server.make_url('/')))
        async with factory() as client:
            for path in ['/view', '/object_info']:
                with self.assertRaises(aiohttp.ClientConnectionError):
                    await client.get(gateway_server.make_url(path))
            with self.assertRaises(aiohttp.ClientConnectionError):
                await client.ws_connect(str(gateway_server.make_url('/ws')).replace('http:', 'ws:'))
        self.assertEqual(reached, [])
