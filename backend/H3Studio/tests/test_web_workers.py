"""Full Studio workers, isolated filesystems, no model/GPU execution."""
import io
import tempfile
import unittest
import asyncio
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
import aiohttp
import av
import numpy as np
from fractions import Fraction
from shared_gateway import GatewayStore
from web_server import StudioWebServer

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
