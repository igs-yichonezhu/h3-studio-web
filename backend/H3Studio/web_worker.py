"""A private, per-user Studio process. Only web_server can reach its socket."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import hmac
from pathlib import Path

from aiohttp import web


async def serve(ready_file: Path) -> None:
    config = json.loads(Path(os.environ["H3_STUDIO_CONFIG_PATH"]).read_text(encoding="utf-8"))
    if config.get("studio_role") != "client" or config.get("mode") != "remote" or config.get("auto_start_local"):
        raise RuntimeError("Web workers require remote client settings.")
    # Import after the environment and configuration have been prepared.
    from app import create_app
    application = create_app()
    secret = os.environ.pop("H3_WORKER_TOKEN")
    @web.middleware
    async def broker_only(request, handler):
        if not hmac.compare_digest(request.headers.get("X-H3-Worker-Token", ""), secret):
            return web.json_response({"error": "Private Studio worker"}, status=401)
        return await handler(request)
    application.middlewares.insert(0, broker_only)
    async def refresh_connection(request):
        payload = await request.json()
        token = payload.get("remote_access_token")
        if not isinstance(token, str) or not token or len(token) > 256:
            return web.json_response({"error": "Invalid credential"}, status=400)
        updated = application["settings"].update({
            "mode": "remote", "base_url": config["base_url"], "auto_start_local": False,
            "remote_access_token": token,
        })
        application["comfy"].configure(updated)
        return web.json_response({"updated": True})
    application.router.add_post("/internal/web/connection", refresh_connection)
    runner = web.AppRunner(application, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    try:
        await site.start()
        port = runner.addresses[0][1]
        temporary = ready_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")
        temporary.replace(ready_file)
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        ready_file.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-file", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        asyncio.run(serve(arguments.ready_file))
    except KeyboardInterrupt:
        pass
