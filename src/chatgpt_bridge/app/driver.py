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
        self._assert_loopback_cdp(config.app.cdp_url)

    @staticmethod
    def _assert_loopback_cdp(cdp_url: str) -> None:
        from urllib.parse import urlparse

        host = urlparse(cdp_url).hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ProjectConfinementBreachError(f"CDP endpoint must be loopback, got {host!r}")

    @staticmethod
    def _verify_renderer(page: Any, origin: str) -> None:
        from ..errors import AppIdentityMismatchError

        identity = page.evaluate(
            """() => ({ origin: location.origin, bridge: typeof window.electronBridge === 'object' })"""
        )
        if identity.get("origin") != origin or not identity.get("bridge"):
            raise AppIdentityMismatchError(f"renderer identity mismatch: {identity}")

    def project_fingerprint(self) -> str:
        with CdpClient(self.config.app.cdp_url) as client:
            renderer = find_renderer(client, self.config.app.renderer_origin)
            if renderer is None:
                raise ProjectNotFoundError("no renderer found")
            page = renderer.page
            ua = page.evaluate("() => navigator.userAgent") or ""
            return f"origin:{self.config.app.renderer_origin}|ua:{ua[:64]}"

    def assert_account_matches(self, binding: dict) -> None:
        from ..errors import AccountMismatchError

        enrolled = binding.get("account_fingerprint") or ""
        if enrolled in ("", "unknown"):
            return  # never enrolled with a fingerprint; nothing to compare
        current = self.project_fingerprint()
        if current != enrolled:
            raise AccountMismatchError("account fingerprint changed since enrollment")

    # ---- conversation lifecycle -----------------------------------------
    def create_conversation(self, session_key: str, title: str) -> dict:
        """Open the pinned project and start a project-scoped conversation."""
        with CdpClient(self.config.app.cdp_url) as client:
            renderer = find_renderer(client, self.config.app.renderer_origin)
            if renderer is None:
                raise ProjectNotFoundError("no renderer found")
            page = renderer.page
            self._verify_renderer(page, self.config.app.renderer_origin)
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
            self._verify_renderer(page, self.config.app.renderer_origin)
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
            # confinement: active thread must equal the requested id AND sit
            # inside the pinned project section of the sidebar
            placement = page.evaluate(
                """(o) => {
                  const pid = o.pid, tid = o.tid;
                  const rows=[...document.querySelectorAll('[data-app-action-sidebar-project-id],[data-app-action-sidebar-thread-id]')];
                  let proj=-1, thread=-1;
                  rows.forEach((r,i)=>{
                    if (r.getAttribute('data-app-action-sidebar-project-id')===pid) proj=i;
                    if (r.getAttribute('data-app-action-sidebar-thread-id')===tid) thread=i;
                  });
                  const active = document.querySelector('[data-app-action-sidebar-thread-active="true"]');
                  return { proj, thread, placed: proj>=0 && thread>proj,
                           active: active ? active.getAttribute('data-app-action-sidebar-thread-id') : null };
                }""",
                {"pid": self.project_id, "tid": thread_id},
            )
            if not placement.get("placed") or placement.get("active") != thread_id:
                raise ProjectConfinementBreachError(
                    f"thread {thread_id} not confined to project section: {placement}"
                )

    def read_conversation(self, thread_id: str) -> str:
        """Open a conversation and return its body text (for reconciliation)."""
        self.open_conversation(thread_id)
        with CdpClient(self.config.app.cdp_url) as client:
            renderer = find_renderer(client, self.config.app.renderer_origin)
            return renderer.page.evaluate("() => document.body.innerText")

    # ---- send ------------------------------------------------------------
    @staticmethod
    def _tail_after_user(txt: str) -> str:
        """Text after the LAST user turn's timestamp (excludes the prompt
        itself, which contains the HERMES-DONE instruction line)."""
        idx = txt.rfind("You said:")
        if idx < 0:
            return txt
        rest = txt[idx:]
        m = re.search(r"\d{1,2}:\d{2}\s*(AM|PM)", rest)
        if m:
            rest = rest[m.end():]
        return rest

    def send_and_wait(self, prompt: str, *, timeout_s: int = 900, poll_s: float = 2.0) -> str:
        with CdpClient(self.config.app.cdp_url) as client:
            renderer = find_renderer(client, self.config.app.renderer_origin)
            if renderer is None:
                raise ProjectNotFoundError("no renderer found")
            page = renderer.page
            self._verify_renderer(page, self.config.app.renderer_origin)

            self._enforce(page)

            box = _visible_textbox(page)
            if box is None:
                raise ConversationMetadataUnavailableError("no visible composer textbox")
            box.click(force=True)
            page.wait_for_timeout(300)
            self._insert_text(page, box, prompt)
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
            stable = 0
            last_tail_hash = None
            while time.time() < deadline:
                time.sleep(poll_s)
                try:
                    txt = page.evaluate("() => document.body.innerText")
                except Exception:
                    continue
                tail = self._tail_after_user(txt)
                # authoritative completion: HERMES-DONE in the assistant area
                if "HERMES-DONE" in tail:
                    return self._extract_response(txt)
                # fallback: finished-looking tail (assistant text, no 'Thinking')
                # that is stable across consecutive polls and no Stop control.
                try:
                    stop = page.locator('button[aria-label*="Stop" i], [data-testid="stop-button"]').count()
                except Exception:
                    stop = 1
                looks_done = "ChatGPT said:" in tail and "Thinking" not in tail
                if looks_done and stop == 0:
                    import hashlib

                    h = hashlib.sha256(tail.encode("utf-8")).hexdigest()
                    if h == last_tail_hash:
                        stable += 1
                        if stable >= 4:
                            return self._extract_response(txt)
                    else:
                        stable = 0
                        last_tail_hash = h
                else:
                    stable = 0
                    last_tail_hash = None
                if self._approval_card(page):
                    raise ToolApprovalRequestedError("tool approval UI appeared; never approving")
            raise CompletionTimeoutError(f"no HERMES-DONE within {timeout_s}s")

    @staticmethod
    def _insert_text(page: Any, box: Any, text: str) -> None:
        """Insert prompt into the ProseMirror composer. fill() is used for
        small prompts; large prompts go through a synthetic paste event which
        ProseMirror handles far more efficiently."""
        if len(text) < 20_000:
            try:
                box.fill(text)
                return
            except Exception:
                pass
        ok = page.evaluate(
            """(t) => {
                const dt = new DataTransfer();
                dt.setData('text/plain', t);
                const el = document.activeElement && document.activeElement.isContentEditable
                    ? document.activeElement
                    : document.querySelector('[contenteditable="true"]');
                if (!el) return false;
                el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
                return true;
            }""",
            text,
        )
        if not ok:
            # last resort: chunked fill
            box.fill(text[:100_000])

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
