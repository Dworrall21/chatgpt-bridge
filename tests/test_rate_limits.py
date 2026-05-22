#!/usr/bin/env python3
"""Focused rate-limit and concurrency tests for bridge-host.py.

This script avoids the live ChatGPT extension and exercises the reusable
request gate directly:
  1. Flood 20 concurrent /chat requests and verify no more than 3 handlers
     run at once when BRIDGE_MAX_CONCURRENT=3.
  2. Verify the sliding-window limiter returns HTTP 429 and Retry-After when
     the per-IP limit is exceeded.

Run: python3 tests/test_rate_limits.py
Exit: 0 on success, 1 on failure.
"""

import asyncio
import importlib.util
import json
from pathlib import Path

import aiohttp
from aiohttp import web

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = REPO_ROOT / "bridge-host.py"


def load_bridge_module():
    spec = importlib.util.spec_from_file_location("bridge_host", BRIDGE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {BRIDGE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def run_server(app):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server = getattr(site, "_server", None)
    sockets = getattr(server, "sockets", None) or []
    if not sockets:
        await runner.cleanup()
        raise RuntimeError("Test server did not expose a bound socket")
    port = sockets[0].getsockname()[1]
    return runner, port


async def flood_concurrency_test(bridge):
    gate, _semaphore, _limiter = bridge.make_request_gate(3, 1000)
    app = web.Application(middlewares=[gate])

    state = {"active": 0, "max_active": 0, "started": 0}
    lock = asyncio.Lock()
    release = asyncio.Event()
    all_started = asyncio.Event()

    async def slow_chat(request):
        async with lock:
            state["active"] += 1
            state["started"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            if state["started"] >= 3:
                all_started.set()
        await release.wait()
        async with lock:
            state["active"] -= 1
        return web.json_response({"ok": True})

    app.router.add_post("/chat", slow_chat)
    runner, port = await run_server(app)
    try:
        url = f"http://127.0.0.1:{port}/chat"
        async with aiohttp.ClientSession() as session:
            tasks = [asyncio.create_task(session.post(url, json={"prompt": f"msg-{i}"})) for i in range(20)]
            await asyncio.wait_for(all_started.wait(), timeout=5)
            release.set()
            responses = await asyncio.gather(*tasks)
            statuses = [resp.status for resp in responses]
            bodies = [await resp.json() for resp in responses]
        if state["max_active"] != 3:
            raise AssertionError(f"expected max_active=3, saw {state['max_active']}")
        if any(status != 200 for status in statuses):
            raise AssertionError(f"expected all 200s in pure semaphore test, got {statuses}")
        if not all(body.get("ok") is True for body in bodies):
            raise AssertionError(f"unexpected body payloads: {bodies}")
    finally:
        await runner.cleanup()


async def rate_limit_test(bridge):
    gate, _semaphore, _limiter = bridge.make_request_gate(10, 2)
    app = web.Application(middlewares=[gate])

    async def fast_chat(request):
        return web.json_response({"ok": True})

    app.router.add_post("/chat", fast_chat)
    runner, port = await run_server(app)
    try:
        url = f"http://127.0.0.1:{port}/chat"
        async with aiohttp.ClientSession() as session:
            first = await session.post(url, json={"prompt": "a"})
            second = await session.post(url, json={"prompt": "b"})
            third = await session.post(url, json={"prompt": "c"})

            first_body = await first.json()
            second_body = await second.json()
            third_text = await third.text()
            third_body = json.loads(third_text)

        if first.status != 200 or second.status != 200:
            raise AssertionError(f"expected first two requests to pass, got {first.status}, {second.status}")
        if first_body.get("ok") is not True or second_body.get("ok") is not True:
            raise AssertionError(f"unexpected success payloads: {first_body}, {second_body}")
        if third.status != 429:
            raise AssertionError(f"expected 429 on third request, got {third.status}")
        if third.headers.get("Retry-After") in (None, ""):
            raise AssertionError("missing Retry-After header on 429 response")
        if int(third.headers["Retry-After"]) < 1:
            raise AssertionError(f"invalid Retry-After header: {third.headers['Retry-After']}")
        if third_body.get("success") is not False and third_body.get("error") is None:
            raise AssertionError(f"unexpected 429 payload: {third_body}")
    finally:
        await runner.cleanup()


async def main():
    bridge = load_bridge_module()
    await flood_concurrency_test(bridge)
    await rate_limit_test(bridge)
    print("rate limit tests passed")


if __name__ == "__main__":
    asyncio.run(main())
