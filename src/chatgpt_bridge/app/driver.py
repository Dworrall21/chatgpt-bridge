"""DesktopDriver: the Phase 3 executor that drives the real app.

Reuses the canary-verified primitives:
  open pinned project -> scoped new chat -> enforce 5.6 Sol + High ->
  fill composer -> send -> wait -> extract assistant response text.
"""

from __future__ import annotations

import datetime
import re
import time
from typing import Any

from ..config import Config
from ..errors import (
    CompletionTimeoutError,
    ConversationMetadataUnavailableError,
    ModelAssertionFailedError,
    ProjectConfinementBreachError,
    ProjectNotFoundError,
    SendAmbiguousError,
    ToolApprovalRequestedError,
)
from ..registry import Registry
from .cdp_client import CdpClient, find_renderer
from .canary import _enforce_model_effort, _intelligence_picker, _visible_textbox

_UI_NOISE = ("Ask for approval", "Outputs", "Create a file or site", "Sources", "Attach files or connect apps")


class DesktopDriver:
    def __init__(self, config: Config, registry: Registry):
        self.config = config
        self.registry = registry
        self.project_id = config.app.required_project_id or "cloud:Dworrall21/chatgpt-bridge"

    # ---- conversation lifecycle -----------------------------------------
    def create_conversation(self, session_key: str, title: str) -> dict:
        """Open the pinned project and start a project-scoped conversation."""
        with CdpClient(self.config.app.cdp_url) as client:
            renderer = find_renderer(client, self.config.app.renderer_origin)
            if renderer is None:
                raise ProjectNotFoundError("no renderer found")
            page = renderer.page
            row = page.locator(f'[data-app-action-sidebar-project-id="{self.project_id}"]')
            if row.count() == 0:
                raise ProjectConfinementBreachError("pinned project row not in sidebar")
            row.first.click(force=True)
            page.wait_for_timeout(2200)
            start = page.locator(f'[aria-label="Start new chat in {self.config.app.required_project_name}"]')
            if start.count() == 0:
                raise ProjectConfinementBreachError("project-scoped new-chat primitive missing")
            start.first.click(force=True)
            page.wait_for_timeout(2200)
            if _visible_textbox(page) is None:
                raise ConversationMetadataUnavailableError("composer did not open")
            thread_id = None
            for _ in range(4):
                thread_id = self._active_thread_id(page)
                if thread_id:
                    break
                page.wait_for_timeout(400)
            # thread row may only appear after the first send; caller updates it.
        now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        conv = {
            "conversation_id": f"{session_key}-{now}",
            "session_key": session_key,
            "shard_number": 1,
            "project_id": self.project_id,
            "title": title,
            "conversation_href": thread_id,  # app thread id used for reuse
            "mode": self.config.app.required_mode,
            "model_label": self.config.app.required_model_label,
            "model_backend_id": None,
            "effort_label": self.config.app.required_effort_label,
            "turn_count": 0,
            "estimated_context_tokens": 0,
            "created_at": now,
            "last_used_at": now,
            "verification_method": "cdp-scoped-primitive",
            "verification_timestamp": now,
            "status": "active",
        }
        self.registry.create_conversation(conv)
        return conv

    @staticmethod
    def _active_thread_id(page: Any) -> str | None:
        try:
            return page.evaluate(
                """() => {
                  const el = document.querySelector('[data-app-action-sidebar-thread-active="true"]');
                  return el ? el.getAttribute('data-app-action-sidebar-thread-id') : null;
                }"""
            )
        except Exception:
            return None

    def capture_active_thread_id(self) -> str | None:
        with CdpClient(self.config.app.cdp_url) as client:
            renderer = find_renderer(client, self.config.app.renderer_origin)
            if renderer is None:
                return None
            return self._active_thread_id(renderer.page)

    def open_conversation(self, thread_id: str) -> None:
        """Navigate to an existing project conversation by its app thread id."""
        with CdpClient(self.config.app.cdp_url) as client:
            renderer = find_renderer(client, self.config.app.renderer_origin)
            if renderer is None:
                raise ProjectNotFoundError("no renderer found")
            page = renderer.page
            row = page.locator(f'[data-app-action-sidebar-thread-id="{thread_id}"]')
            if row.count() == 0:
                # sidebar may need the project expanded: open the project first
                proj = page.locator(f'[data-app-action-sidebar-project-id="{self.project_id}"]')
                if proj.count():
                    proj.first.click(force=True)
                    page.wait_for_timeout(2200)
                row = page.locator(f'[data-app-action-sidebar-thread-id="{thread_id}"]')
                if row.count() == 0:
                    raise ConversationMetadataUnavailableError(f"thread {thread_id} not in sidebar")
            row.first.click(force=True)
            page.wait_for_timeout(2200)
            if _visible_textbox(page) is None:
                raise ConversationMetadataUnavailableError("composer did not open for existing thread")

    def read_conversation(self, thread_id: str) -> str:
        """Open a conversation and return its body text (for reconciliation)."""
        self.open_conversation(thread_id)
        with CdpClient(self.config.app.cdp_url) as client:
            renderer = find_renderer(client, self.config.app.renderer_origin)
            return renderer.page.evaluate("() => document.body.innerText")

    # ---- send ------------------------------------------------------------
    def send_and_wait(self, prompt: str, *, timeout_s: int = 900, poll_s: float = 2.0) -> str:
        with CdpClient(self.config.app.cdp_url) as client:
            renderer = find_renderer(client, self.config.app.renderer_origin)
            if renderer is None:
                raise ProjectNotFoundError("no renderer found")
            page = renderer.page

            self._enforce(page)

            box = _visible_textbox(page)
            if box is None:
                raise ConversationMetadataUnavailableError("no visible composer textbox")
            box.click(force=True)
            page.wait_for_timeout(300)
            box.fill(prompt)
            page.wait_for_timeout(300)
            sends = page.locator('button[aria-label="Send prompt"], button[aria-label="Send"]')
            if sends.count():
                sends.last.click(force=True)
            else:
                page.keyboard.press("Enter")
            page.wait_for_timeout(1500)
            body = page.evaluate("() => document.body.innerText")
            if prompt.splitlines()[0][:60] not in body:
                raise SendAmbiguousError("prompt sentinel not found in conversation after send")

            deadline = time.time() + timeout_s
            while time.time() < deadline:
                time.sleep(poll_s)
                try:
                    txt = page.evaluate("() => document.body.innerText")
                except Exception:
                    continue
                if "Ask for approval" in txt and "approval" in txt and self._approval_card(page):
                    raise ToolApprovalRequestedError("tool approval UI appeared; never approving")
                if not self._still_running(txt):
                    return self._extract_response(txt)
            raise CompletionTimeoutError(f"no stable response within {timeout_s}s")

    def _enforce(self, page: Any) -> None:
        model = _enforce_model_effort(page)
        if "Sol" not in model["pill"] or "High" not in model["pill"]:
            raise ModelAssertionFailedError(f"pill {model['pill']!r} not Sol/High")

    @staticmethod
    def _still_running(txt: str) -> bool:
        return any(m in txt for m in ("Thinking", "Stop", "Responding", "Generating"))

    @staticmethod
    def _approval_card(page: Any) -> bool:
        """True only for a REAL tool-approval card (exact action buttons),
        never the composer's 'Ask for approval' mode toggle."""
        try:
            labels = {
                (b.inner_text() or "").strip()
                for b in page.locator("button").all()
            }
        except Exception:
            return False
        actions = {"Approve", "Allow", "Deny", "Reject", "Confirm", "Cancel request"}
        return len(labels & actions) >= 2

    @staticmethod
    def _extract_response(txt: str) -> str:
        """Take the assistant response block after the last user turn."""
        cut = txt
        for marker in _UI_NOISE:
            i = cut.find(marker)
            if i > 0:
                cut = cut[:i]
        # last user block: split on 'You said:'
        parts = cut.split("You said:")
        if len(parts) >= 2:
            cut = parts[-1]
            # drop the user's own prompt line(s) up to the timestamp
            m = re.search(r"\d{1,2}:\d{2}\s*(AM|PM)", cut)
            if m:
                cut = cut[m.end():]
        # drop leading assistant-turn label if present
        cut = re.sub(r"^ChatGPT said:\s*", "", cut.strip())
        # terminate at the HERMES-DONE marker (drop trailing timestamps/UI)
        m = re.search(r"HERMES-DONE", cut)
        if m:
            cut = cut[: m.end()]
        return cut.strip()
