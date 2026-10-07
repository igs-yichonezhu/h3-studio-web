"""Authenticated LAN API for the static GitHub Pages Studio frontend.

Each Gateway identity gets an independent Studio process and filesystem root.
The public listener never forwards configuration or engine-management APIs.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import ssl
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import aiohttp
from aiohttp import web
from shared_gateway import GatewayStore

APP_DIR = Path(__file__).resolve().parent
ID = r"[a-f0-9]{32}"
USER_ID = re.compile(r"^[a-f0-9]{16}$")
READ_ROUTES = re.compile(
    rf"^/api/(?:status|queue|connection|loras|model-updates|engine-installer/status|"
    rf"assets/{ID}|face-repair/status|jobs(?:/options|/{ID}(?:/(?:recipe|preview|video|frames|thumbnail))?)?|"
    rf"shortfilms(?:/{ID}(?:/jobs)?)?|(?:voice|music)/(?:status|jobs(?:/{ID}/audio)?)|"
    rf"images/(?:status|upscale/status|jobs(?:/{ID}/image)?)|"
    rf"editor/(?:capabilities|fonts|media(?:/{ID}/(?:file|thumbnail|preview(?:/file)?))?|"
    rf"projects(?:/{ID}(?:/archive)?)?|exports/{ID}(?:/file)?))$"
)
WRITE_ROUTES = {
    "POST": re.compile(
        rf"^/api/(?:assets|(?:replacement|continuation|symbol|keyframes)/prepare|compile|render|"
        rf"model-updates/preference|jobs/{ID}/(?:cancel|resume|rename|favorite|face-repair)|"
        rf"shortfilms(?:/{ID}/(?:segments|shots/{ID}/compile))?|"
        rf"(?:voice|music)/jobs(?:/{ID}/(?:cancel|resume|rename|favorite))?|"
        rf"images/jobs(?:/{ID}/(?:cancel|reference|upscale))?|"
        rf"editor/(?:upload|text-preview|media|media/{ID}/preview(?:/cancel)?|"
        rf"projects(?:/import|/{ID}/exports)?|exports/{ID}/cancel))$"
    ),
    "PUT": re.compile(rf"^/api/(?:shortfilms|editor/projects)/{ID}$"),
    "DELETE": re.compile(rf"^/api/(?:jobs|images/jobs|shortfilms)/{ID}$"),
}
MEDIA_ROUTE = re.compile(
    rf"^/api/(?:assets/{ID}|jobs/{ID}/(?:video|preview|frames|thumbnail)|"
    rf"(?:voice|music)/jobs/{ID}/audio|images/jobs/{ID}/image|"
    rf"editor/(?:media/{ID}/(?:file|thumbnail|preview/file)|projects/{ID}/archive|exports/{ID}/file))$"
)
MEDIA_PATTERNS = (
    "/api/assets/{id}", "/api/jobs/{id}/video", "/api/jobs/{id}/preview",
    "/api/jobs/{id}/frames", "/api/jobs/{id}/thumbnail", "/api/voice/jobs/{id}/audio",
    "/api/music/jobs/{id}/audio", "/api/images/jobs/{id}/image",
    "/api/editor/media/{id}/file", "/api/editor/media/{id}/thumbnail",
    "/api/editor/media/{id}/preview/file", "/api/editor/projects/{id}/archive",
    "/api/editor/exports/{id}/file",
)
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "host"}
SESSION_SECONDS = 8 * 3600
TICKET_SECONDS = 30 * 60


def error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def clean_api_path(raw_path: str) -> str:
    path = raw_path.split("?", 1)[0]
    # Encoded routing characters can be interpreted differently by proxy/router.
    if unquote(path) != path or "\\" in path or ".." in path or "//" in path:
        raise ValueError("Invalid API path")
    if not path.startswith("/api/"):
        raise ValueError("Invalid API path")
    return path


def route_allowed(method: str, path: str) -> bool:
    return bool(READ_ROUTES.fullmatch(path)) if method in {"GET", "HEAD"} else bool(
        WRITE_ROUTES.get(method) and WRITE_ROUTES[method].fullmatch(path))


def origin_value(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Origin must be an http(s) origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Origin must not contain a path")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


@dataclass
class Worker:
    url: str
    process: asyncio.subprocess.Process
    log: object
    ready_file: Path
    secret: str
    fingerprint: str


class WorkerPool:
    def __init__(self, root: Path, gateway_url: str, *, startup_seconds=45):
        self.root = root.resolve()
        parsed = urlsplit(gateway_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query:
            raise ValueError("Invalid configured Gateway URL")
        self.gateway_url = gateway_url.rstrip("/")
        self.startup_seconds = startup_seconds
        self.workers: dict[str, Worker] = {}
        self.locks = defaultdict(asyncio.Lock)

    async def ensure(self, user_id: str, token: str) -> str:
        if not USER_ID.fullmatch(user_id):
            raise ValueError("Invalid Gateway identity")
        async with self.locks[user_id]:
            worker = self.workers.get(user_id)
            if worker and worker.process.returncode is None:
                fingerprint = hashlib.sha256(token.encode()).hexdigest()
                if hmac.compare_digest(worker.fingerprint, fingerprint):
                    return worker.url
                # Only the broker can update this loopback-only worker setting.
                async with aiohttp.ClientSession() as client:
                    async with client.post(worker.url + "/internal/web/connection", json={
                        "remote_access_token": token,
                    }, headers={"X-H3-Worker-Token": worker.secret}, allow_redirects=False) as response:
                        if response.status != 200:
                            raise RuntimeError("Worker connection refresh failed")
                worker.fingerprint = fingerprint
                return worker.url
            if worker:
                worker.log.close()
                worker.ready_file.unlink(missing_ok=True)
            directory = self.root / user_id
            directory.mkdir(parents=True, exist_ok=True)
            configuration = directory / "config.json"
            configuration.write_text(json.dumps({
                "studio_role": "client", "mode": "remote", "base_url": self.gateway_url,
                "comfy_dir": str(APP_DIR.parent / "ComfyUI"), "auto_start_local": False,
                "remote_access_token": token,
            }), encoding="utf-8")
            ready = directory / ("worker-" + secrets.token_hex(8) + ".ready.json")
            secret = secrets.token_urlsafe(32)
            environment = dict(os.environ, H3_STUDIO_DATA_DIR=str(directory / "data"),
                               H3_STUDIO_CONFIG_PATH=str(configuration), H3_WORKER_TOKEN=secret, PYTHONUNBUFFERED="1")
            log = (directory / "worker.log").open("ab")
            options = {"creationflags": 0x08000000} if os.name == "nt" else {}
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(APP_DIR / "web_worker.py"), "--ready-file", str(ready),
                cwd=str(APP_DIR), env=environment, stdout=log, stderr=log, **options,
            )
            try:
                deadline = time.monotonic() + self.startup_seconds
                while not ready.exists():
                    if process.returncode is not None:
                        raise RuntimeError("Studio worker exited; check its local worker.log")
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Studio worker startup timed out")
                    await asyncio.sleep(0.1)
                payload = json.loads(ready.read_text(encoding="utf-8"))
                port = payload["port"]
                if type(port) is not int or not 1 <= port <= 65535 or payload["pid"] != process.pid:
                    raise RuntimeError("Invalid worker readiness")
                url = f"http://127.0.0.1:{port}"
                self.workers[user_id] = Worker(url, process, log, ready, secret, hashlib.sha256(token.encode()).hexdigest())
                return url
            except BaseException:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
                log.close()
                ready.unlink(missing_ok=True)
                raise

    def get(self, user_id: str) -> str | None:
        worker = self.workers.get(user_id)
        return worker.url if worker and worker.process.returncode is None else None

    def headers(self, user_id: str):
        worker = self.workers.get(user_id)
        return {"X-H3-Worker-Token": worker.secret} if worker else {}

    async def close(self):
        for worker in self.workers.values():
            if worker.process.returncode is None:
                if os.name == "nt":
                    # Graceful CTRL_BREAK requires a console; these processes are hidden.
                    worker.process.terminate()
                else:
                    worker.process.send_signal(2)
                try:
                    await asyncio.wait_for(worker.process.wait(), 10)
                except asyncio.TimeoutError:
                    worker.process.kill()
                    await worker.process.wait()
            worker.log.close()
            worker.ready_file.unlink(missing_ok=True)
        self.workers.clear()


class StudioWebServer:
    def __init__(self, gateway_data: Path, worker_root: Path, gateway_url: str, origins,
                 *, pool=None, session_seconds=SESSION_SECONDS, remote_auth=False):
        self.gateway_data = gateway_data.resolve()
        self.gateway_url = origin_value(gateway_url)
        self.remote_auth = remote_auth
        self.pool = pool or WorkerPool(worker_root, gateway_url)
        self.origins = {origin_value(value) for value in origins}
        self.sessions = {}
        self.session_ids = {}
        self.signing_key = secrets.token_bytes(32)
        self.session_seconds = session_seconds
        self.attempts = defaultdict(deque)
        self.client = None
        self.auth_client = None

    def users(self):
        # Never trust the cached config of a previous GatewayStore instance.
        if not (self.gateway_data / "shared_gateway.json").is_file():
            return None
        return GatewayStore(self.gateway_data)

    async def remote_key_valid(self, token, *, check_protection=False):
        if not isinstance(token, str) or not re.fullmatch(r'h3g_[A-Za-z0-9_-]{10,240}', token):
            return False
        target = self.gateway_url + '/queue'
        timeout = aiohttp.ClientTimeout(total=8)
        if check_protection:
            async with self.auth_client.get(target, timeout=timeout, allow_redirects=False) as response:
                if response.status not in {401, 403}:
                    raise web.HTTPServiceUnavailable(reason='Configured remote Gateway is not authenticated')
        async with self.auth_client.get(target, headers={'Authorization': 'Bearer ' + token},
                                   timeout=timeout, allow_redirects=False) as response:
            if response.status in {401, 403}:
                return False
            if response.status != 200:
                raise web.HTTPServiceUnavailable(reason='Remote Gateway unavailable')
            try:
                if response.content_type != 'application/json':
                    raise ValueError('Not JSON')
                body, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 2 * 1024**2:
                        raise ValueError('Queue metadata too large')
                    body.append(chunk)
                value = json.loads(b''.join(body))
            except (ValueError, aiohttp.ContentTypeError):
                raise web.HTTPServiceUnavailable(reason='Invalid remote Gateway response') from None
            if not isinstance(value, dict) or not isinstance(value.get('running'), list) or not isinstance(value.get('pending'), list):
                raise web.HTTPServiceUnavailable(reason='Invalid remote Gateway queue')
            return True

    async def active(self, session):
        if session['expires'] <= time.time():
            return False
        if self.remote_auth:
            try:
                return await self.remote_key_valid(session.get('gateway_token'))
            except (aiohttp.ClientError, asyncio.TimeoutError):
                raise web.HTTPServiceUnavailable(reason='Remote Gateway temporarily unavailable') from None
        store = self.users()
        if not store or session["expires"] <= time.time():
            return False
        return any(user.get("id") == session["user_id"] and user.get("enabled", True)
                   and hmac.compare_digest(str(user.get("token_hash", "")), session["fingerprint"])
                   for user in store.config.get("users", []))

    async def bearer_session(self, request):
        header = request.headers.get("Authorization", "")
        if not header.lower().startswith("bearer "):
            return None
        session = self.sessions.get(hashlib.sha256(header[7:].strip().encode()).hexdigest())
        return session if session and await self.active(session) else None

    def ticket(self, session, path):
        if not MEDIA_ROUTE.fullmatch(path):
            raise ValueError("Not a media route")
        payload = json.dumps({"s": session["id"], "p": path,
                              "e": min(int(time.time()) + TICKET_SECONDS, session["expires"])},
                             separators=(",", ":")).encode()
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
        signature = hmac.new(self.signing_key, encoded.encode(), hashlib.sha256).hexdigest()
        return encoded + "." + signature

    async def media_session(self, request, path):
        if request.method not in {"GET", "HEAD"} or not MEDIA_ROUTE.fullmatch(path):
            return None
        if set(request.query) - {"ticket", "download", "original", "v"}:
            return None
        if any(len(value) > 256 for key, value in request.query.items() if key != "ticket"):
            return None
        if len(request.query.getall("ticket", [])) != 1:
            return None
        try:
            value = request.query["ticket"]
            if len(value) > 1024:
                return None
            encoded, signature = value.split(".", 1)
            expected = hmac.new(self.signing_key, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return None
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            session = self.session_ids.get(payload["s"])
            if payload["p"] != path or payload["e"] <= time.time():
                return None
            return session if session and await self.active(session) else None
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def media_paths(self, value):
        identities = set()
        def visit(item, depth=0):
            if depth > 24:
                return
            if isinstance(item, str) and re.fullmatch(ID, item):
                identities.add(item)
            elif isinstance(item, dict):
                for key, child in item.items():
                    # No need to scan user-authored prompt text.
                    if key not in {"prompt", "original_prompt", "description", "text"}:
                        visit(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    visit(child, depth + 1)
        visit(value)
        return {pattern.format(id=identity) for identity in sorted(identities)[:512]
                for pattern in MEDIA_PATTERNS}

    def cors_headers(self, origin):
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Expose-Headers": "Content-Disposition, Content-Range, Accept-Ranges, Content-Length"}

    def create_app(self):
        @web.middleware
        async def boundary(request, handler):
            try:
                address = ipaddress.ip_address(request.remote or "")
                if not (address.is_private or address.is_loopback):
                    return error(403, "僅限公司內網使用。")
            except ValueError:
                return error(403, "無法確認內網來源。")
            origin = request.headers.get("Origin")
            if origin and origin not in self.origins:
                return error(403, "此網站未列入公司主機允許的來源。")
            if request.method == "OPTIONS":
                requested_headers = {value.strip().lower() for value in
                                     request.headers.get("Access-Control-Request-Headers", "").split(",") if value.strip()}
                method = request.headers.get("Access-Control-Request-Method", "GET").upper()
                if not origin or method not in {"GET", "HEAD", "POST", "PUT", "DELETE"} or requested_headers - {"authorization", "content-type", "range"}:
                    return error(403, "不允許此跨網域請求。")
                response = web.Response(status=204, headers={
                    "Access-Control-Allow-Methods": "GET, HEAD, POST, PUT, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type, Range",
                    "Access-Control-Max-Age": "600", "Access-Control-Allow-Private-Network": "true",
                })
            else:
                try:
                    response = await handler(request)
                except web.HTTPException as exception:
                    response = error(exception.status, "請求無法完成。")
                except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError):
                    response = error(502, "公司 Studio 工作程序連線中斷，請重新登入。")
            if origin and not response.prepared:
                response.headers.update(self.cors_headers(origin))
            if not response.prepared:
                response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                         "X-Content-Type-Options": "nosniff"})
            return response

        application = web.Application(middlewares=[boundary], client_max_size=8 * 1024**3)
        application.router.add_get("/web/health", self.health)
        application.router.add_post("/web/login", self.login)
        application.router.add_get("/web/session", self.session)
        application.router.add_post("/web/logout", self.logout)
        application.router.add_post("/web/media-tickets", self.refresh_tickets)
        application.router.add_route("*", "/{tail:.*}", self.proxy)

        async def start(_):
            self.client = aiohttp.ClientSession(auto_decompress=False, timeout=aiohttp.ClientTimeout(total=None, sock_connect=15))
            self.auth_client = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(),
                                                    timeout=aiohttp.ClientTimeout(total=8, sock_connect=3, sock_read=8))
        async def close(_):
            await self.pool.close()
            await self.client.close()
            await self.auth_client.close()
        application.on_startup.append(start)
        application.on_cleanup.append(close)
        return application

    async def health(self, _):
        return web.json_response({"service": "h3-studio-web", "version": 1})

    async def login(self, request):
        if request.headers.get("Origin") not in self.origins:
            return error(403, "請從已允許的 Studio 網頁登入。")
        attempts = self.attempts[request.remote]
        now = time.time()
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 15:
            return error(429, "登入嘗試過於頻繁，請稍後再試。")
        attempts.append(now)
        if request.content_length and request.content_length > 8192:
            return error(413, "登入資料過大。")
        try:
            payload = await request.json()
            token = str(payload.get("key", "")).strip()
        except (ValueError, AttributeError):
            return error(400, "請輸入個人金鑰。")
        if self.remote_auth:
            try:
                valid = await self.remote_key_valid(token, check_protection=True)
            except (aiohttp.ClientError, asyncio.TimeoutError, web.HTTPException):
                return error(503, '無法驗證遠端 GPU 共享引擎，請確認主機運作與 Gateway 網址。')
            fingerprint = hashlib.sha256(token.encode()).hexdigest()
            identity = hashlib.sha256(json.dumps([self.gateway_url, token], separators=(',', ':')).encode()).hexdigest()[:16]
            user = {'id': identity, 'name': 'GPU 使用者', 'token_hash': fingerprint} if valid else None
        else:
            store = self.users()
            user = store.authenticate(token) if store and len(token) < 256 else None
        if not user:
            return error(401, "個人金鑰無效、已換發或已停用。")
        try:
            await self.pool.ensure(user["id"], token)
        except (RuntimeError, OSError):
            return error(503, "Studio 工作程序啟動失敗，請管理者查看本機 worker.log。")
        for digest, session in list(self.sessions.items()):
            if session["expires"] <= now:
                self.sessions.pop(digest, None)
                self.session_ids.pop(session["id"], None)
        if len(self.sessions) >= 1024:
            return error(503, "登入連線數已滿，請管理者檢查服務。")
        bearer = secrets.token_urlsafe(32)
        session = {"id": secrets.token_hex(16), "user_id": user["id"], "name": user["name"],
                   "fingerprint": str(user["token_hash"]), "expires": int(now) + self.session_seconds}
        if self.remote_auth:
            session['gateway_token'] = token
        self.sessions[hashlib.sha256(bearer.encode()).hexdigest()] = session
        self.session_ids[session["id"]] = session
        return web.json_response({"token": bearer, "user": {"id": user["id"], "name": user["name"]},
                                  "expires": session["expires"]})

    async def session(self, request):
        session = await self.bearer_session(request)
        if not session:
            return error(401, "登入已失效，請重新輸入個人金鑰。")
        if not self.pool.get(session["user_id"]):
            return error(503, "工作程序已停止，請重新登入。")
        return web.json_response({"user": {"id": session["user_id"], "name": session["name"]}, "expires": session["expires"]})

    async def logout(self, request):
        header = request.headers.get('Authorization', '')
        digest = hashlib.sha256(header[7:].strip().encode()).hexdigest() if header.lower().startswith('bearer ') else None
        # Logout remains available when the remote GPU is offline or revoked.
        session = self.sessions.get(digest)
        if session:
            self.session_ids.pop(session["id"], None)
            self.sessions.pop(digest, None)
        return web.json_response({"logged_out": True})

    async def refresh_tickets(self, request):
        session = await self.bearer_session(request)
        if not session:
            return error(401, "登入已失效。")
        if request.content_length and request.content_length > 1024 * 1024:
            return error(413, "素材網址清單過大。")
        try:
            payload = await request.json()
            paths = payload.get("paths")
            if not isinstance(paths, list) or len(paths) > 8192 or any(not isinstance(path, str) or not MEDIA_ROUTE.fullmatch(path) for path in paths):
                return error(400, "素材網址清單無效。")
            return web.json_response({"tickets": {path: self.ticket(session, path) for path in paths}, "ttl": TICKET_SECONDS})
        except (ValueError, AttributeError):
            return error(400, "素材網址清單無效。")

    async def proxy(self, request):
        try:
            path = clean_api_path(request.raw_path)
        except ValueError:
            return error(404, "找不到此網頁 API。")
        session = await self.bearer_session(request) or await self.media_session(request, path)
        if not session:
            return error(401, "請先登入 Studio。")
        if not route_allowed(request.method, path):
            return error(403, "此操作僅由公司主機管理者執行。")
        worker = self.pool.get(session["user_id"])
        if not worker:
            return error(503, "Studio 工作程序已停止，請重新登入。")
        query = request.query.copy()
        query.popall("ticket", None)
        headers = {key: value for key, value in request.headers.items()
                   if key.lower() in {"content-type", "content-length", "range", "if-range", "accept"}}
        if hasattr(self.pool, "headers"):
            headers.update(self.pool.headers(session["user_id"]))
        async def body():
            async for chunk in request.content.iter_chunked(256 * 1024):
                yield chunk
        async with self.client.request(request.method, worker + path, params=query, headers=headers,
                                       data=body() if request.can_read_body else None,
                                       allow_redirects=False) as upstream:
            if 300 <= upstream.status < 400:
                return error(502, "Studio 回傳不支援的轉址。")
            if "application/json" in upstream.headers.get("Content-Type", "") and request.method != "HEAD":
                # Metadata only. Actual uploads/downloads below remain streamed.
                chunks, size = [], 0
                async for chunk in upstream.content.iter_chunked(256 * 1024):
                    size += len(chunk)
                    if size > 16 * 1024**2:
                        return error(502, "Studio 資料回應過大。")
                    chunks.append(chunk)
                try:
                    value = json.loads(b"".join(chunks))
                except ValueError:
                    return error(502, "Studio 回傳無效資料。")
                tickets = {media: self.ticket(session, media) for media in self.media_paths(value)}
                # Wrap lists as well as objects without changing their public shape.
                return web.json_response({"__h3_web_payload": value, "__h3_web_tickets": tickets}, status=upstream.status)
            connection_tokens = {value.strip().lower() for value in upstream.headers.get("Connection", "").split(",")}
            response_headers = {key: value for key, value in upstream.headers.items()
                                if key.lower() not in HOP_HEADERS | connection_tokens | {"set-cookie", "location", "access-control-allow-origin"}}
            response_headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                     "X-Content-Type-Options": "nosniff"})
            origin = request.headers.get("Origin")
            if origin:
                response_headers.update(self.cors_headers(origin))
            response = web.StreamResponse(status=upstream.status, headers=response_headers)
            await response.prepare(request)
            if request.method != "HEAD":
                async for chunk in upstream.content.iter_chunked(256 * 1024):
                    await response.write(chunk)
            await response.write_eof()
            return response


def main():
    parser = argparse.ArgumentParser(description="H3 Studio authenticated company LAN API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8795)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8190")
    parser.add_argument('--remote-auth', action='store_true', help='Authenticate personal keys against the configured remote Gateway')
    parser.add_argument('--local-auth', action='store_false', dest='remote_auth', help='Authenticate with this computer GatewayStore')
    parser.add_argument('--settings', type=Path, default=APP_DIR / 'data' / 'web_server.settings.json')
    parser.add_argument("--gateway-data", type=Path, default=APP_DIR / "data")
    parser.add_argument("--worker-root", type=Path, default=APP_DIR / "data" / "web_users")
    parser.add_argument("--origin", action="append", default=None)
    parser.add_argument("--cert", type=Path)
    parser.add_argument("--key", type=Path)
    preview, _ = parser.parse_known_args()
    if preview.settings.is_file():
        try:
            settings = json.loads(preview.settings.read_text(encoding='utf-8'))
            if not isinstance(settings, dict) or set(settings) - {'gateway_url', 'remote_auth', 'origins'}:
                raise ValueError('Unknown deployment settings')
            defaults = {}
            if 'gateway_url' in settings:
                if not isinstance(settings['gateway_url'], str):
                    raise ValueError('gateway_url must be a URL string')
                defaults['gateway_url'] = settings['gateway_url']
            if 'remote_auth' in settings:
                if type(settings['remote_auth']) is not bool:
                    raise ValueError('remote_auth must be boolean')
                defaults['remote_auth'] = settings['remote_auth']
            if 'origins' in settings:
                if not isinstance(settings['origins'], list) or not settings['origins']:
                    raise ValueError('origins must be a non-empty list')
                defaults['origin'] = [origin_value(value) for value in settings['origins']]
            parser.set_defaults(**defaults)
        except (ValueError, TypeError, AttributeError):
            parser.error('Invalid private deployment settings file')
    arguments = parser.parse_args()
    if bool(arguments.cert) != bool(arguments.key):
        parser.error("--cert and --key must be provided together")
    tls = None
    if arguments.cert:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(arguments.cert, arguments.key)
    server = StudioWebServer(arguments.gateway_data, arguments.worker_root, arguments.gateway_url,
                             arguments.origin or ["https://igs-yichonezhu.github.io"], remote_auth=arguments.remote_auth)
    print("H3 Studio Web API: company LAN port", arguments.port, flush=True)
    print("Allowed web origins:", ", ".join(sorted(server.origins)), flush=True)
    web.run_app(server.create_app(), host=arguments.host, port=arguments.port,
                ssl_context=tls, access_log=None)


if __name__ == "__main__":
    main()
