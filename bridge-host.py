#!/usr/bin/env python3
"""
chatgpt-bridge-host.py — Local bridge between CLI tools and ChatGPT web.
Uses aiohttp for reliable HTTP + WebSocket serving.

Architecture (same pattern as the gemini bridge, extension-owned WebSocket):
  1. You POST JSON to http://127.0.0.1:11557/chat   →  {"prompt": "..."}
  2. Host forwards the prompt to the Chrome extension via WebSocket (11558)
  3. Extension interacts with the ChatGPT DOM and returns the response
  4. Response flows back: Extension → WS → Host → HTTP JSON response

SSRF protection, conversational state, and metrics are carried over directly.
Structured JSONLines logging on stderr, configurable via --log-level.
"""

import sys
import io
import json
import argparse
import logging
import subprocess
import asyncio
import signal
import traceback
import time
import os
import re
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

HTTP_PORT = 11557
WS_PORT   = 11558
CDP_PORT  = 9222

# ─── SSRF Protection ───
_TRUSTED_URL_HOSTS = frozenset({
    "chatgpt.com",
    "chat.openai.com",
    "openai.com",
    "openai-svc.com",
})
_TRUSTED_URL_ALLOW_ALL_HTTPS = True
_BLOCKED_URL_HOSTS = frozenset({
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "169.254.169.254", "metadata.google.internal",
})
_blocked_netlo = [
    (int("0a000000", 16), int("0a000000", 16) | 0x00ffffff),
    (int("ac100000", 16), int("ac100000", 16) | 0x0000ffff),
    (int("c0a80000", 16), int("c0a80000", 16) | 0x0000ffff),
    (int("a9fe0000", 16), int("a9feffff", 16)),
]


def _ip_to_int(ip_str):
    parts = ip_str.split(".")
    if len(parts) != 4:
        return None
    try:
        return (int(parts[0]) << 24) | (int(parts[1]) << 16) | (int(parts[2]) << 8) | int(parts[3])
    except ValueError:
        return None


def is_trusted_url(url):
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    scheme = parsed.scheme.lower()
    if scheme not in ("https", "http"):
        return False
    host = (parsed.hostname or "").lower().strip()
    if not host:
        return False
    if host in _BLOCKED_URL_HOSTS:
        return False
    ip_int = _ip_to_int(host)
    if ip_int is not None:
        for netlo, netlo_broadcast in _blocked_netlo:
            if netlo <= ip_int <= netlo_broadcast:
                return False
        if ip_int == int("a9fea9fe", 16):
            return False
    if not _TRUSTED_URL_ALLOW_ALL_HTTPS:
        if host not in _TRUSTED_URL_HOSTS:
            if not any(host == h or host.endswith("." + h) for h in _TRUSTED_URL_HOSTS):
                return False
    return True


def sanitize_url_for_logging(url):
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
    except Exception:
        return "<invalid url>"


# ─── Atomic file writes ───
def atomic_write(path, content):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        if isinstance(content, bytes):
            tmp.write_bytes(content)
        else:
            tmp.write_text(content)
        os.replace(str(tmp), str(p))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ─── Persistent bridge state ───
_state_dir = Path.home() / ".hermes" / "chatgpt_bridge_state"
_state_file = _state_dir / "state.json"


class BridgeState:
    def __init__(self, path=None):
        self._path = Path(path) if path else _state_file
        self._data = {}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def save(self):
        try:
            atomic_write(str(self._path), json.dumps(self._data, indent=2))
        except OSError as e:
            log.warning("state_save_failed", extra={"error": str(e)})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    @property
    def last_conversation_id(self):
        return self._data.get("last_conversation_id")

    @last_conversation_id.setter
    def last_conversation_id(self, value):
        self._data["last_conversation_id"] = value
        self.save()

    @property
    def last_conversation_title(self):
        return self._data.get("last_conversation_title")

    @last_conversation_title.setter
    def last_conversation_title(self, value):
        self._data["last_conversation_title"] = value
        self.save()


# ─── Structured JSONLogging ───

_LEVEL_NAMES = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}


class _JsonLinesFormatter(logging.Formatter):
    """Render log records as one JSON object per line (JSONLines / NDJSON)."""

    def format(self, record):
        entry = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S.") + f"{record.msecs:03.0f}",
            "level": record.levelname,
        }
        # structured extra fields from extra={...}
        for k in ("event", "rid", "assignee", "method", "status",
                   "prompt_chars", "response_chars", "elapsed_ms",
                   "error", "code", "count", "detail", "reason",
                   "duration_s", "queue_len", "qc"):
            if hasattr(record, k):
                entry[k] = getattr(record, k)
        # human-readable fallback
        entry["message"] = record.getMessage()
        return json.dumps(entry)


def _setup_logging(level_name: str = "WARNING"):
    """Configure root logger: structured JSONLines to stderr."""
    root = logging.getLogger()
    root.setLevel(_LEVEL_NAMES.get(level_name.upper(), logging.WARNING))
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JsonLinesFormatter())
        root.addHandler(handler)
    return root


import logging  # noqa – imported here to keep the _LEVEL_NAMES dict above defined before use
log = _setup_logging()  # replaced at startup from CLI --log-level


# ═══════════════════════════════════════════════════════════════════════════
# Global metrics  (initialised in main(); kept here so type checkers see them)
# ═══════════════════════════════════════════════════════════════════════════
start_time: float
total_requests: int
successful_requests: int
timed_out_requests: int
failed_requests: int
last_response_time: str | None

# Additional per-request-type counters + queue depths
per_assignee_requests: dict  # assignee -> dict(total, success, timeout, error)
watchdog_events: list        # last up to 20 {event, tabId, detail, ts}


def _find_chatgpt_tab_cdp():
    try:
        import urllib.request
        req = urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3)
        tabs = json.loads(req.read())
        for t in tabs:
            if "chatgpt.com" in t.get("url", ""):
                return t["id"], t.get("url", "")
    except Exception:
        pass
    return None, None


# ─── CDP helpers ───────────────────────────────────────────────────────────

async def cdp_eval(ws, expression, timeout_s=10):
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
    page_id, page_url = _find_chatgpt_tab_cdp()
    if not page_id:
        return False, "No ChatGPT tab found. Open https://chatgpt.com/ and ensure Chrome is running with --remote-debugging-port=9222"
    resolved = []
    for fp in file_paths:
        p = Path(fp).expanduser().resolve()
        if not p.exists():
            return False, f"File not found: {fp}"
        resolved.append(str(p))
    ws_url = f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{page_id}"
    try:
        async with websockets.connect(ws_url, max_size=2**20) as ws:
            pos = await cdp_eval(ws, """(function() {
    var el = document.querySelector('#prompt-textarea');
    if (!el) el = document.querySelector('div[contenteditable="true"][role="textbox"]');
    if (!el) el = document.querySelector('div.ProseMirror[contenteditable]');
    if (!el) return JSON.stringify({error: 'no_input_found'});
    var rect = el.getBoundingClientRect();
    return JSON.stringify({x: Math.round(rect.left + rect.width/2), y: Math.round(rect.top + rect.height/2)});
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
            log.debug("cdp_upload_start", extra={"queue_len": x, "qc": y})
            for abs_path in resolved:
                mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
                basename = os.path.basename(abs_path)
                for ev_type in ["dragEnter", "dragOver", "drop"]:
                    await ws.send(json.dumps({
                        "id": 20, "method": "Input.dispatchDragEvent",
                        "params": {"type": ev_type, "x": x, "y": y,
                                   "data": {"items": [{"mimeType": mime, "data": basename}],
                                            "dragOperationsMask": 1,
                                            "files": [abs_path]},
                                   "modifiers": 0}
                    }))
                    await asyncio.sleep(0.3)
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=1)
                    except asyncio.TimeoutError:
                        pass
                await asyncio.sleep(0.5)
                log.debug("cdp_uploaded", extra={"queue_len": abs_path})
            for _ in range(10):
                check = await cdp_eval(ws, """(function() {
    var att = document.querySelectorAll(
        '[data-testid*="attachment"], [class*="attachment"], [class*="file-preview"], [class*="FilePreview"]'
    );
    return JSON.stringify({found: att.length > 0, count: att.length});
})()""", timeout_s=3)
                if check:
                    result = json.loads(check) if isinstance(check, str) else check
                    if isinstance(result, dict) and result.get("found"):
                        break
                await asyncio.sleep(0.5)
            return True, None
    except Exception as e:
        return False, f"CDP upload error: {e}"


# ═══════════════════════════════════════════════════════════════════════════
# main()
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    global log, start_time, total_requests, successful_requests, timed_out_requests, failed_requests, last_response_time
    global per_assignee_requests, watchdog_events

    try:
        import websockets
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"], stdout=subprocess.DEVNULL)
        import websockets
    try:
        from aiohttp import web
    except ImportError:
        log.warning("installing_aiohttp")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp", "-q"], stdout=subprocess.DEVNULL)
        from aiohttp import web

    # Re-read --log-level from sys.argv because this is a re-exec path
    _re_ll = None
    for i, a in enumerate(sys.argv):
        if a == "--log-level" and i + 1 < len(sys.argv):
            _re_ll = sys.argv[i + 1]
            break
    _pre_ll = _setup_logging(_re_ll or getattr(_cli_log_level, "value", "WARNING"))
    log = _pre_ll
    start_time = time.time()
    total_requests = 0
    successful_requests = 0
    timed_out_requests = 0
    failed_requests = 0
    last_response_time = None
    log.info("bridge_starting", extra={"queue_len": HTTP_PORT, "qc": WS_PORT})

    connected_extensions = set()
    pending = {}           # rid -> asyncio.Future
    pending_meta = {}      # rid -> {conversation_id, conversation_title}
    stream_queues = {}     # rid -> asyncio.Queue (SSE streaming)
    counter = 0
    ws_lock = asyncio.Lock()

    per_assignee_requests = {}  # assignee -> {total, success, timeout, error}
    watchdog_events = []       # last 20 watchdog recovery event dicts

    state = BridgeState()
    log.info("state_loaded", extra={"queue_len": state.last_conversation_id})

    # ─── WebSocket handler ──────────────────────────────────────────────────

    async def ws_handler(ws):
        connected_extensions.add(ws)
        log.info("extension_connected", extra={"queue_len": len(connected_extensions)})
        try:
            async for raw in ws:
                msg = json.loads(raw)
                log.debug("ws_frame_from_ext",
                          extra={"event": msg.get("type"), "rid": msg.get("id"),
                                 "queue_len": len(str(msg.get("text", ""))) })
                mtype = msg.get("type")
                if mtype == "response":
                    rid = msg.get("id")
                    if rid in pending:
                        meta = {}
                        conv_id = msg.get("conversation_id")
                        conv_title = msg.get("conversation_title")
                        if conv_id:
                            meta = {"conversation_id": conv_id, "conversation_title": conv_title}
                            state.last_conversation_id = conv_id
                            state.last_conversation_title = conv_title
                        pending_meta[rid] = meta
                        pending[rid].set_result(msg.get("text", ""))
                        del pending[rid]

                elif mtype == "error":
                    rid = msg.get("id")
                    if rid in pending:
                        err_msg = msg.get("error", "unknown")
                        log.warning("ws_error_response", extra={"rid": rid, "error": err_msg})
                        pending[rid].set_exception(RuntimeError(err_msg))
                        del pending[rid]

                elif mtype == "delta":
                    rid = msg.get("id")
                    if rid in stream_queues:
                        await stream_queues[rid].put({"type": "delta", "content": msg.get("content", "")})

                elif mtype == "done":
                    rid = msg.get("id")
                    if rid in stream_queues:
                        await stream_queues[rid].put({"type": "done"})
                        del stream_queues[rid]

                elif mtype == "fresh_page":
                    pass  # no-op without CDP

                elif mtype == "watchdog_events":
                    events = msg.get("events", [])
                    watchdog_events.extend(events)
                    if len(watchdog_events) > 20:
                        watchdog_events[:] = watchdog_events[-20:]
                    log.info("watchdog_sync", extra={"count": len(events)})

        except Exception as e:
            log.error("ws_handler_exception", extra={"error": str(e)}, exc_info=True)
        finally:
            connected_extensions.discard(ws)
            log.warning("extension_disconnected", extra={"queue_len": len(connected_extensions)})
            for rid in list(pending.keys()):
                if not pending[rid].done():
                    pending[rid].set_exception(RuntimeError("WebSocket disconnected"))
                del pending[rid]
            for rid in list(stream_queues.keys()):
                stream_queues.pop(rid, None)

    # ─── Send helpers ───────────────────────────────────────────────────────

    async def _send_to_extension(msg_data):
        async with ws_lock:
            sent = False
            for ext in list(connected_extensions):
                try:
                    await ext.send(json.dumps(msg_data))
                    sent = True
                except Exception as e:
                    log.warning("ws_send_failed", extra={"error": str(e)})
            if not sent:
                raise RuntimeError("Failed to send to any extension")

    async def _send_and_wait(msg_data, timeout_s, rid):
        await _send_to_extension(msg_data)
        ts = time.time()
        result = await asyncio.wait_for(pending[rid], timeout=timeout_s)
        elapsed = (time.time() - ts) * 1000
        log.info("response_received",
                 extra={"rid": rid, "response_chars": len(result),
                        "elapsed_ms": round(elapsed)})
        return result

    # ─── Per-assignee bookkeeping ───────────────────────────────────────────

    def _incr_assignee(assignee: str, field: str):
        if not assignee:
            return
        p = per_assignee_requests.setdefault(assignee, {"total": 0, "success": 0, "timeout": 0, "error": 0})
        p[field] = p.get(field, 0) + 1
        p["total"] = p.get("total", 0) + 1

    def _extract_assignee(body) -> str:
        """Pull an 'assignee' string from the request body if present."""
        if isinstance(body.get("assignee"), str):
            return body["assignee"]
        for m in body.get("messages", []):
            a = m.get("assignee")
            if a:
                return str(a)
        return ""

    # ─── OpenAI-compatible /v1/chat/completions ─────────────────────────────

    async def chat_completions(request):
        global total_requests, successful_requests, timed_out_requests, failed_requests, last_response_time

        try:
            body = await request.json()
        except Exception as e:
            return web.json_response({"error": {"message": f"Invalid JSON: {e}", "type": "invalid_request_error"}}, status=400)

        assignee = _extract_assignee(body)
        _incr_assignee(assignee, "total")

        messages = body.get("messages", [])
        model = body.get("model", "chatgpt")
        model_search = body.get("model_search") or None
        if not model_search and model.startswith("chatgpt-"):
            model_search = model[len("chatgpt-"):]
        stream = body.get("stream", False)
        conversation_id = body.get("conversation_id") or None
        timeout_s = body.get("timeout", 10)
        if timeout_s > 600:
            timeout_s = 600

        user_msgs = [m for m in messages if m.get("role") == "user"]
        if not user_msgs:
            return web.json_response({"error": {"message": "No user message found", "type": "invalid_request_error"}}, status=400)

        for _u in body.get("files", []):
            fp = _u if isinstance(_u, str) else _u.get("path", _u.get("url", ""))
            if fp.startswith("file://"):
                fp = fp[7:]
            if fp.startswith("/") or (len(fp) > 1 and fp[1] == ":"):
                if not is_trusted_url(fp):
                    _safe = sanitize_url_for_logging(fp)
                    log.warning("ssrf_blocked_file", extra={"url": _safe})
                    return web.json_response({"error": {"message": f"URL not allowed: {_safe}", "type": "invalid_request_error"}}, status=400)
                break

        prompt_parts = []; cdp_file_paths = []
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
                content = " ".join(text_parts)
            prompt_parts.append(f"{role}: {content}")

        for f in body.get("files", []):
            fp = f if isinstance(f, str) else f.get("path", f.get("url", ""))
            if fp.startswith("file://"):
                fp = fp[7:]
            cdp_file_paths.append(fp)

        full_prompt = "\n".join(prompt_parts)
        prompt_chars = len(full_prompt)

        if cdp_file_paths:
            seen = set(); unique_paths = []
            for p in cdp_file_paths:
                if p not in seen:
                    seen.add(p); unique_paths.append(p)
            log.info("cdp_upload_begin", extra={"queue_len": len(unique_paths)})
            ok, err = await upload_files_cdp(unique_paths)
            if not ok:
                failed_requests += 1; _incr_assignee(assignee, "error")
                return web.json_response({"error": {"message": f"File upload failed: {err}", "type": "server_error"}}, status=500)
            log.info("cdp_upload_done")

        if not connected_extensions:
            for _ in range(20):
                await asyncio.sleep(0.5)
                if connected_extensions:
                    break
            if not connected_extensions:
                failed_requests += 1; _incr_assignee(assignee, "error")
                return web.json_response({"error": {"message": "No ChatGPT tab connected", "type": "server_error"}}, status=503)

        total_requests += 1; counter += 1; rid = str(counter)
        log.info("request_received",
                 extra={"rid": rid, "assignee": assignee,
                        "prompt_chars": prompt_chars,
                        "code": "v1/chat/completions"})

        msg_data = {
            "type": "prompt",
            "id": rid, "prompt": full_prompt, "options": {},
            "files": [], "timeout": timeout_s,
            "conversation_id": conversation_id, "model_search": model_search,
        }

        if stream:
            stream_queue = asyncio.Queue()
            stream_queues[rid] = stream_queue; created_at = time.time()
            async def sse_generator():
                try:
                    while True:
                        hard_elapsed = time.time() - created_at
                        if hard_elapsed >= timeout_s:
                            break
                        try:
                            item = await asyncio.wait_for(stream_queue.get(), timeout=min(1.0, max(timeout_s - hard_elapsed, 0.1)))
                        except asyncio.TimeoutError:
                            continue
                        if item["type"] == "done":
                            yield "data: [DONE]\n\n"; break
                        elif item["type"] == "delta":
                            payload_ = {"id": rid, "object": "chat.completion.chunk",
                                        "created": int(time.time()), "model": model,
                                        "choices": [{"index": 0, "delta": {"content": item["content"]}, "finish_reason": None}]}
                            yield f"data: {json.dumps(payload_)}\n\n"
                except Exception:
                    pass
                finally:
                    stream_queues.pop(rid, None); pending.pop(rid, None); pending_meta.pop(rid, None)
            try:
                await _send_to_extension(msg_data)
            except Exception as e:
                stream_queues.pop(rid, None)
                log.error("stream_send_error", extra={"rid": rid, "error": str(e)})
                failed_requests += 1; _incr_assignee(assignee, "error")
                return web.json_response({"error": {"message": str(e), "type": "server_error"}}, status=500)
            response = web.StreamResponse(
                status=200, content_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                         "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*"})
            await response.prepare(request)
            async for chunk in sse_generator():
                await response.write(chunk.encode())
            await response.write_eof()
            return response

        # non-streaming path
        pending[rid] = future = asyncio.get_event_loop().create_future()
        try:
            result = await _send_and_wait(msg_data, timeout_s, rid)
            pending.pop(rid, None)
            meta = pending_meta.pop(rid, {})
            conv_id = meta.get("conversation_id"); conv_title = meta.get("conversation_title")
            successful_requests += 1; last_response_time = time.strftime("%H:%M:%S")
            _incr_assignee(assignee, "success")
            response_chars = len(result)
            completion_id = f"chatcmpl-{int(time.time())}"
            resp = {"id": completion_id, "object": "chat.completion",
                    "created": int(time.time()), "model": model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": result},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": prompt_chars // 4,
                              "completion_tokens": response_chars // 4,
                              "total_tokens": (prompt_chars + response_chars) // 4}}
            if conv_id: resp["conversation_id"] = conv_id
            if conv_title: resp["conversation_title"] = conv_title
            return web.json_response(resp)
        except asyncio.TimeoutError:
            pending.pop(rid, None); pending_meta.pop(rid, None)
            timed_out_requests += 1; _incr_assignee(assignee, "timeout")
            log.warning("request_timeout", extra={"rid": rid, "status": "timeout",
                                                  "elapsed_ms": timeout_s * 1000})
            return web.json_response({"error": {"message": f"Timed out ({timeout_s}s)", "type": "timeout"}}, status=504)
        except Exception as e:
            pending.pop(rid, None); pending_meta.pop(rid, None)
            failed_requests += 1; _incr_assignee(assignee, "error")
            log.error("request_error", extra={"rid": rid, "status": "error", "error": str(e)})
            return web.json_response({"error": {"message": str(e), "type": "server_error"}}, status=500)

    # ─── /chat handler ──────────────────────────────────────────────────────

    async def chat(request):
        global total_requests, successful_requests, timed_out_requests, failed_requests, last_response_time
        log.debug("http_request", extra={"method": "POST", "path": "/chat"})
        try:
            raw_body = await request.read()
            body = json.loads(raw_body)
        except Exception as e:
            return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)
        prompt = body.get("prompt", "").strip()
        options = body.get("options", {}); files = body.get("files", [])
        assignee = body.get("assignee") or ""
        conversation_id = body.get("conversation_id") or state.last_conversation_id or None
        model_search = body.get("model_search") or None
        timeout_s = body.get("timeout", 10)
        if timeout_s > 600: timeout_s = 600
        if not prompt and not files:
            return web.json_response({"error": "Missing or empty prompt"}, status=400)

        _url_re = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
        for _u in _url_re.findall(prompt):
            if not is_trusted_url(_u):
                _safe = sanitize_url_for_logging(_u)
                log.warning("ssrf_blocked", extra={"url": _safe})
                return web.json_response({"error": f"URL not allowed (SSRF protection): {_safe}"}, status=400)

        if files:
            file_paths = []
            for f in files:
                fp = f if isinstance(f, str) else f.get("path", f.get("url", ""))
                if fp.startswith("file://"):
                    fp = fp[7:]
                file_paths.append(fp)
            log.info("cdp_upload_begin", extra={"queue_len": len(file_paths)})
            ok, err = await upload_files_cdp(file_paths)
            if not ok:
                failed_requests += 1; _incr_assignee(assignee, "error")
                return web.json_response({"success": False, "error": f"File upload failed: {err}"}, status=500)
            log.info("cdp_upload_done")

        if not connected_extensions:
            log.warning("no_extension_waiting")
            for _ in range(20):
                await asyncio.sleep(0.5)
                if connected_extensions:
                    log.info("extension_connected_after_wait"); break
            if not connected_extensions:
                failed_requests += 1; _incr_assignee(assignee, "error")
                return web.json_response({"error": "No ChatGPT tab connected"}, status=503)

        total_requests += 1; counter += 1; rid = str(counter)
        log.info("request_received",
                 extra={"rid": rid, "assignee": assignee,
                        "prompt_chars": len(prompt),
                        "code": "/chat"})
        pending[rid] = future = asyncio.get_event_loop().create_future()
        msg_data = {"type": "prompt", "id": rid, "prompt": prompt,
                    "options": options, "files": [], "timeout": timeout_s,
                    "conversation_id": conversation_id, "model_search": model_search}
        try:
            result = await _send_and_wait(msg_data, timeout_s, rid)
            pending.pop(rid, None)
            meta = pending_meta.pop(rid, {})
            conv_id = meta.get("conversation_id"); conv_title = meta.get("conversation_title")
            successful_requests += 1; last_response_time = time.strftime("%H:%M:%S")
            _incr_assignee(assignee, "success")
            response_chars = len(result)
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and "type" in parsed:
                    resp = {"success": True, **parsed}
                    if conv_id: resp["conversation_id"] = conv_id
                    if conv_title: resp["conversation_title"] = conv_title
                    return web.json_response(resp)
            except (json.JSONDecodeError, TypeError):
                pass
            return web.json_response({"success": True, "type": "text", "text": result,
                                      **({"conversation_id": conv_id} if conv_id else {}),
                                      **({"conversation_title": conv_title} if conv_title else {})})
        except asyncio.TimeoutError:
            pending.pop(rid, None); pending_meta.pop(rid, None)
            timed_out_requests += 1; _incr_assignee(assignee, "timeout")
            log.warning("request_timeout", extra={"rid": rid, "status": "timeout",
                                                  "elapsed_ms": timeout_s * 1000})
            return web.json_response({"success": False, "error": f"Timed out ({timeout_s}s)"})
        except Exception as e:
            pending.pop(rid, None); pending_meta.pop(rid, None)
            failed_requests += 1; _incr_assignee(assignee, "error")
            log.error("request_error", extra={"rid": rid, "status": "error", "error": str(e)})
            return web.json_response({"success": False, "error": str(e)})

    # ─── /health handler ────────────────────────────────────────────────────

    async def health(request):
        uptime_s = int(time.time() - start_time)
        cdp_page_id, cdp_url = _find_chatgpt_tab_cdp()
        cdp_status = {"available": cdp_page_id is not None, "page_id": cdp_page_id, "url": cdp_url}
        payload = {
            "status": "ok" if cdp_page_id else "degraded",
            "extensions": len(connected_extensions),
            "cdp": cdp_status,
            "uptime": f"{uptime_s // 3600}h{(uptime_s % 3600) // 60}m{uptime_s % 60}s",
            "uptime_seconds": uptime_s,
            "requests": {"total": total_requests, "success": successful_requests,
                         "timeout": timed_out_requests, "failed": failed_requests},
            "pending": len(pending),
            "stream_queues": len(stream_queues),
            "last_response": last_response_time,
            "last_conversation_id": state.last_conversation_id,
            "last_conversation_title": state.last_conversation_title,
            "watchdog_recovery": watchdog_events[-5:],
            "per_assignee_requests": per_assignee_requests,
        }
        log.debug("health_requested", extra={"queues": len(stream_queues), "pending": len(pending)})
        return web.json_response(payload)

    async def cdp_status(request):
        page_id, page_url = _find_chatgpt_tab_cdp()
        if page_id:
            return web.json_response({"available": True, "page_id": page_id, "url": page_url,
                                       "cdp_port": CDP_PORT, "cdp": True, "blocked": False})
        return web.json_response({"available": False, "page_id": None, "url": None,
                                   "cdp_port": CDP_PORT, "cdp": False, "blocked": False,
                                   "hint": "Start Chrome with --remote-debugging-port=9222 and open https://chatgpt.com/"}, status=404)

    async def reload_ext(request):
        if not connected_extensions:
            return web.json_response({"success": False, "error": "No extensions connected"})
        msg = {"type": "reload"}
        async with ws_lock:
            for ext in list(connected_extensions):
                try:
                    await ext.send(json.dumps(msg))
                except Exception as e:
                    log.warning("reload_send_error", extra={"error": str(e)})
        return web.json_response({"success": True, "message": "Reload sent to extensions"})

    async def cors(request):
        return web.Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        })

    # ─── Wire up ────────────────────────────────────────────────────────────

    @web.middleware
    async def error_mw(request, handler):
        try:
            return await handler(request)
        except Exception as e:
            log.error("unhandled_error", extra={"error": str(e)}, exc_info=True)
            traceback.print_exc()
            return web.json_response({"error": str(e)}, status=500)

    app = web.Application(middlewares=[error_mw])
    app.router.add_get("/health", health)
    app.router.add_get("/v1/cdp/status", cdp_status)
    app.router.add_post("/reload", reload_ext)
    app.router.add_post("/chat", chat)
    app.router.add_post("/v1/chat/completions", chat_completions)
    app.router.add_options("/v1/chat/completions", cors)
    app.router.add_options("/chat", cors)
    app.router.add_options("/v1/cdp/status", cors)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", HTTP_PORT)
    await site.start()
    log.info("http_listening", extra={"queue_len": HTTP_PORT})
    log.info("ws_listening", extra={"qc": WS_PORT})
    log.info("bridge_ready")

    try:
        await asyncio.Event().wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ChatGPT Bridge Host")
    _cli_log_level = type("_ns", (), {})()
    parser.add_argument("--log-level", default="WARNING",
                        help="Python log level (DEBUG, INFO, WARNING, ERROR)")
    parser.add_argument("--health", action="store_true",
                        help="Hit /health once and exit")
    args = parser.parse_args()
    _cli_log_level.value = args.log_level
    _setup_logging(args.log_level)
    log = logging.getLogger()

    if args.health:
        import urllib.request
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{HTTP_PORT}/health", timeout=3)
            print(json.dumps(json.loads(req.read()), indent=2), flush=True)
        except Exception as e:
            print(f"Not reachable: {e}", flush=True)
    else:
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
        asyncio.run(main())
