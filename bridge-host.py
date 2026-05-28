#!/usr/bin/env python3
"""
bridge-host.py — Local bridge between CLI tools and ChatGPT web.
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
import uuid
import math
import re
import mimetypes
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web

HTTP_PORT = 11557
WS_PORT   = 11558
CDP_PORT  = 9222

# Default to ChatGPT Temporary Chat for Hermes/provider calls. Temporary Chat is
# the ChatGPT-side privacy boundary: it does not use saved account memories and
# does not create/update user memory from the conversation. Set
# CHATGPT_BRIDGE_PRIVACY_MODE=standard to opt out for manual/default bridge use.
DEFAULT_PRIVACY_MODE = os.getenv("CHATGPT_BRIDGE_PRIVACY_MODE", "temporary").strip().lower()
TEMPORARY_CHAT_URL = os.getenv("CHATGPT_BRIDGE_TEMPORARY_CHAT_URL", "https://chatgpt.com/?temporary-chat=true")

# ─── SSRF Protection ───
_TRUSTED_URL_HOSTS = frozenset({
    "chatgpt.com",
    "chat.openai.com",
    "openai.com",
    "openai-svc.com",
})
_TRUSTED_URL_ALLOW_ALL_HTTPS = os.getenv("BRIDGE_TRUST_ALL_HTTPS", "0").strip().lower() in {"1","true","yes","on"}
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


def validate_local_file_path(raw_path):
    """Return resolved local file path string, or raise ValueError."""
    if not raw_path:
        raise ValueError("Empty file path")

    path = str(raw_path)
    if path.startswith("file://"):
        path = path[7:]

    # Reject remote URLs here. Remote URL download/upload is not implemented.
    parsed = urlparse(path)
    if parsed.scheme in {"http", "https"}:
        raise ValueError(f"Remote file URLs are not supported for upload: {sanitize_url_for_logging(path)}")

    p = Path(path).expanduser().resolve()

    allowed_roots = [Path.home().resolve(), Path("/tmp").resolve()]
    if not any(p == root or root in p.parents for root in allowed_roots):
        raise ValueError(f"File path is outside allowed roots: {p}")

    if not p.exists():
        raise ValueError(f"File not found: {p}")
    if not p.is_file():
        raise ValueError(f"Not a regular file: {p}")

    return str(p)


def _is_truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}



if _TRUSTED_URL_ALLOW_ALL_HTTPS:
    logging.getLogger("bridge").warning(
        "SSRF relaxed mode enabled via BRIDGE_TRUST_ALL_HTTPS=1; all public HTTPS hosts are allowed"
    )

def format_uptime(seconds):
    seconds = max(int(seconds or 0), 0)
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m"


_MODEL_CATALOG_CANDIDATES = [
    Path("/home/david/.hermes/cache/model_catalog.json"),
    Path.home() / ".hermes" / "cache" / "model_catalog.json",
    Path.home() / ".hermes" / "chatgpt_bridge_state" / "model_catalog.json",
    Path.home() / ".hermes" / "chatgpt_bridge_state" / "models.json",
]
_MODEL_CATALOG_CACHE_TTL_SECONDS = 10
_MODEL_CATALOG_CACHE_MODELS = None
_MODEL_CATALOG_CACHE_SOURCE_PATH = None
_MODEL_CATALOG_CACHE_SOURCE_MTIME = None
_MODEL_CATALOG_CACHE_LOADED_AT = None


def load_available_models():
    global _MODEL_CATALOG_CACHE_MODELS
    global _MODEL_CATALOG_CACHE_SOURCE_PATH
    global _MODEL_CATALOG_CACHE_SOURCE_MTIME
    global _MODEL_CATALOG_CACHE_LOADED_AT

    now = time.monotonic()
    cached_models = _MODEL_CATALOG_CACHE_MODELS
    cached_path = _MODEL_CATALOG_CACHE_SOURCE_PATH
    cached_mtime = _MODEL_CATALOG_CACHE_SOURCE_MTIME
    loaded_at = _MODEL_CATALOG_CACHE_LOADED_AT

    if (
        cached_models is not None
        and cached_path is not None
        and cached_mtime is not None
        and loaded_at is not None
        and (now - loaded_at) <= _MODEL_CATALOG_CACHE_TTL_SECONDS
    ):
        try:
            if cached_path.exists() and cached_path.stat().st_mtime == cached_mtime:
                return list(cached_models)
        except OSError:
            pass

    for path in _MODEL_CATALOG_CANDIDATES:
        try:
            if not path.exists():
                continue
            source_mtime = path.stat().st_mtime
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, TypeError):
            continue

        available = []
        providers = data.get("providers", {}) if isinstance(data, dict) else {}
        if isinstance(providers, dict):
            for provider_data in providers.values():
                if not isinstance(provider_data, dict):
                    continue
                for model in provider_data.get("models", []) or []:
                    if isinstance(model, dict):
                        model_id = model.get("id") or model.get("model")
                    else:
                        model_id = model
                    if model_id:
                        available.append(str(model_id))
        elif isinstance(data, dict):
            raw_models = data.get("available") or data.get("models") or []
            for model in raw_models:
                if isinstance(model, dict):
                    model_id = model.get("id") or model.get("model")
                else:
                    model_id = model
                if model_id:
                    available.append(str(model_id))

        if available:
            models = sorted(dict.fromkeys(available))
            _MODEL_CATALOG_CACHE_MODELS = list(models)
            _MODEL_CATALOG_CACHE_SOURCE_PATH = path
            _MODEL_CATALOG_CACHE_SOURCE_MTIME = source_mtime
            _MODEL_CATALOG_CACHE_LOADED_AT = now
            return models
    return []


def summarize_watchdog_events(events):
    events = list(events or [])
    last = events[-1] if events else None
    if not isinstance(last, dict):
        last = {}
    return {
        "recovery_events": len(events),
        "last_recovery": last.get("ts"),
        "chrome_alive": bool(last.get("chrome_alive", False)),
        "events": events[-5:],
    }


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


def _env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    if min_value is not None and value < min_value:
        value = min_value
    return value


def _client_ip(request) -> str:
    remote = getattr(request, "remote", None)
    if remote:
        return str(remote)
    transport = getattr(request, "transport", None)
    if transport is not None:
        peer = transport.get_extra_info("peername")
        if isinstance(peer, (tuple, list)) and peer:
            return str(peer[0])
        if isinstance(peer, str) and peer:
            return peer
    return "unknown"


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = max(0, int(limit))
        self.window_seconds = int(window_seconds)
        self._hits = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str, now: float | None = None):
        if self.limit <= 0:
            return True, None, 0
        if now is None:
            now = time.monotonic()
        async with self._lock:
            hits = self._hits[key]
            cutoff = now - self.window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, math.ceil(self.window_seconds - (now - hits[0])))
                return False, retry_after, len(hits)
            hits.append(now)
            return True, None, len(hits)


def make_request_gate(max_concurrent: int, rate_limit: int, window_seconds: int = 60):
    semaphore = asyncio.Semaphore(max(1, int(max_concurrent)))
    limiter = SlidingWindowRateLimiter(rate_limit, window_seconds)

    @web.middleware
    async def request_gate(request, handler):
        if request.method == "OPTIONS":
            return await handler(request)

        if request.path in {"/chat", "/v1/chat/completions"}:
            ip = _client_ip(request)
            allowed, retry_after, count = await limiter.allow(ip)
            if not allowed:
                log.warning("rate_limited", extra={"method": request.method, "path": request.path,
                                                     "detail": ip, "count": count})
                headers = {"Retry-After": str(retry_after), "Access-Control-Allow-Origin": "*"}
                if request.path == "/v1/chat/completions":
                    return web.json_response(
                        {"error": {"message": "Rate limit exceeded", "type": "rate_limit_exceeded"}},
                        status=429,
                        headers=headers,
                    )
                return web.json_response({"success": False, "error": "Rate limit exceeded"}, status=429, headers=headers)

            async with semaphore:
                hook = globals().get("bridge_test_hook")
                if hook is not None:
                    maybe = hook(request)
                    if asyncio.iscoroutine(maybe):
                        await maybe
                return await handler(request)

        return await handler(request)

    return request_gate, semaphore, limiter


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

    @property
    def conversation_sessions(self):
        sessions = self._data.get("conversation_sessions")
        return sessions if isinstance(sessions, dict) else {}

    def get_conversation(self, session_key):
        sessions = self.conversation_sessions
        key = str(session_key or "default")
        stored = sessions.get(key)
        if isinstance(stored, dict):
            return stored
        if isinstance(stored, str):
            return {"conversation_id": stored, "conversation_title": None}
        return None

    def set_conversation(self, session_key, conversation_id, conversation_title=None, privacy_mode=None):
        key = str(session_key or "default")
        sessions = dict(self.conversation_sessions)
        if conversation_id:
            entry = {"conversation_id": conversation_id, "conversation_title": conversation_title}
            if privacy_mode:
                entry["privacy_mode"] = _normalize_privacy_mode(privacy_mode)
            sessions[key] = entry
        else:
            sessions.pop(key, None)
        self._data["conversation_sessions"] = sessions
        self.save()

    def clear_conversation(self, session_key):
        key = str(session_key or "default")
        sessions = dict(self.conversation_sessions)
        previous = sessions.pop(key, None)
        self._data["conversation_sessions"] = sessions
        self.save()
        return previous

    @property
    def available_models(self):
        return self._data.get("available_models", [])

    @available_models.setter
    def available_models(self, value):
        self._data["available_models"] = value
        self.save()

    @property
    def available_models_fetched_at(self):
        return self._data.get("available_models_fetched_at", 0)

    @available_models_fetched_at.setter
    def available_models_fetched_at(self, value):
        self._data["available_models_fetched_at"] = int(value or 0)
        self.save()


MODEL_CATALOG_TTL_SECONDS = 24 * 60 * 60


def _normalize_model_text(text):
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact_model_text(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").strip().lower())


def _unique_model_labels(labels):
    seen = set()
    out = []
    for label in labels or []:
        label = " ".join(str(label).split()).strip()
        if not label:
            continue
        norm = _normalize_model_text(label)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(label)
    return out


def _model_id_from_label(label):
    norm = _normalize_model_text(label)
    if not norm:
        return "unknown-model"
    return re.sub(r"[^a-z0-9]+", "-", norm).strip("-") or "unknown-model"


def _format_model_catalog(labels, fetched_at=None):
    fetched_at = int(fetched_at or time.time())
    return [{
        "id": _model_id_from_label(label),
        "object": "model",
        "created": fetched_at,
        "owned_by": "openai",
    } for label in _unique_model_labels(labels)]


def _best_model_match(search_term, labels):
    search_norm = _normalize_model_text(search_term)
    search_compact = _compact_model_text(search_term)
    if not search_norm and not search_compact:
        return None

    search_tokens = [tok for tok in search_norm.split(" ") if tok]
    best = None
    best_score = None
    for idx, label in enumerate(_unique_model_labels(labels)):
        label_norm = _normalize_model_text(label)
        label_compact = _compact_model_text(label)
        if not label_norm and not label_compact:
            continue
        score = None
        if label_norm == search_norm or label_compact == search_compact:
            score = (0, len(label_compact) or len(label_norm), idx)
        elif label_norm.startswith(search_norm) or label_compact.startswith(search_compact):
            score = (1, len(label_compact) or len(label_norm), idx)
        elif search_norm in label_norm or search_compact in label_compact:
            score = (2, len(label_compact) or len(label_norm), idx)
        elif search_tokens and all(tok in label_norm for tok in search_tokens):
            score = (3, len(label_compact) or len(label_norm), idx)
        if score is None:
            continue
        if best_score is None or score < best_score:
            best_score = score
            best = label
    return best


def _catalog_is_stale(state):
    fetched_at = int(state.available_models_fetched_at or 0)
    if not fetched_at:
        return True
    return (time.time() - fetched_at) >= MODEL_CATALOG_TTL_SECONDS


def _catalog_payload(state):
    fetched_at = int(state.available_models_fetched_at or 0)
    return {
        "object": "list",
        "data": _format_model_catalog(state.available_models, fetched_at),
    }


def _normalize_privacy_mode(value):
    mode = str(value or DEFAULT_PRIVACY_MODE or "temporary").strip().lower().replace("_", "-")
    if mode in {"temporary", "temp", "temporary-chat", "no-memory", "private", "privacy"}:
        return "temporary"
    if mode in {"standard", "normal", "default", "off", "false", "0", "none"}:
        return "standard"
    return "temporary"


def _resolve_privacy_mode(body):
    if isinstance(body, dict):
        if _is_truthy(body.get("temporary_chat", False)) or _is_truthy(body.get("no_memory", False)):
            return "temporary"
        if "privacy_mode" in body:
            return _normalize_privacy_mode(body.get("privacy_mode"))
    return _normalize_privacy_mode(DEFAULT_PRIVACY_MODE)


def _conversation_session_key(body):
    if not isinstance(body, dict):
        return "default"
    for key in ("session_id", "conversation_key", "hermes_session_id"):
        value = body.get(key)
        if value:
            return str(value)
    return "default"


def _resolve_conversation_state(state, body, *, allow_default_fallback=True):
    session_key = _conversation_session_key(body)
    privacy_mode = _resolve_privacy_mode(body)
    wants_new = _is_truthy(body.get("new", False))
    explicit_id = body.get("conversation_id") or None

    if explicit_id:
        return session_key, explicit_id, wants_new, True
    if wants_new:
        return session_key, None, True, False

    # For API callers that did not provide a session key, do not reuse any
    # default/global pin unless the endpoint explicitly allows it. This prevents
    # a fresh Hermes session from inheriting an unrelated old ChatGPT thread.
    if session_key == "default" and not allow_default_fallback:
        return session_key, None, False, False

    stored = state.get_conversation(session_key)
    if stored and stored.get("conversation_id"):
        stored_privacy = _normalize_privacy_mode(stored.get("privacy_mode") or "standard")
        if privacy_mode == stored_privacy:
            return session_key, stored.get("conversation_id"), False, False

    # A non-default session key is authoritative: if it has no pin yet, start a
    # fresh ChatGPT thread and then persist the returned conversation_id.
    if session_key != "default":
        return session_key, None, False, False

    # OpenAI-compatible providers like Hermes send the whole message transcript
    # on every request. If they do not send a session_id, falling back to a
    # process-global last_conversation_id can leak an unrelated old ChatGPT
    # thread into a new local session. Keep the legacy default fallback only for
    # explicit CLI/default-session callers that opted into it and only for the
    # standard non-temporary mode, because legacy global pins predate privacy
    # metadata and may be regular memory-enabled ChatGPT conversations.
    if not allow_default_fallback or privacy_mode != "standard":
        return session_key, None, False, False

    return session_key, state.last_conversation_id or None, False, False


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
        for k in ("event", "rid", "request_id", "type", "assignee", "method", "status",
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
    root.handlers.clear()
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
bridge_test_hook = None


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
    import websockets
    page_id, page_url = await asyncio.to_thread(_find_chatgpt_tab_cdp)
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
    model_catalog_pending = {}  # rid -> asyncio.Future for model catalog responses
    model_catalog_refresh_lock = asyncio.Lock()
    counter = 0
    ws_lock = asyncio.Lock()
    extension_connected_since = None
    conversation_lock = asyncio.Lock()  # protects resolve-store cycle for session→conversation mapping

    per_assignee_requests = {}  # assignee -> {total, success, timeout, error}
    watchdog_events = []       # last 20 watchdog recovery event dicts
    watchdog_status = None

    state = BridgeState()
    if not state.available_models:
        cached_models = load_available_models()
        if cached_models:
            state.available_models = cached_models
            state.available_models_fetched_at = int(time.time())
            log.info("model_catalog_seeded", extra={"count": len(cached_models)})
    log.info("state_loaded", extra={"queue_len": state.last_conversation_id})

    max_concurrent = _env_int("BRIDGE_MAX_CONCURRENT", 3, min_value=1)
    rate_limit = _env_int("BRIDGE_RATE_LIMIT", 10, min_value=0)
    request_gate, request_semaphore, rate_limiter = make_request_gate(max_concurrent, rate_limit)
    log.info("request_controls_ready", extra={"count": max_concurrent, "detail": rate_limit})

    # ─── WebSocket handler ──────────────────────────────────────────────────

    async def ws_handler(ws):
        nonlocal extension_connected_since, watchdog_status
        connected_extensions.add(ws)
        if extension_connected_since is None:
            extension_connected_since = time.time()
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
                        meta = pending_meta.get(rid, {}) or {}
                        conv_id = msg.get("conversation_id")
                        conv_title = msg.get("conversation_title")
                        if conv_id:
                            meta.update({"conversation_id": conv_id, "conversation_title": conv_title})
                        debug_info = msg.get("_debug")
                        if debug_info:
                            meta["_debug"] = debug_info
                        pending_meta[rid] = meta
                        pending[rid].set_result(msg.get("text", ""))
                        del pending[rid]
                    elif rid in stream_queues:
                        final_text = msg.get("text", "")
                        if final_text:
                            await stream_queues[rid].put({"type": "delta", "content": final_text})
                        await stream_queues[rid].put({"type": "done"})
                        del stream_queues[rid]

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

                elif mtype == "poll":
                    rid = msg.get("id")
                    poll = msg.get("poll", {}) or {}
                    log.info("poll_interval", extra={
                        "rid": rid,
                        "request_id": msg.get("request_id"),
                        "count": poll.get("poll_index"),
                        "elapsed_ms": poll.get("interval_ms"),
                        "detail": f"text_changed={poll.get('text_changed')} generating={poll.get('generating')} assistant_count={poll.get('assistant_count')}",
                    })

                elif mtype == "done":
                    rid = msg.get("id")
                    if rid in stream_queues:
                        # Store conversation_id from the done message so streaming
                        # sessions can pin the ChatGPT thread for subsequent turns.
                        conv_id = msg.get("conversation_id")
                        conv_title = msg.get("conversation_title")
                        log.info("ws_done", extra={"rid": rid, "conv_id": conv_id, "has_meta": rid in pending_meta})
                        if conv_id and rid in pending_meta:
                            pending_meta[rid].update({"conversation_id": conv_id, "conversation_title": conv_title})
                        await stream_queues[rid].put({"type": "done"})
                        del stream_queues[rid]

                elif mtype == "fresh_page":
                    pass  # no-op without CDP

                elif mtype == "watchdog_events":
                    events = msg.get("events", [])
                    if isinstance(events, list) and events:
                        watchdog_events[:] = events[-20:]
                    log.info("watchdog_sync", extra={"count": len(events)})

                elif mtype == "model_catalog":
                    rid = msg.get("id")
                    models = msg.get("models", [])
                    if isinstance(models, list):
                        state.available_models = _unique_model_labels(models)
                        state.available_models_fetched_at = int(msg.get("fetched_at") or time.time())
                    if rid in model_catalog_pending:
                        model_catalog_pending[rid].set_result(list(state.available_models))
                        del model_catalog_pending[rid]

                elif mtype == "model_catalog_error":
                    rid = msg.get("id")
                    err_msg = msg.get("error", "unknown")
                    if rid in model_catalog_pending:
                        model_catalog_pending[rid].set_exception(RuntimeError(err_msg))
                        del model_catalog_pending[rid]

                elif mtype == "watchdog_status":
                    watchdog_status = {
                        "recovery_events": msg.get("recovery_events", 0),
                        "last_recovery": msg.get("last_recovery"),
                        "chrome_alive": bool(msg.get("chrome_alive", False)),
                    }
                    events = msg.get("events", [])
                    if isinstance(events, list) and events:
                        watchdog_events[:] = events[-20:]
                    log.info("watchdog_status_sync", extra={"count": watchdog_status.get("recovery_events", 0)})

        except Exception as e:
            log.error("ws_handler_exception", extra={"error": str(e)}, exc_info=True)
        finally:
            connected_extensions.discard(ws)
            if not connected_extensions:
                extension_connected_since = None
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
            send_started = time.time()
            for ext in list(connected_extensions):
                try:
                    await ext.send(json.dumps(msg_data))
                    sent = True
                except Exception as e:
                    log.warning("ws_send_failed", extra={"error": str(e)})
            if not sent:
                raise RuntimeError("Failed to send to any extension")
            return round((time.time() - send_started) * 1000)

    async def _send_and_wait(msg_data, timeout_s, rid, *, prompt_chars=None, req_type=None):
        ws_send_ms = await _send_to_extension(msg_data)
        log.info("ws_send",
                 extra={"rid": rid,
                        "type": req_type,
                        "elapsed_ms": ws_send_ms,
                        "status": "ok"})
        ts = time.time()
        result = await asyncio.wait_for(pending[rid], timeout=timeout_s)
        elapsed = (time.time() - ts) * 1000
        log.info("response_received",
                 extra={"rid": rid,
                        "type": req_type,
                        "prompt_chars": prompt_chars,
                        "response_chars": len(result),
                        "elapsed_ms": round(elapsed),
                        "status": "ok"})
        return result, ws_send_ms

    def _build_debug(result, ws_send_ms, request_started_at):
        meta = result.get("_debug") if isinstance(result, dict) else None
        meta = meta or {}
        return {
            "elapsed_ms": meta.get("elapsed_ms", round((time.time() - request_started_at) * 1000)),
            "ws_send_ms": ws_send_ms,
            "poll_count": meta.get("poll_count"),
            "poll_intervals_ms": meta.get("poll_intervals_ms"),
        }

    async def _ensure_model_catalog(timeout_s=15, force_refresh=False):
        if state.available_models and not force_refresh and not _catalog_is_stale(state):
            return list(state.available_models)
        if state.available_models and not force_refresh and not connected_extensions:
            return list(state.available_models)
        if not connected_extensions:
            raise RuntimeError("No ChatGPT tab connected")
        async with model_catalog_refresh_lock:
            if state.available_models and not force_refresh and not _catalog_is_stale(state):
                return list(state.available_models)
            rid = f"models-{int(time.time() * 1000)}-{len(model_catalog_pending) + 1}"
            fut = asyncio.get_event_loop().create_future()
            model_catalog_pending[rid] = fut
            try:
                await _send_to_extension({"type": "list_models", "id": rid, "timeout": timeout_s})
                models = await asyncio.wait_for(fut, timeout=timeout_s)
                return list(models)
            finally:
                model_catalog_pending.pop(rid, None)

    def _resolve_model_search_or_404(model_search):
        models = list(state.available_models or [])
        if not model_search:
            return None, models
        return _best_model_match(model_search, models), models

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
        nonlocal counter

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
        if model_search:
            try:
                await _ensure_model_catalog(timeout_s=15)
            except Exception as e:
                failed_requests += 1; _incr_assignee(assignee, "error")
                return web.json_response({"error": {"message": str(e), "type": "server_error"}}, status=503)
            resolved_model, available_models = _resolve_model_search_or_404(model_search)
            if resolved_model is None:
                # Model not found in catalog — clear model_search so the request
                # still proceeds with whatever model ChatGPT has selected.
                # Returning 404 here causes Hermes to retry with different model
                # names, creating duplicate requests that flood the bridge.
                log.warning("model_not_found_falling_back", extra={"model_search": model_search, "count": len(available_models)})
                model_search = None
            else:
                model_search = resolved_model
        stream = body.get("stream", False)
        debug = _is_truthy(request.rel_url.query.get("debug") or body.get("debug", False))
        privacy_mode = _resolve_privacy_mode(body)
        request_id = request.get("request_id")
        request_started_at = time.time()
        session_key, conversation_id, new_conversation, explicit_conversation = _resolve_conversation_state(
            state,
            body,
            allow_default_fallback=False,
        )
        if new_conversation and not explicit_conversation:
            state.clear_conversation(session_key)
        timeout_s = body.get("timeout", 10)
        if timeout_s > 600:
            timeout_s = 600

        user_msgs = [m for m in messages if m.get("role") == "user"]
        if not user_msgs:
            return web.json_response({"error": {"message": "No user message found", "type": "invalid_request_error"}}, status=400)

        cdp_file_paths = []

        for _u in body.get("files", []):
            fp = _u if isinstance(_u, str) else _u.get("path", _u.get("url", ""))
            try:
                cdp_file_paths.append(validate_local_file_path(fp))
            except ValueError as e:
                return web.json_response({"error": {"message": str(e), "type": "invalid_request_error"}}, status=400)

        prompt_parts = []; cdp_file_paths_from_images = []
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
                        if url.startswith("file://") or url.startswith("/") or (len(url) > 1 and url[1] == ":"):
                            try:
                                cdp_file_paths_from_images.append(validate_local_file_path(url))
                            except ValueError as e:
                                return web.json_response({"error": {"message": str(e), "type": "invalid_request_error"}}, status=400)
                content = " ".join(text_parts)
            prompt_parts.append(f"{role}: {content}")

        for f in body.get("files", []):
            fp = f if isinstance(f, str) else f.get("path", f.get("url", ""))
            try:
                cdp_file_paths_from_images.append(validate_local_file_path(fp))
            except ValueError as e:
                return web.json_response({"error": {"message": str(e), "type": "invalid_request_error"}}, status=400)

        cdp_file_paths = cdp_file_paths_from_images

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
                 extra={"rid": rid, "request_id": request_id, "assignee": assignee,
                        "type": "/v1/chat/completions",
                        "prompt_chars": prompt_chars,
                        "response_chars": None,
                        "elapsed_ms": None,
                        "status": "started",
                        "code": "v1/chat/completions"})

        # Use the resolved new_conversation flag from _resolve_conversation_state
        # instead of recalculating from conversation_id. This prevents a race where
        # concurrent requests for the same new session all see conversation_id=None
        # and each start a fresh ChatGPT conversation.
        msg_data = {
            "type": "prompt",
            "id": rid, "prompt": full_prompt, "options": {},
            "files": [], "timeout": timeout_s,
            "conversation_id": conversation_id, "model_search": model_search,
            "new_conversation": new_conversation,
            "privacy_mode": privacy_mode,
            "temporary_chat_url": TEMPORARY_CHAT_URL,
            "debug": debug,
        }

        # Serialize prompt delivery through conversation_lock so that concurrent
        # requests for the same session don't each create a new ChatGPT thread.
        # The lock spans resolve → send → store so the first request's
        # conversation_id is stored before the next request resolves.
        async with conversation_lock:

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
                        stream_queues.pop(rid, None)
                        # Note: pending_meta is NOT popped here — the outer code
                        # needs to read conversation_id from it after streaming completes.
                try:
                    await _send_to_extension(msg_data)
                except Exception as e:
                    stream_queues.pop(rid, None)
                    log.error("stream_send_error", extra={"rid": rid, "error": str(e)})
                    failed_requests += 1; _incr_assignee(assignee, "error")
                    return web.json_response({"error": {"message": str(e), "type": "server_error"}}, status=500)
                response = web.StreamResponse(
                    status=200,
                    headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                             "Content-Type": "text/event-stream", "X-Accel-Buffering": "no",
                             "Access-Control-Allow-Origin": "*"})
                response.headers["X-Request-Id"] = request_id
                await response.prepare(request)
                async for chunk in sse_generator():
                    await response.write(chunk.encode())
                await response.write_eof()
                _stream_elapsed = round((time.time() - created_at) * 1000)
                log.info("response_received",
                         extra={"rid": rid,
                                "type": "/v1/chat/completions",
                                "prompt_chars": prompt_chars,
                                "response_chars": 0,
                                "elapsed_ms": _stream_elapsed,
                                "status": "ok"})
                # Store conversation_id from the streaming response
                # (populated by the "done" WS message from the extension)
                meta = pending_meta.pop(rid, {}) or {}
                conv_id = meta.get("conversation_id")
                conv_title = meta.get("conversation_title")
                session_key_meta = meta.get("session_key") or session_key
                privacy_mode_meta = meta.get("privacy_mode") or privacy_mode
                log.info("stream_store", extra={"rid": rid, "conv_id": conv_id, "meta_keys": list(meta.keys())})
                if conv_id:
                    state.set_conversation(session_key_meta, conv_id, conv_title, privacy_mode=privacy_mode_meta)
                    if privacy_mode_meta == "standard":
                        state.last_conversation_id = conv_id
                        state.last_conversation_title = conv_title
                successful_requests += 1; last_response_time = time.strftime("%H:%M:%S")
                _incr_assignee(assignee, "success")
                return response

            # non-streaming path
            pending[rid] = future = asyncio.get_event_loop().create_future()
            pending_meta[rid] = {"session_key": session_key, "conversation_id": conversation_id, "conversation_title": None, "privacy_mode": privacy_mode}
            try:
                result, ws_send_ms = await _send_and_wait(msg_data, timeout_s, rid, prompt_chars=prompt_chars, req_type="/v1/chat/completions")
                pending.pop(rid, None)
                meta = pending_meta.pop(rid, {})
                conv_id = meta.get("conversation_id"); conv_title = meta.get("conversation_title")
                session_key_meta = meta.get("session_key") or session_key
                privacy_mode_meta = meta.get("privacy_mode") or privacy_mode
                if conv_id:
                    state.set_conversation(session_key_meta, conv_id, conv_title, privacy_mode=privacy_mode_meta)
                    if privacy_mode_meta == "standard":
                        state.last_conversation_id = conv_id
                        state.last_conversation_title = conv_title
                successful_requests += 1; last_response_time = time.strftime("%H:%M:%S")
                _incr_assignee(assignee, "success")
                response_chars = len(result)
                completion_id = f"chatcmpl-{int(time.time())}"
                assistant_text = result
                resp = {"id": completion_id, "object": "chat.completion",
                        "created": int(time.time()), "model": model,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": assistant_text},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": prompt_chars // 4,
                                  "completion_tokens": response_chars // 4,
                                  "total_tokens": (prompt_chars + response_chars) // 4}}
                if conv_id: resp["conversation_id"] = conv_id
                if conv_title: resp["conversation_title"] = conv_title
                if debug:
                    resp["_debug"] = _build_debug({"_debug": meta.get("_debug")}, ws_send_ms, request_started_at)
                    log.info("poll_summary", extra={"rid": rid, "request_id": request_id, "count": resp["_debug"].get("poll_count"), "detail": f"intervals={resp['_debug'].get('poll_intervals_ms')}"})
                log.info("response_end", extra={"rid": rid, "request_id": request_id, "type": "/v1/chat/completions", "status": "ok"})
                return web.json_response(resp)
            except asyncio.TimeoutError:
                pending.pop(rid, None); pending_meta.pop(rid, None)
                timed_out_requests += 1; _incr_assignee(assignee, "timeout")
                log.warning("request_timeout", extra={"rid": rid, "request_id": request_id, "status": "timeout",
                                                      "elapsed_ms": timeout_s * 1000})
                log.info("response_end", extra={"rid": rid, "request_id": request_id, "type": "/v1/chat/completions", "status": "timeout"})
                return web.json_response({"error": {"message": f"Timed out ({timeout_s}s)", "type": "timeout"}}, status=504)
            except Exception as e:
                pending.pop(rid, None); pending_meta.pop(rid, None)
                failed_requests += 1; _incr_assignee(assignee, "error")
                log.error("request_error", extra={"rid": rid, "request_id": request_id, "status": "error", "error": str(e)})
                log.info("response_end", extra={"rid": rid, "request_id": request_id, "type": "/v1/chat/completions", "status": "error"})
                return web.json_response({"error": {"message": str(e), "type": "server_error"}}, status=500)

    # ─── /chat handler ──────────────────────────────────────────────────────

    async def chat(request):
        global total_requests, successful_requests, timed_out_requests, failed_requests, last_response_time
        nonlocal counter
        log.debug("http_request", extra={"method": "POST", "path": "/chat"})
        try:
            raw_body = await request.read()
            body = json.loads(raw_body)
        except Exception as e:
            return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)
        prompt = body.get("prompt", "").strip()
        options = body.get("options", {}); files = body.get("files", [])
        messages = body.get("messages", [])
        if isinstance(messages, list) and messages:
            prompt_parts = []
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
                                url = url[7:]
                            if url.startswith("/") or (len(url) > 1 and url[1] == ":"):
                                files.append(url)
                    content = " ".join(text_parts)
                prompt_parts.append(f"{role}: {content}")
            prompt = "\n".join(prompt_parts).strip()
        assignee = body.get("assignee") or ""
        session_key, conversation_id, new_conversation, explicit_conversation = _resolve_conversation_state(state, body)
        model_search = body.get("model_search") or None
        if model_search:
            try:
                await _ensure_model_catalog(timeout_s=15)
            except Exception as e:
                failed_requests += 1; _incr_assignee(assignee, "error")
                return web.json_response({"error": f"{e}"}, status=503)
            resolved_model, available_models = _resolve_model_search_or_404(model_search)
            if resolved_model is None:
                return web.json_response({
                    "error": {
                        "message": f"Model not found: {model_search}",
                        "type": "model_not_found",
                        "available_models": _format_model_catalog(available_models, state.available_models_fetched_at),
                    }
                }, status=404)
            model_search = resolved_model
        debug = _is_truthy(request.rel_url.query.get("debug") or body.get("debug", False))
        privacy_mode = _resolve_privacy_mode(body)
        request_id = request.get("request_id")
        request_started_at = time.time()
        if new_conversation and not explicit_conversation:
            state.clear_conversation(session_key)
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
                try:
                    file_paths.append(validate_local_file_path(fp))
                except ValueError as e:
                    return web.json_response({"success": False, "error": str(e)}, status=400)
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
                 extra={"rid": rid, "request_id": request_id, "assignee": assignee,
                        "type": "/chat",
                        "prompt_chars": len(prompt),
                        "response_chars": None,
                        "elapsed_ms": None,
                        "status": "started",
                        "code": "/chat"})
        pending[rid] = future = asyncio.get_event_loop().create_future()
        pending_meta[rid] = {"session_key": session_key, "conversation_id": conversation_id, "conversation_title": None, "privacy_mode": privacy_mode}
        # Use the resolved new_conversation flag from _resolve_conversation_state
        msg_data = {"type": "prompt", "id": rid, "prompt": prompt,
                    "options": options, "files": [], "timeout": timeout_s,
                    "conversation_id": conversation_id, "model_search": model_search,
                    "new_conversation": new_conversation,
                    "privacy_mode": privacy_mode,
                    "temporary_chat_url": TEMPORARY_CHAT_URL,
                    "debug": debug}
        try:
            result, ws_send_ms = await _send_and_wait(msg_data, timeout_s, rid, prompt_chars=len(prompt), req_type="/chat")
            pending.pop(rid, None)
            meta = pending_meta.pop(rid, {})
            conv_id = meta.get("conversation_id"); conv_title = meta.get("conversation_title")
            session_key_meta = meta.get("session_key") or session_key
            privacy_mode_meta = meta.get("privacy_mode") or privacy_mode
            if conv_id:
                state.set_conversation(session_key_meta, conv_id, conv_title, privacy_mode=privacy_mode_meta)
                if privacy_mode_meta == "standard":
                    state.last_conversation_id = conv_id
                    state.last_conversation_title = conv_title
            successful_requests += 1; last_response_time = time.strftime("%H:%M:%S")
            _incr_assignee(assignee, "success")
            response_chars = len(result)
            assistant_text = result
            try:
                parsed = json.loads(assistant_text)
                if isinstance(parsed, dict) and "type" in parsed:
                    resp = {"success": True, **parsed}
                    if conv_id: resp["conversation_id"] = conv_id
                    if conv_title: resp["conversation_title"] = conv_title
                    if debug:
                        resp["_debug"] = _build_debug({"_debug": meta.get("_debug")}, ws_send_ms, request_started_at)
                    log.info("poll_summary", extra={"rid": rid, "request_id": request_id, "count": resp.get("_debug", {}).get("poll_count") if debug else None, "detail": f"intervals={(resp.get('_debug', {}) or {}).get('poll_intervals_ms')}"})
                    log.info("response_end", extra={"rid": rid, "request_id": request_id, "type": "/chat", "status": "ok"})
                    return web.json_response(resp)
            except (json.JSONDecodeError, TypeError):
                pass
            resp = {"success": True, "type": "text", "text": assistant_text,
                     **({"conversation_id": conv_id} if conv_id else {}),
                     **({"conversation_title": conv_title} if conv_title else {})}
            if debug:
                resp["_debug"] = _build_debug({"_debug": meta.get("_debug")}, ws_send_ms, request_started_at)
                log.info("poll_summary", extra={"rid": rid, "request_id": request_id, "count": resp["_debug"].get("poll_count"), "detail": f"intervals={resp['_debug'].get('poll_intervals_ms')}"})
            log.info("response_end", extra={"rid": rid, "request_id": request_id, "type": "/chat", "status": "ok"})
            return web.json_response(resp)
        except asyncio.TimeoutError:
            pending.pop(rid, None); pending_meta.pop(rid, None)
            timed_out_requests += 1; _incr_assignee(assignee, "timeout")
            log.warning("request_timeout", extra={"rid": rid, "request_id": request_id, "status": "timeout",
                                                  "elapsed_ms": timeout_s * 1000})
            log.info("response_end", extra={"rid": rid, "request_id": request_id, "type": "/chat", "status": "timeout"})
            return web.json_response({"success": False, "error": f"Timed out ({timeout_s}s)"})
        except Exception as e:
            pending.pop(rid, None); pending_meta.pop(rid, None)
            failed_requests += 1; _incr_assignee(assignee, "error")
            log.error("request_error", extra={"rid": rid, "request_id": request_id, "status": "error", "error": str(e)})
            log.info("response_end", extra={"rid": rid, "request_id": request_id, "type": "/chat", "status": "error"})
            return web.json_response({"success": False, "error": str(e)})

    async def models(request):
        try:
            await _ensure_model_catalog(timeout_s=15)
        except Exception:
            if not state.available_models:
                return web.json_response({"error": {"message": "No model catalog available", "type": "server_error"}}, status=503)
        return web.json_response(_catalog_payload(state))

    # ─── /health handler ────────────────────────────────────────────────────

    async def health(request):
        uptime_s = int(time.time() - start_time)
        extension_uptime_s = int(time.time() - extension_connected_since) if extension_connected_since else 0
        cdp_page_id, cdp_url = _find_chatgpt_tab_cdp()
        cdp_status = {"available": cdp_page_id is not None, "page_id": cdp_page_id, "url": cdp_url}
        watchdog_summary = watchdog_status or summarize_watchdog_events(watchdog_events)
        payload = {
            "status": "ok" if cdp_page_id else "degraded",
            "extensions": len(connected_extensions),
            "cdp": cdp_status,
            "uptime": f"{uptime_s // 3600}h{(uptime_s % 3600) // 60}m{uptime_s % 60}s",
            "uptime_breakdown": {
                "bridge": format_uptime(uptime_s),
                "extension_connected": format_uptime(extension_uptime_s),
            },
            "uptime_seconds": uptime_s,
            "requests": {"total": total_requests, "success": successful_requests,
                         "timeout": timed_out_requests, "failed": failed_requests},
            "pending": len(pending),
            "stream_queues": len(stream_queues),
            "last_response": last_response_time,
            "last_conversation_id": state.last_conversation_id,
            "last_conversation_title": state.last_conversation_title,
            "privacy_mode": _normalize_privacy_mode(DEFAULT_PRIVACY_MODE),
            "temporary_chat_url": TEMPORARY_CHAT_URL if _normalize_privacy_mode(DEFAULT_PRIVACY_MODE) == "temporary" else None,
            "watchdog": {
                "recovery_events": watchdog_summary.get("recovery_events", 0),
                "last_recovery": watchdog_summary.get("last_recovery"),
                "chrome_alive": bool(cdp_page_id is not None),
            },
            "watchdog_events": watchdog_summary.get("events", watchdog_events[-5:]),
            "watchdog_recovery": watchdog_summary.get("events", watchdog_events[-5:]),
            "models": {
                "available": _format_model_catalog(state.available_models, state.available_models_fetched_at),
                "fetched_at": int(state.available_models_fetched_at or 0),
                "stale": _catalog_is_stale(state),
            },
            "bridge_limits": {
                "max_concurrent": max_concurrent,
                "rate_limit_per_minute": rate_limit,
            },
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

    async def new_conversation(request):
        """Explicitly reset the conversation pin — the next prompt starts a fresh ChatGPT thread."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        session_key = _conversation_session_key(body)
        privacy_mode = _resolve_privacy_mode(body)
        previous = state.clear_conversation(session_key)
        if session_key == "default":
            state.last_conversation_id = None
            state.last_conversation_title = None
        previous_id = previous.get("conversation_id") if isinstance(previous, dict) else previous
        if connected_extensions:
            msg = {"type": "new_chat", "privacy_mode": privacy_mode, "temporary_chat_url": TEMPORARY_CHAT_URL}
            async with ws_lock:
                for ext in list(connected_extensions):
                    try:
                        await ext.send(json.dumps(msg))
                    except Exception as e:
                        log.warning("new_chat_send_error", extra={"error": str(e)})
        log.info("conversation_reset", extra={"rid": request.get("request_id"), "detail": previous_id})
        return web.json_response({
            "success": True,
            "message": "Conversation pin reset — next prompt starts a fresh ChatGPT conversation",
            "previous_conversation_id": previous_id,
            "session_id": session_key,
            "privacy_mode": privacy_mode,
        })

    async def cors(request):
        return web.Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        })

    # ─── Wire up ────────────────────────────────────────────────────────────

    @web.middleware
    async def error_mw(request, handler):
        request_id = uuid.uuid4().hex
        request["request_id"] = request_id
        try:
            response = await handler(request)
            try:
                response.headers["X-Request-Id"] = request_id
            except Exception:
                pass
            return response
        except Exception as e:
            log.error("unhandled_error", extra={"request_id": request_id, "error": str(e)}, exc_info=True)
            traceback.print_exc()
            return web.json_response({"error": str(e)}, status=500, headers={"X-Request-Id": request_id})

    app = web.Application(middlewares=[error_mw, request_gate])
    app.router.add_get("/health", health)
    app.router.add_get("/v1/models", models)
    app.router.add_get("/v1/cdp/status", cdp_status)
    app.router.add_post("/reload", reload_ext)
    app.router.add_post("/new", new_conversation)
    app.router.add_post("/chat", chat)
    app.router.add_post("/v1/chat/completions", chat_completions)
    app.router.add_options("/v1/chat/completions", cors)
    app.router.add_options("/chat", cors)
    app.router.add_options("/v1/models", cors)
    app.router.add_options("/v1/cdp/status", cors)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", HTTP_PORT)
    await site.start()

    ws_server = await websockets.serve(ws_handler, "127.0.0.1", WS_PORT)

    log.info("http_listening", extra={"queue_len": HTTP_PORT})
    log.info("ws_listening", extra={"qc": WS_PORT})
    log.info("bridge_ready")

    stop = asyncio.Event()
    try:
        await stop.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        ws_server.close()
        await ws_server.wait_closed()
        await runner.cleanup()


if __name__ == "__main__":
    # Recursively clean __pycache__ before any Python bytecode compilation
    import shutil
    _script_root = Path(__file__).parent.resolve()
    for _p in _script_root.rglob("__pycache__"):
        if _p.is_dir():
            shutil.rmtree(_p, ignore_errors=True)

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
