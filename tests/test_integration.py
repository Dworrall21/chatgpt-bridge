#!/usr/bin/env python3
"""Integration test suite for the ChatGPT Bridge extension.

This script exercises the live bridge + Chrome + extension pipeline. It exits
with code 0 only when every check passes.

Run:
  python3 tests/test_integration.py
"""

from __future__ import annotations

import http.client
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

BRIDGE_URL = "http://127.0.0.1:11557"
CDP_URL = "http://127.0.0.1:9222"
HTTP_TIMEOUT = 60
LONG_TIMEOUT = 300
STREAM_OVERALL_TIMEOUT = 180
STREAM_READ_TIMEOUT = 5
SHORT_PROMPT = "Say hello"


@dataclass
class HttpResult:
    status: int
    body: Any
    raw: bytes
    content_type: str
    headers: dict[str, str]
    elapsed: float


results: list[tuple[str, bool, str]] = []
passed = 0
failed = 0


def record(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    results.append((name, ok, detail))
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}: {detail}")


def fail(msg: str) -> None:
    print(f"\nFATAL: {msg}", flush=True)
    sys.exit(1)


def _decode_body(raw: bytes, content_type: str) -> Any:
    if not raw:
        return ""
    if content_type == "application/json" or content_type.endswith("+json"):
        try:
            return json.loads(raw)
        except Exception:
            return raw.decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


def request_json(
    method: str,
    path: str,
    body: Any | None = None,
    *,
    accept: str = "application/json",
    timeout: int = HTTP_TIMEOUT,
) -> HttpResult:
    url = BRIDGE_URL + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", accept)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            ctype = resp.headers.get_content_type()
            return HttpResult(
                status=resp.status,
                body=_decode_body(raw, ctype),
                raw=raw,
                content_type=ctype,
                headers=headers,
                elapsed=time.monotonic() - start,
            )
    except urllib.error.HTTPError as e:
        raw = e.read()
        headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        ctype = e.headers.get_content_type() if getattr(e, "headers", None) else "application/octet-stream"
        return HttpResult(
            status=e.code,
            body=_decode_body(raw, ctype),
            raw=raw,
            content_type=ctype,
            headers=headers,
            elapsed=time.monotonic() - start,
        )
    except Exception as e:
        return HttpResult(
            status=-1,
            body={"error": str(e)},
            raw=b"",
            content_type="error",
            headers={},
            elapsed=time.monotonic() - start,
        )


def request_json_retry(
    method: str,
    path: str,
    body: Any | None = None,
    *,
    accept: str = "application/json",
    timeout: int = HTTP_TIMEOUT,
    attempts: int = 3,
    retry_statuses: Iterable[int] = (-1, 429, 500, 502, 503, 504),
) -> HttpResult:
    retry_statuses = set(retry_statuses)
    last: HttpResult | None = None
    for attempt in range(1, attempts + 1):
        last = request_json(method, path, body, accept=accept, timeout=timeout)
        if last.status not in retry_statuses:
            return last
        if attempt < attempts:
            time.sleep(min(4 * attempt, 10))
    assert last is not None
    return last


def request_sse(
    path: str,
    body: Any,
    *,
    overall_timeout: int = STREAM_OVERALL_TIMEOUT,
    read_timeout: int = STREAM_READ_TIMEOUT,
) -> HttpResult:
    payload = json.dumps(body).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", 11557, timeout=read_timeout)
    start = time.monotonic()
    conn.putrequest("POST", path)
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Accept", "text/event-stream")
    conn.putheader("Content-Length", str(len(payload)))
    conn.endheaders()
    conn.send(payload)
    resp = conn.getresponse()
    headers = {k.lower(): v for k, v in resp.getheaders()}
    frames: list[str] = []
    buffer = ""
    deadline = start + overall_timeout
    while True:
        if time.monotonic() >= deadline:
            break
        try:
            chunk = resp.read(4096)
        except (socket.timeout, TimeoutError):
            continue
        if not chunk:
            break
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            frame = frame.strip()
            if not frame:
                continue
            frames.append(frame)
            if frame.strip() == "data: [DONE]":
                elapsed = time.monotonic() - start
                return HttpResult(
                    status=resp.status,
                    body={"frames": frames, "done": True},
                    raw="\n\n".join(frames).encode("utf-8"),
                    content_type=headers.get("content-type", "text/event-stream"),
                    headers=headers,
                    elapsed=elapsed,
                )
    elapsed = time.monotonic() - start
    return HttpResult(
        status=resp.status,
        body={"frames": frames, "done": False, "error": "stream ended before [DONE]"},
        raw="\n\n".join(frames).encode("utf-8"),
        content_type=headers.get("content-type", "text/event-stream"),
        headers=headers,
        elapsed=elapsed,
    )


def parse_health() -> dict[str, Any]:
    res = request_json("GET", "/health", timeout=10)
    if res.status != 200 or not isinstance(res.body, dict):
        fail(f"Bridge /health unavailable: status={res.status}, body={res.body!r}")
    return res.body


def assert_contains(haystack: str, needle: str) -> bool:
    return needle.lower() in haystack.lower()


def summarize_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True)
    except Exception:
        return repr(obj)


def main() -> int:
    print("=== Prerequisites ===")

    try:
        cdp = urllib.request.urlopen(f"{CDP_URL}/json", timeout=5)
        tabs = json.loads(cdp.read())
        chatgpt_tabs = [t for t in tabs if "chatgpt.com" in t.get("url", "")]
        if not chatgpt_tabs:
            fail("Chrome CDP is reachable, but no chatgpt.com tab is open.")
        print("  OK   Chrome CDP on 9222 + chatgpt.com tab open")
    except Exception as exc:
        fail(f"Chrome CDP not reachable on 9222: {exc}")

    health = parse_health()
    extensions = health.get("extensions", 0)
    if extensions < 1:
        fail(f"Bridge is healthy but extensions={extensions}; load the extension and reopen ChatGPT.")
    print(f"  OK   Bridge on 11557, extensions={extensions}")

    print("\n=== Test 1: GET /health → status ok, extensions=1 ===")
    res1 = request_json("GET", "/health", timeout=10)
    ok1 = (
        res1.status == 200
        and isinstance(res1.body, dict)
        and res1.body.get("status") == "ok"
        and res1.body.get("extensions") == 1
        and isinstance(res1.body.get("requests"), dict)
    )
    record(
        "health status+extensions",
        ok1,
        f"status={res1.body.get('status')!r}, extensions={res1.body.get('extensions')!r}, metrics={summarize_json(res1.body.get('requests'))}",
    )
    if not ok1:
        return 1
    baseline_requests = dict(res1.body["requests"])
    baseline_total = int(baseline_requests.get("total", 0))
    baseline_success = int(baseline_requests.get("success", 0))

    print("\n=== Test 2: POST /chat simple prompt → success in <15 s ===")
    res2 = request_json_retry(
        "POST",
        "/chat",
        body={"prompt": SHORT_PROMPT, "timeout": LONG_TIMEOUT},
        timeout=LONG_TIMEOUT + 30,
    )
    text2 = res2.body.get("response") if isinstance(res2.body, dict) else None
    if text2 is None and isinstance(res2.body, dict):
        text2 = res2.body.get("text")
    ok2 = bool(res2.status == 200 and isinstance(res2.body, dict) and res2.body.get("success") is True and isinstance(text2, str) and text2.strip() and res2.elapsed < 15)
    record(
        "chat simple prompt",
        ok2,
        f"status={res2.status}, elapsed={res2.elapsed:.2f}s, text={text2!r}",
    )
    if not ok2:
        return 1

    print("\n=== Test 3: POST /v1/chat/completions → OpenAI-format response ===")
    res3 = request_json_retry(
        "POST",
        "/v1/chat/completions",
        body={
            "model": "chatgpt",
            "messages": [{"role": "user", "content": SHORT_PROMPT}],
            "timeout": LONG_TIMEOUT,
        },
        timeout=LONG_TIMEOUT + 30,
    )
    body3 = res3.body if isinstance(res3.body, dict) else {}
    choices3 = body3.get("choices") if isinstance(body3, dict) else None
    msg3 = choices3[0].get("message", {}) if isinstance(choices3, list) and choices3 else {}
    text3 = msg3.get("content", "") if isinstance(msg3, dict) else ""
    ok3 = bool(
        res3.status == 200
        and isinstance(body3, dict)
        and body3.get("object") == "chat.completion"
        and isinstance(choices3, list)
        and len(choices3) == 1
        and choices3[0].get("finish_reason") == "stop"
        and isinstance(text3, str)
        and text3.strip()
        and isinstance(body3.get("conversation_id"), str)
    )
    record(
        "openai format",
        ok3,
        f"status={res3.status}, object={body3.get('object')!r}, text={text3!r}, conv_id={body3.get('conversation_id')!r}",
    )
    if not ok3:
        return 1
    conv_id = body3["conversation_id"]

    print("\n=== Test 4: Conversation continuation ===")
    followup_prompt = (
        "In your previous assistant reply, you answered exactly: "
        f"{text3!r}. Reply with that exact text and nothing else."
    )
    res4 = request_json_retry(
        "POST",
        "/v1/chat/completions",
        body={
            "model": "chatgpt",
            "messages": [{"role": "user", "content": followup_prompt}],
            "conversation_id": conv_id,
            "timeout": LONG_TIMEOUT,
        },
        timeout=LONG_TIMEOUT + 30,
    )
    body4 = res4.body if isinstance(res4.body, dict) else {}
    choices4 = body4.get("choices") if isinstance(body4, dict) else None
    msg4 = choices4[0].get("message", {}) if isinstance(choices4, list) and choices4 else {}
    text4 = msg4.get("content", "") if isinstance(msg4, dict) else ""
    ok4 = bool(
        res4.status == 200
        and isinstance(body4, dict)
        and body4.get("object") == "chat.completion"
        and isinstance(choices4, list)
        and len(choices4) == 1
        and isinstance(text4, str)
        and text4.strip()
        and text3.strip() in text4
        and body4.get("conversation_id") == conv_id
    )
    record(
        "conversation continuation",
        ok4,
        f"status={res4.status}, conv_id={body4.get('conversation_id')!r}, expected_text={text3!r}, got={text4!r}",
    )
    if not ok4:
        return 1

    print("\n=== Test 5: Model selection (model_search parameter) ===")
    model_prompt = "Write one short, vivid sentence about a bridge crossing a stormy river."
    plain = request_json_retry(
        "POST",
        "/chat",
        body={"prompt": model_prompt, "timeout": LONG_TIMEOUT},
        timeout=LONG_TIMEOUT + 30,
    )
    search = request_json_retry(
        "POST",
        "/chat",
        body={"prompt": model_prompt, "model_search": "thinking", "timeout": LONG_TIMEOUT},
        timeout=LONG_TIMEOUT + 30,
    )
    plain_text = plain.body.get("text") if isinstance(plain.body, dict) else ""
    search_text = search.body.get("text") if isinstance(search.body, dict) else ""
    ok5 = bool(
        plain.status == 200
        and search.status == 200
        and isinstance(plain_text, str)
        and isinstance(search_text, str)
        and plain_text.strip()
        and search_text.strip()
        and search.body.get("success") is True
        and plain.body.get("success") is True
        and (plain_text != search_text or plain.body.get("conversation_id") != search.body.get("conversation_id"))
    )
    record(
        "model_search accepted",
        ok5,
        f"plain={plain_text!r}, model_search={search_text!r}, plain_conv={plain.body.get('conversation_id')!r}, search_conv={search.body.get('conversation_id')!r}",
    )
    if not ok5:
        return 1

    v1_model_search = request_json_retry(
        "POST",
        "/v1/chat/completions",
        body={
            "model": "chatgpt",
            "messages": [{"role": "user", "content": model_prompt}],
            "model_search": "thinking",
            "timeout": LONG_TIMEOUT,
        },
        timeout=LONG_TIMEOUT + 30,
    )
    body5b = v1_model_search.body if isinstance(v1_model_search.body, dict) else {}
    ok5b = bool(v1_model_search.status == 200 and body5b.get("object") == "chat.completion")
    record(
        "model_search accepted (v1)",
        ok5b,
        f"status={v1_model_search.status}, object={body5b.get('object')!r}, model={body5b.get('model')!r}",
    )
    if not ok5b:
        return 1

    print("\n=== Test 6: Streaming (SSE) ===")
    stream_body = {
        "model": "chatgpt",
        "messages": [{"role": "user", "content": "Say hello"}],
        "stream": True,
        "timeout": LONG_TIMEOUT,
    }
    res6 = request_sse("/v1/chat/completions", stream_body)
    frames = res6.body.get("frames", []) if isinstance(res6.body, dict) else []
    done = bool(res6.body.get("done")) if isinstance(res6.body, dict) else False
    saw_done = any(frame.strip() == "data: [DONE]" for frame in frames)
    saw_chunk = any("chat.completion.chunk" in frame for frame in frames)
    ok6 = bool(res6.status == 200 and saw_chunk and (done or saw_done))
    record(
        "streaming SSE",
        ok6,
        f"status={res6.status}, frames={len(frames)}, saw_chunk={saw_chunk}, done={done}, elapsed={res6.elapsed:.2f}s, error={res6.body.get('error') if isinstance(res6.body, dict) else None!r}",
    )
    if not ok6:
        return 1

    print("\n=== Test 7: Error handling ===")
    bad_method = request_json("GET", "/chat", timeout=10)
    bad_json = request_json(
        "POST",
        "/chat",
        body={"timeout": 10},
        timeout=10,
    )
    missing_prompt = request_json(
        "POST",
        "/chat",
        body={"prompt": "", "timeout": 10},
        timeout=10,
    )
    ok7 = bool(
        bad_method.status != 200
        and bad_json.status in {400, 503}
        and missing_prompt.status in {400, 503}
    )
    record(
        "error handling",
        ok7,
        f"GET /chat={bad_method.status}, missing prompt body={bad_json.status}/{bad_json.body!r}, empty prompt={missing_prompt.status}/{missing_prompt.body!r}",
    )
    if not ok7:
        return 1

    print("\n=== Test 8: Metrics accumulate ===")
    health_after = parse_health()
    requests_after = health_after.get("requests", {}) if isinstance(health_after, dict) else {}
    total_after = int(requests_after.get("total", 0))
    success_after = int(requests_after.get("success", 0))
    ok8 = bool(total_after > baseline_total and success_after > baseline_success)
    record(
        "metrics accumulate",
        ok8,
        f"requests.total: {baseline_total} → {total_after}; success: {baseline_success} → {success_after}",
    )
    if not ok8:
        return 1

    print("\n=== Summary ===")
    for name, ok, detail in results:
        status = "OK " if ok else "FAIL"
        print(f"  [{status}]  {name}" + (f" — {detail}" if not ok else ""))

    print(f"\nPassed: {passed}/{len(results)}   Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
