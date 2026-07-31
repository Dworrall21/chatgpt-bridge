"""Minimal CDP client over Playwright (Phase 1, read-only).

Wraps connect_over_cdp and picks the main renderer by body-text heuristic,
mirroring the proven chatgpt_desktop.py approach.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Renderer:
    url: str
    body_chars: int
    page: Any  # playwright Page


class CdpClient:
    def __init__(self, cdp_url: str = "http://127.0.0.1:9222"):
        self.cdp_url = cdp_url
        self._playwright = None
        self._browser = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        return self

    def __exit__(self, *exc):
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            self._playwright.stop()

    def pages(self) -> list[Renderer]:
        out = []
        for ctx in self._browser.contexts:
            for pg in ctx.pages:
                try:
                    body = pg.locator("body").inner_text()
                except Exception:
                    body = ""
                out.append(Renderer(url=pg.url, body_chars=len(body), page=pg))
        return out


def find_renderer(client: CdpClient, origin: str | None = None) -> Renderer | None:
    candidates = [r for r in client.pages() if r.body_chars > 500]
    if not candidates:
        return None
    if origin:
        for r in candidates:
            if r.url.startswith(origin):
                return r
    return max(candidates, key=lambda r: r.body_chars)
