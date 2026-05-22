#!/usr/bin/env python3
"""
chatgpt-bridge-host.py — Local bridge between CLI tools and ChatGPT web app
via the ChatGPT bridge Chrome extension.

Flow:
  1. You POST {"prompt": "..."}  to http://127.0.0.1:11557/chat
  2. Host forwards the prompt to the Chrome extension via WebSocket
     on ws://127.0.0.1:11558
  3. Extension interacts with the ChatGPT DOM and returns the response
  4. Response flows back: Extension -> WebSocket -> Host -> HTTP response

Usage:
    python3 chatgpt-bridge-host.py              # run the bridge
    python3 chatgpt-bridge-host.py --health      # check if it is running

Send prompts:
    curl -s -X POST http://127.0.0.1:11557/chat \
         -H 'Content-Type: application/json' \
         -d '{"prompt":"Hello"}'
"""

import sys, json, argparse, subprocess, asyncio, signal, traceback, time, os, sqlite3, urllib.request, re, mimetypes
from pathlib import Path
from urllib.parse import urlparse

HTTP_PORT = 11557
WS_PORT   = 11558
CHATGPT_URL = "https://chatgpt.com/*"
CDP_PORT  = 9222

# ── State DB (BridgeState: last_conversation_id) ────────────────────────
STATE_DB      = os.path.expanduser("~/.hermes/profiles/chatgpt-bridge/state.db")
CFG_CONV_KEY  = "last_conversation_id"

# ── Chrome CDP Watchdog ───────────────────────────────────────────────
CDP_CHECK_URL       = "http://127.0.0.1:9222/json/version"
CDP_CHECK_INTERVAL  = 30   # seconds between health checks
CDP_DOWN_THRESHOLD  = 30   # seconds of downtime before restart attempt
MAX_BACKOFF        = 60   # max seconds between restart attempts
CHROME_START_SCRIPT = os.path.expanduser("~/chatgpt-extension/start-chrome.sh")

# Watchdog state (module-level so /health can read it)
cdp_down_since       = None   # timestamp when CDP first went down, or None
consecutive_failures = 0      # number of failed restart attempts
recovery_state       = "healthy"  # healthy | degraded | recovering | failed
last_recovery_attempt = 0     # timestamp of last restart attempt


# ── Dependency bootstrap ──────────────────────────────────────────────

def ensure(package):
    try:
        __import__(package)
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package, "-q",
             "--break-system-packages"],
            stdout=subprocess.DEVNULL,
        )

ensure("websockets")
ensure("aiohttp")

import websockets
from aiohttp import web

# ── CDP Health Check ──────────────────────────────────────────────────────

def check_cdp_health_sync():
    """Check Chrome CDP health via urllib (synchronous, runs in executor).
    Returns True if CDP is responding, False otherwise."""
    try:
        req = urllib.request.Request(CDP_CHECK_URL)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False

async def check_cdp_health():
    """Async wrapper for check_cdp_health_sync."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, check_cdp_health_sync)

async def restart_chrome():
    """Attempt to restart Chrome using the start-chrome.sh script.
    Returns True if the script was launched, False otherwise."""
    global consecutive_failures, last_recovery_attempt, recovery_state

    if not os.path.isfile(CHROME_START_SCRIPT):
        print(f"[watchdog] Chrome start script not found: {CHROME_START_SCRIPT}", flush=True)
        return False

    try:
        # Calculate exponential backoff: 2, 4, 8, 16, 32, 60, 60, ...
        backoff = min(2 ** (consecutive_failures + 1), MAX_BACKOFF)
        now = time.time()
        elapsed = now - last_recovery_attempt

        if elapsed < backoff:
            remaining = int(backoff - elapsed)
            print(f"[watchdog] Backoff active ({remaining}s remaining), skipping restart", flush=True)
            return False

        consecutive_failures += 1
        last_recovery_attempt = now
        recovery_state = "recovering"

        print(f"[watchdog] Restarting Chrome (attempt #{consecutive_failures}, backoff={backoff}s)", flush=True)
        subprocess.Popen(
            ["/bin/bash", CHROME_START_SCRIPT],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach from bridge process group
        )
        print("[watchdog] Chrome start script launched", flush=True)
        return True
    except Exception as e:
        print(f"[watchdog] Chrome restart failed: {e}", flush=True)
        recovery_state = "failed"
        return False

async def cdp_watchdog_loop():
    """Main watchdog loop: check CDP every 30s, restart Chrome if down for 30s+."""
    global cdp_down_since, consecutive_failures, recovery_state

    print("[watchdog] CDP health monitor started", flush=True)
    # Initial delay to let Chrome start up
    await asyncio.sleep(10)

    while True:
        try:
            await asyncio.sleep(CDP_CHECK_INTERVAL)
            healthy = await check_cdp_health()
            now = time.time()

            if healthy:
                if cdp_down_since is not None:
                    downtime = int(now - cdp_down_since)
                    print(f"[watchdog] CDP recovered after {downtime}s downtime", flush=True)
                    cdp_down_since = None
                    consecutive_failures = 0
                    recovery_state = "healthy"
                continue

            # CDP is down
            if cdp_down_since is None:
                cdp_down_since = now
                recovery_state = "degraded"
                print("[watchdog] CDP down detected", flush=True)
                continue

            downtime = int(now - cdp_down_since)
            if downtime < CDP_DOWN_THRESHOLD:
                print(f"[watchdog] CDP down for {downtime}s (threshold={CDP_DOWN_THRESHOLD}s)", flush=True)
                continue

            # CDP down for 30s+ — attempt restart
            print(f"[watchdog] CDP down for {downtime}s — attempting Chrome restart", flush=True)
            await restart_chrome()

        except asyncio.CancelledError:
            print("[watchdog] Cancelled, shutting down", flush=True)
            return
        except Exception as e:
            print(f"[watchdog] Unexpected error: {e}", flush=True)
            recovery_state = "failed"
            await asyncio.sleep(CDP_CHECK_INTERVAL)

# ── State DB helpers (BridgeState: last_conversation_id) ─────────────────

def _init_state_db():
    os.makedirs(os.path.dirname(STATE_DB), exist_ok=True)
    conn = sqlite3.connect(STATE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kv_store (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def load_last_conversation_id() -> str | None:
    try:
        conn = sqlite3.connect(STATE_DB)
        row = conn.execute(
            "SELECT value FROM kv_store WHERE key = ?", (CFG_CONV_KEY,)
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None

def save_last_conversation_id(cid: str | None) -> None:
    try:
        conn = sqlite3.connect(STATE_DB)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        if cid is None:
            conn.execute("DELETE FROM kv_store WHERE key = ?", (CFG_CONV_KEY,))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value, updated_at) VALUES (?, ?, ?)",
                (CFG_CONV_KEY, cid, ts),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass

# ── CDP helpers (Chrome DevTools Protocol) ─────────────────────────────────

def _find_chatgpt_tab_cdp() -> tuple[str | None, str | None]:
    """Find a ChatGPT tab via CDP. Returns (page_id, url) or (None, None)."""
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3)
        tabs = json.loads(req.read())
        for t in tabs:
            if "chatgpt.com" in t.get("url", ""):
                return t["id"], t.get("url", "")
    except Exception:
        pass
    return None, None


# ── Server state ──────────────────────────────────────────────────────
connected = set()
pending   = {}
counter   = 0


# ── WebSocket handler (extension content script connects here) ─────────

async def ws_handler(ws):
    connected.add(ws)
    print(f"[bridge] ChatGPT extension connected ({len(connected)} total)", flush=True)
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "response":
                rid = msg.get("id")
                if rid in pending:
                    result = {
                        "text":                msg.get("text", ""),
                        "conversation_id":     msg.get("conversation_id"),
                        "conversation_title":  msg.get("conversation_title"),
                    }
                    # Persist conversation_id to BridgeState each time it appears
                    cid = result["conversation_id"]
                    if cid:
                        save_last_conversation_id(cid)
                    pending[rid].set_result(result)
                    del pending[rid]

            elif msg.get("type") == "error":
                rid = msg.get("id")
                if rid in pending:
                    pending[rid].set_exception(
                        RuntimeError(msg.get("error", "unknown"))
                    )
                    del pending[rid]

    except Exception as e:
        print(f"[bridge] WS handler error: {e}", flush=True)
    finally:
        connected.discard(ws)
        print(f"[bridge] Extension disconnected ({len(connected)} total)", flush=True)
        # Fail any pending request immediately so caller isn't left hanging
        for rid, fut in list(pending.items()):
            if not fut.done():
                fut.set_exception(RuntimeError("Bridge connection lost"))
            del pending[rid]


# ── HTTP handlers ─────────────────────────────────────────────────────

async def health(request):
    # Check CDP status for watchdog reporting
    cdp_healthy = await check_cdp_health()
    cdp_downtime = None
    if cdp_down_since is not None:
        cdp_downtime = int(time.time() - cdp_down_since)

    return web.json_response({
        "status":           "ok" if cdp_healthy else "degraded",
        "extensions":       len(connected),
        "target":           "https://chatgpt.com/*",
        "cdp": {
            "healthy":           cdp_healthy,
            "recovery_state":    recovery_state,
            "down_since":        cdp_down_since,
            "downtime_seconds":  cdp_downtime,
            "consecutive_failures": consecutive_failures,
            "last_recovery":     last_recovery_attempt,
        },
    })


async def chat(request):
    global counter
    try:
        raw = await request.read()
        body = json.loads(raw)
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    prompt = body.get("prompt", "").strip()
    if not prompt:
        # OpenAI-style: fall back to last user message
        for m in reversed(body.get("messages", [])):
            if m.get("role") == "user" and m.get("content"):
                prompt = m["content"].strip()
                break
    model_search = body.get("model_search") or None
    conversation_id = body.get("conversation_id") or None
    files = body.get("files", [])
    if not prompt and not files:
        return web.json_response({"error": "Missing or empty prompt and no files"}, status=400)

    # ─── CDP file upload (images, PDFs, etc.) ─────────────────────────────
    # Runs before the extension check so the composer already has the files
    # when the text prompt arrives.
    if files:
        file_paths: list[str] = []
        for f in files:
            fp = f if isinstance(f, str) else f.get("path", f.get("url", ""))
            if fp.startswith("file://"):
                fp = fp[7:]   # strip file:// prefix
            file_paths.append(fp)
        if file_paths:
            print(f"[bridge] CDP upload: {len(file_paths)} file(s)", flush=True)
            ok, err = await upload_files_cdp(file_paths)
            if not ok:
                return web.json_response({"success": False, "error": f"File upload failed: {err}"}, status=500)
            print(f"[bridge] CDP upload complete", flush=True)

    if not connected:
        return web.json_response(
            {"error": "No ChatGPT tab connected. Open https://chatgpt.com and load the extension first."},
            status=503,
        )

    timeout = body.get("timeout", 180)
    counter += 1
    rid = str(counter)
    fut = asyncio.get_event_loop().create_future()
    pending[rid] = fut

    msg = json.dumps({
        "type":            "prompt",
        "id":              rid,
        "prompt":          prompt,
        "model_search":    model_search,
        "conversation_id": conversation_id,
    })
    print(f"[bridge] -> {prompt[:60]!r}  conv={conversation_id or 'new'}", flush=True)

    dead = []
    for ext in list(connected):
        try:
            await ext.send(msg)
        except Exception as e:
            print(f"[bridge] Send error (marking dead): {e}", flush=True)
            dead.append(ext)

    for ext in dead:
        connected.discard(ext)

    try:
        result = await asyncio.wait_for(fut, timeout=float(timeout))
        # result is a dict with text + (optionally) conversation_id + conversation_title
        response_payload = {
            "success":            True,
            "response":           result["text"] if isinstance(result, dict) else result,
            "conversation_id":    result.get("conversation_id") if isinstance(result, dict) else None,
            "conversation_title": result.get("conversation_title") if isinstance(result, dict) else None,
        }
        print(f"[bridge] <- {len(response_payload['response'])} chars  conv={response_payload['conversation_id'] or 'none'}", flush=True)
        return web.json_response(response_payload)
    except asyncio.TimeoutError:
        pending.pop(rid, None)
        print(f"[bridge] Timeout ({timeout}s)", flush=True)
        return web.json_response(
            {"success": False, "error": f"Timed out ({timeout}s)"}, status=504
        )
    except Exception as e:
        pending.pop(rid, None)
        return web.json_response(
            {"success": False, "error": str(e)}, status=500
        )


async def v1_chat_completions(request):
    """OpenAI-compatible /v1/chat/completions endpoint.
    Maps {model, messages[...], conversation_id} -> /chat flow.
    conversation_id is forwarded verbatim (accepts both new and existing).
    """
    global counter
    try:
        raw = await request.read()
        body = json.loads(raw)
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    messages   = body.get("messages", [])
    model      = body.get("model") or None
    # Extract last user message as prompt text, and gather file paths
    # from multi-part content (image_url with file:// or absolute paths) and
    # from the top-level "files" array.
    prompt = ""
    cdp_file_paths: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for p in content:
                if p.get("type") == "text":
                    text_parts.append(p.get("text", ""))
                elif p.get("type") == "image_url":
                    url = p.get("image_url", {}).get("url", "")
                    if url.startswith("file://"):
                        cdp_file_paths.append(url[7:])
                    elif url.startswith("/") or (len(url) > 1 and url[1] == ":"):
                        cdp_file_paths.append(url)
            # Also check for explicit top-level files array
            content = " ".join(text_parts)
        if role == "user":
            prompt = content.strip()
    if not prompt:
        return web.json_response({"error": "No user message in request"}, status=400)

    # Also check for top-level 'files' parameter (simple path list)
    for f in body.get("files", []):
        fp = f if isinstance(f, str) else f.get("path", f.get("url", ""))
        if fp.startswith("file://"):
            fp = fp[7:]
        cdp_file_paths.append(fp)

    # ─── CDP file upload (images, PDFs, etc.) ─────────────────────────
    if cdp_file_paths:
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_paths: list[str] = []
        for p in cdp_file_paths:
            if p not in seen:
                seen.add(p)
                unique_paths.append(p)
        print(f"[bridge] CDP upload: {len(unique_paths)} file(s) for /v1/chat/completions", flush=True)
        ok, err = await upload_files_cdp(unique_paths)
        if not ok:
            return web.json_response({"error": {"message": f"File upload failed: {err}", "type": "server_error"}}, status=500)
        print(f"[bridge] CDP upload complete", flush=True)

    conversation_id = body.get("conversation_id") or None

    if not connected:
        return web.json_response(
            {"error": "No ChatGPT tab connected. Open https://chatgpt.com and load the extension first."},
            status=503,
        )

    timeout  = body.get("timeout", 180)
    counter += 1
    rid     = str(counter)
    fut     = asyncio.get_event_loop().create_future()
    pending[rid] = fut

    model_search = f"model {model}" if model else None
    msg = json.dumps({
        "type":            "prompt",
        "id":              rid,
        "prompt":          prompt,
        "model_search":    model_search,
        "conversation_id": conversation_id,
        "files":           [],   # files already uploaded via CDP
    })
    print(f"[bridge] /v1 -> {prompt[:60]!r}  conv={conversation_id or 'new'}  model={model or 'default'}", flush=True)

    dead = []
    for ext in list(connected):
        try:
            await ext.send(msg)
        except Exception as e:
            print(f"[bridge] Send error (marking dead): {e}", flush=True)
            dead.append(ext)

    for ext in dead:
        connected.discard(ext)

    try:
        result = await asyncio.wait_for(fut, timeout=float(timeout))
        response_text   = result["text"] if isinstance(result, dict) else result
        response_cid    = result.get("conversation_id") if isinstance(result, dict) else None
        response_title  = result.get("conversation_title") if isinstance(result, dict) else None

        # OpenAI-compatible response shape
        response_payload = {
            "id":      f"chatcmpl-{rid}",
            "object":  "chat.completion",
            "created": int(time.time()),
            "model":   model or "unknown",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role":    "assistant",
                        "content": response_text,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens":     0,
                "completion_tokens": len(response_text.split()),
                "total_tokens":      len(response_text.split()),
            },
            "conversation_id":    response_cid,
            "conversation_title": response_title,
        }
        print(f"[bridge] /v1 <- {len(response_text)} chars  conv={response_cid or 'none'}", flush=True)
        return web.json_response(response_payload)
    except asyncio.TimeoutError:
        pending.pop(rid, None)
        print(f"[bridge] /v1 Timeout ({timeout}s)", flush=True)
        return web.json_response(
            {"error": {"message": f"Timed out ({timeout}s)", "type": "timeout_error"}},
            status=504,
        )
    except Exception as e:
        pending.pop(rid, None)
        return web.json_response(
            {"error": {"message": str(e), "type": "api_error"}},
            status=500,
        )


async def cors_handler(request):
    return web.Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    })


# ── Main ──────────────────────────────────────────────────────────────

async def main():
    _init_state_db()
    app = web.Application(middlewares=[error_mw])
    app.router.add_get("/health",  health)
    app.router.add_post("/chat",   chat)
    app.router.add_post("/v1/chat/completions", v1_chat_completions)
    app.router.add_options("/chat", cors_handler)

    # ─── Dynamic imports with fall-back install (moved here to avoid slow/absent deps at startup) ───

    try:
        import websockets
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "websockets", "-q"],
            stdout=subprocess.DEVNULL,
        )
        import websockets

    connected = set()
    pending   = {}
    counter   = 0

    # ─── CDP: local helpers for file drag-and-drop ─────────────────────
    # Uses Chrome DevTools Protocol Input.dispatchDragEvent to drop files
    # onto the ChatGPT composer area without needing the extension path.

    async def cdp_eval(ws, expression, timeout_s=10):
        """Evaluate JavaScript in the active ChatGPT tab via CDP Runtime.evaluate."""
        rid = int(time.time() * 1000000) % 1000000
        await ws.send(json.dumps({
            "id": rid, "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True, "timeout": int(timeout_s * 1000)}
        }))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s + 5)
            data = json.loads(raw)
            if data.get("id") == rid:
                exc = data.get("result", {}).get("exceptionDetails")
                if exc:
                    raise RuntimeError(f"CDP eval error: {exc.get('text', '')}")
                return data["result"]["result"].get("value")

    async def upload_files_cdp(file_paths):
        """Upload files to the ChatGPT composer via CDP Input.dispatchDragEvent.

        Args:
            file_paths: list of absolute file paths on disk.

        Returns:
            (success: bool, error_message: str | None)
        """
        page_id, _ = _find_chatgpt_tab_cdp()
        if not page_id:
            return False, (
                "No ChatGPT tab found. Open https://chatgpt.com/ "
                "and ensure Chrome is running with --remote-debugging-port=9222"
            )

        # Validate and resolve
        resolved: list[str] = []
        for fp in file_paths:
            p = Path(fp).expanduser().resolve()
            if not p.exists():
                return False, f"File not found: {fp}"
            resolved.append(str(p))

        ws_url = f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{page_id}"

        try:
            async with websockets.connect(ws_url, max_size=2**20) as ws:
                # Find composer drop-zone centre by querying the input element rect
                pos = await cdp_eval(ws, """(function() {
    var el = document.querySelector('#prompt-textarea');
    if (!el) el = document.querySelector('div[contenteditable="true"][role="textbox"]');
    if (!el) el = document.querySelector('div.ProseMirror[contenteditable]');
    if (!el) return JSON.stringify({error: 'no_input_found'});
    var rect = el.getBoundingClientRect();
    return JSON.stringify({
        x: Math.round(rect.left + rect.width / 2),
        y: Math.round(rect.top + rect.height / 2)
    });
})()""", timeout_s=5)

                if not pos:
                    return False, "Could not locate ChatGPT input area (empty response)"

                try:
                    pos_data = json.loads(pos) if isinstance(pos, str) else pos
                except (json.JSONDecodeError, TypeError):
                    return False, "Could not parse input area position"

                if isinstance(pos_data, dict) and "error" in pos_data:
                    return False, f"Could not find ChatGPT input area: {pos_data.get('error', 'unknown')}"

                x, y = pos_data["x"], pos_data["y"]
                print(f"[bridge] CDP drop zone at ({x}, {y})", flush=True)

                for abs_path in resolved:
                    mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
                    basename = os.path.basename(abs_path)

                    for ev_type in ("dragEnter", "dragOver", "drop"):
                        await ws.send(json.dumps({
                            "id": 20, "method": "Input.dispatchDragEvent",
                            "params": {
                                "type": ev_type, "x": x, "y": y,
                                "data": {
                                    "items": [{"mimeType": mime, "data": basename}],
                                    "dragOperationsMask": 1,
                                    "files": [abs_path],
                                },
                                "modifiers": 0,
                            },
                        }))
                        await asyncio.sleep(0.3)
                        try:
                            await asyncio.wait_for(ws.recv(), timeout=1)
                        except asyncio.TimeoutError:
                            pass

                    await asyncio.sleep(0.5)
                    print(f"[bridge] CDP uploaded: {basename}", flush=True)

                # Brief wait for file thumbnails to appear in the composer
                for _ in range(10):
                    check = await cdp_eval(ws, """(function() {
    var att = document.querySelectorAll(
        '[data-testid*="attachment"], [class*="attachment"], [class*="file-preview"], [class*="FilePreview"]'
    );
    return JSON.stringify({found: att.length > 0, count: att.length});
})()""", timeout_s=3)
                    if check:
                        r2 = json.loads(check) if isinstance(check, str) else check
                        if isinstance(r2, dict) and r2.get("found"):
                            break
                    await asyncio.sleep(0.5)

            return True, None

        except Exception as e:
            return False, f"CDP upload error: {e}"

    # ── WebSocket handler ─────────────────────────────────────────────────

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", HTTP_PORT)
    await site.start()
    print(f"[bridge] HTTP  http://127.0.0.1:{HTTP_PORT}/chat  (target=chatgpt.com)", flush=True)

    ws_server = await websockets.serve(
        ws_handler, "127.0.0.1", WS_PORT,
        close_timeout=5,
        ping_interval=30,
        ping_timeout=10,
    )
    print(f"[bridge] WS    ws://127.0.0.1:{WS_PORT}", flush=True)

    # Start CDP watchdog
    watchdog_task = asyncio.create_task(cdp_watchdog_loop())
    print("[bridge] CDP watchdog started (checks every 30s)", flush=True)

    print("[bridge] Ready. Send prompts via POST /chat.", flush=True)

    # Run forever until cancelled
    try:
        await asyncio.Event().wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()


async def error_mw(app, handler):
    async def mw(request):
        try:
            return await handler(request)
        except Exception as e:
            print(f"[bridge] Unhandled: {e}", flush=True)
            traceback.print_exc()
            return web.json_response({"error": str(e)}, status=500)
    return mw


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()

    if args.health:
        import urllib.request
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{HTTP_PORT}/health", timeout=3)
            print(json.loads(r.read()))
        except Exception as e:
            print(f"Not reachable: {e}")
    else:
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
        asyncio.run(main())
