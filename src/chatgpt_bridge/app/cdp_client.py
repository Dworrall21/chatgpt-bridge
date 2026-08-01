"""Minimal CDP client over Playwright.

Playwright sync objects are thread-bound, so every operation gets its own
connection. A global lock serializes connections (the app's CDP endpoint
degrades under concurrent connect churn); connections are always closed.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class Renderer:
    url: str
    body_chars: int
    page: Any  # playwright Page


_connect_lock = threading.Lock()


class CdpClient:
    def __init__(self, cdp_url: str = "http://127.0.0.1:9222", timeout_ms: int = 30_000):
        self.cdp_url = cdp_url
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None

    def __enter__(self):
        last = None
        for attempt in range(3):
            try:
                with _connect_lock:
                    from playwright.sync_api import sync_playwright

                    if self._pw is None:
                        self._pw = sync_playwright().start()
                    self._browser = self._pw.chromium.connect_over_cdp(
                        self.cdp_url, timeout=self.timeout_ms
                    )
                return _CdpHandle(self)
            except Exception as e:  # noqa: BLE001
                last = e
                self._close()
                time.sleep(2 * (attempt + 1))
        raise last if last is not None else RuntimeError("CDP connect failed")

    def __exit__(self, *exc):
        self._close()
        return False

    def _close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        self._browser = None
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None


class _CdpHandle:
    def __init__(self, client: CdpClient):
        self._client = client

    @property
    def contexts(self):
        return self._client._browser.contexts


def find_renderer(client: _CdpHandle, origin: str | None = None) -> Renderer | None:
    candidates = []
    for ctx in client.contexts:
        for pg in ctx.pages:
            try:
                body = pg.locator("body").inner_text()
            except Exception:
                body = ""
            if len(body) > 500:
                candidates.append(Renderer(url=pg.url, body_chars=len(body), page=pg))
    if not candidates:
        return None
    if origin:
        for r in candidates:
            if r.url.startswith(origin):
                return r
    return max(candidates, key=lambda r: r.body_chars)
