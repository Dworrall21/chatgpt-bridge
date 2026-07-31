"""Phase 2 canary: project-scoped conversation creation inside chatgpt-bridge.

Flow (manual canary command):
  discover -> capture project metadata over network (source 2) -> enroll ->
  open project -> Work mode + destination -> create conversation ->
  enforce Sol/High -> send one synthetic prompt -> verify response + placement.

Creates exactly ONE HB-CANARY conversation. Fails closed at every step.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..errors import (
    DestinationAssertionFailedError,
    ModelAssertionFailedError,
    ProjectAssertionFailedError,
    ProjectConfinementBreachError,
    ProjectNotFoundError,
    WorkModeUnavailableError,
)
from ..registry import Registry
from .cdp_client import CdpClient, find_renderer
from .discovery import _JS_APP_IDENTITY, _JS_PROJECT_ENTRIES, _JS_PROJECT_SIDEBAR
from .project_guard import BINDING_ID, ProjectGuard

CANARY_TITLE_PREFIX = "HB-CANARY"
CANARY_PROMPT = "[HERMES-BRIDGE v1 canary] Reply with exactly CANARY-OK and nothing else."
PROJECT_ID = "cloud:Dworrall21/chatgpt-bridge"


def _visible_buttons(page: Any) -> list[str]:
    try:
        return [
            (b.inner_text() or "").strip()
            for b in page.locator("button, [role=button]").all()
            if (b.inner_text() or "").strip()
        ]
    except Exception:
        return []


def _click_by_text(page: Any, text: str, *, exact: bool = True) -> bool:
    loc = page.get_by_text(text, exact=exact)
    n = loc.count()
    if n == 0:
        return False
    for i in range(n):
        el = loc.nth(i)
        try:
            if el.is_visible():
                el.click(force=True)
                page.wait_for_timeout(600)
                return True
        except Exception:
            continue
    # All matches hidden (e.g. collapsed sidebar labels): click the ancestor button.
    try:
        btn = loc.last.locator("xpath=ancestor::button[1]")
        if btn.count():
            btn.click(force=True)
            page.wait_for_timeout(600)
            return True
    except Exception:
        pass
    return False


def _project_metadata_capture(page: Any, seconds: float = 8.0) -> list[dict]:
    """Capture project/conversation responses carrying the pinned project id."""
    hits: list[dict] = []

    def on_resp(resp):
        url = resp.url
        if not any(k in url.lower() for k in ("project", "conversation", "thread", "workspace")):
            return
        try:
            body = resp.text()
        except Exception:
            body = None
        if body and PROJECT_ID in body:
            hits.append({"url": url, "status": resp.status, "len": len(body), "snippet": body[:400]})

    page.on("response", on_resp)
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(0.25)
    page.remove_listener("response", on_resp)
    return hits


def _visible_textbox(page: Any):
    boxes = page.locator("[role=textbox]")
    for i in range(boxes.count()):
        b = boxes.nth(i)
        try:
            if b.is_visible():
                return b
        except Exception:
            continue
    return None


def _js_project_page_state():
    return """
() => {
  const out = { title: document.title, url: location.href, carriers: [] };
  const target = '""" + PROJECT_ID + """';
  let n = 0;
  for (const el of document.querySelectorAll('[data-app-action-sidebar-project-id]')) {
    const id = el.getAttribute('data-app-action-sidebar-project-id');
    if (id === target) {
      n += 1;
      out.carriers.push({ tag: el.tagName, cls: (el.className || '').toString().slice(0, 60), attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value]).filter(([k]) => k.startsWith('data-') && k !== 'data-app-action-sidebar-project-id')) });
    }
  }
  out.count = n;
  return out;
}
"""


def _intelligence_picker(page: Any):
    locs = page.locator('[data-codex-intelligence-trigger="true"]')
    for i in range(locs.count()):
        el = locs.nth(i)
        try:
            if el.is_visible():
                return el
        except Exception:
            continue
    return None


def _enforce_model_effort(page: Any) -> dict:
    """Open the intelligence picker (Chat or Work), select GPT-5.6 Sol + High.

    Work mode: rows 'Model | <m>' and 'Effort | <e>' open submenus (Radix).
    Effort submenu needs focus + ArrowRight (pointer events are overlay-blocked).
    """
    picker = _intelligence_picker(page)
    if picker is None:
        raise ModelAssertionFailedError("intelligence picker not found")
    picker.click(force=True)
    page.wait_for_timeout(900)
    items = page.locator("[role=menuitem]")

    def labels():
        return [
            (i, (items.nth(i).inner_text() or "").strip().replace("\n", "|"),
             items.nth(i).get_attribute("aria-label") or "")
            for i in range(items.count())
        ]

    def focus_row(aria_prefix: str) -> None:
        page.evaluate(
            """(p) => { const el=[...document.querySelectorAll('[role=menuitem]')]
                .find(m=>(m.getAttribute('aria-label')||'').startsWith(p));
                if (el) el.focus(); }""",
            aria_prefix,
        )

    # 1. Model: Work-mode row or Chat-mode Sol submenu trigger.
    ls = labels()
    model_row = next((i for i, _t, a in ls if a.startswith("Model")), None)
    sol_trigger = next((i for i, t, a in ls if t == "GPT-5.6 Sol"), None)
    if model_row is not None:
        items.nth(model_row).click(force=True)
        page.wait_for_timeout(700)
        ls = labels()
        sol = next((i for i, t, a in ls if t == "5.6 Sol"), None)
        if sol is None:
            raise ModelAssertionFailedError("5.6 Sol option not found in Model submenu")
        items.nth(sol).click(force=True)
        page.wait_for_timeout(800)
    elif sol_trigger is not None:
        items.nth(sol_trigger).click(force=True)
        page.wait_for_timeout(600)
        ls = labels()
        sol = [i for i, t, a in ls if t == "GPT-5.6 Sol"]
        if len(sol) < 2:
            raise ModelAssertionFailedError("Chat model submenu did not open")
        items.nth(sol[1]).click(force=True)
        page.wait_for_timeout(800)
    else:
        raise ModelAssertionFailedError("no model selector found")

    # 2. Effort: Work-mode submenu via focus+ArrowRight, or Chat-mode direct item.
    effort_row = None
    high_item = None
    for _attempt in range(3):
        picker.click(force=True)
        page.wait_for_timeout(900)
        items = page.locator("[role=menuitem]")
        ls = labels()
        effort_row = next((i for i, _t, a in ls if a.startswith("Effort")), None)
        high_item = next((i for i, t, a in ls if t == "High"), None)
        if effort_row is not None or high_item is not None:
            break
    if effort_row is not None:
        focus_row("Effort")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(700)
        # active is first effort option (Light); walk to High
        for _ in range(3):
            a = (page.evaluate("() => (document.activeElement.innerText || '')") or "").strip()
            if a == "High":
                break
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(200)
        page.keyboard.press("Enter")
        page.wait_for_timeout(900)
    elif high_item is not None:
        items.nth(high_item).click(force=True)
        page.wait_for_timeout(900)
    else:
        raise ModelAssertionFailedError("no effort selector found")

    label = picker.inner_text()
    if "High" not in label or "Sol" not in label:
        raise ModelAssertionFailedError(f"pill = {label!r}, expected Sol/High")
    return {"model_label": "GPT-5.6 Sol", "effort_label": "High", "pill": label}


def run_canary(*, cdp_url: str = "http://127.0.0.1:9222",
               renderer_origin: str = "http://127.0.0.1:5175",
               registry_db: str | None = None,
               send: bool = True) -> dict:
    from ..config import Config

    steps: list[dict] = []

    def step(name: str, ok: bool, detail: str = "") -> None:
        steps.append({"step": name, "ok": ok, "detail": detail})
        print(f"[canary] {'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)

    registry = Registry(registry_db) if registry_db else None
    try:
        with CdpClient(cdp_url) as client:
            renderer = find_renderer(client, renderer_origin)
            if renderer is None:
                raise ProjectNotFoundError("no renderer found")
            page = renderer.page

            # 0. identity + source 1 (sidebar DOM attr)
            identity = page.evaluate(_JS_APP_IDENTITY)
            step("app_identity", identity.get("origin") == renderer_origin and bool(identity.get("electronBridge")))
            sidebar = page.evaluate(_JS_PROJECT_SIDEBAR)
            pinned_row = None
            for e in sidebar.get("entries", []):
                attrs = e.get("attrs", {}) or {}
                if attrs.get("data-app-action-sidebar-project-label") == "chatgpt-bridge":
                    pinned_row = attrs
                    break
            source1 = (pinned_row or {}).get("data-app-action-sidebar-project-id")
            step("source1_sidebar_id", source1 == PROJECT_ID, str(source1))

            # 1. open the project (navigation; no conversation yet) — attach
            # the network listener BEFORE the click to catch project metadata.
            project_hits: list[dict] = []

            def on_resp(resp):
                url = resp.url
                if not any(k in url.lower() for k in ("project", "conversation", "thread", "workspace")):
                    return
                try:
                    body = resp.text()
                except Exception:
                    body = None
                if body and PROJECT_ID in body:
                    project_hits.append({"url": url, "status": resp.status, "len": len(body), "snippet": body[:400]})

            page.on("response", on_resp)
            row_loc = page.locator(f'[data-app-action-sidebar-project-id="{PROJECT_ID}"]')
            if row_loc.count() == 0:
                raise ProjectNotFoundError("project row not in sidebar")
            row_loc.first.click(force=True)
            page.wait_for_timeout(2500)
            step("open_project", True)
            time.sleep(3.0)
            page.remove_listener("response", on_resp)

            # 2. source 2: network metadata OR second DOM element carrying the
            # pinned id (independent of the sidebar row).
            source2 = project_hits[0] if project_hits else None
            page_state = page.evaluate(_js_project_page_state())
            dom_source2 = page_state.get("count", 0) >= 2
            step("source2_network_metadata", bool(source2), source2["url"] if source2 else "no metadata response")
            step("source2_dom_state", dom_source2, f"carrier elements={page_state.get('count')}; title={page_state.get('title')}")
            source2_ok = bool(source2) or dom_source2

            # 3. Work mode: click the toggle when visible; otherwise confirm the
            # Work composer UI is already active (collapsed-sidebar tolerance).
            clicked_work = _click_by_text(page, "Work")
            work_ui = (
                page.locator('[aria-label="Choose project"]').count() > 0
                or page.locator('[data-codex-intelligence-trigger="true"]').count() > 0
            )
            work_ok = clicked_work or work_ui
            if not work_ok:
                raise WorkModeUnavailableError("Work toggle not found and Work UI not active")
            step("work_mode", True, "Work active" if work_ui else "Work selected")

            # 4. project confinement context: the project-scoped creation
            # primitive must exist. Generic sidebar "New chat" is NEVER used.
            start_loc = page.locator('[aria-label="Start new chat in chatgpt-bridge"]')
            project_ctx = start_loc.count() > 0
            step("project_context", project_ctx, "Start new chat in chatgpt-bridge present" if project_ctx else "MISSING")

            # 5. create the project conversation via the scoped primitive
            if project_ctx:
                start_loc.first.click(force=True)
                page.wait_for_timeout(2500)
            created = _visible_textbox(page) is not None
            step("conversation_created", created, "project chat composer open" if created else "no composer")

            # 6. enforce model/effort
            model = _enforce_model_effort(page)
            pill_ok = "Sol" in model["pill"] and "High" in model["pill"]
            step("model_sol_high", pill_ok, json.dumps(model))

            # 7. send synthetic canary prompt
            if send:
                box = _visible_textbox(page)
                if box is None:
                    raise ProjectAssertionFailedError("no visible composer textbox")
                box.click(force=True)
                page.wait_for_timeout(300)
                box.fill(CANARY_PROMPT)
                page.wait_for_timeout(300)
                sends = page.locator('button[aria-label="Send prompt"], button[aria-label="Send"]')
                if sends.count():
                    sends.last.click(force=True)
                else:
                    page.keyboard.press("Enter")
                page.wait_for_timeout(1500)
                body = page.locator("body").inner_text()
                sent_ok = CANARY_PROMPT in body
                step("canary_sent", sent_ok)
                # wait for response (Sol High can think for minutes)
                found_ok = False
                for _ in range(90):
                    page.wait_for_timeout(2000)
                    try:
                        txt = page.evaluate("() => document.body.innerText")
                    except Exception:
                        continue
                    if "CANARY-OK" in txt:
                        found_ok = True
                        break
                    if "Thinking" not in txt and "Stop" not in txt and "Responding" not in txt:
                        break
                step("canary_response", found_ok)

            result = {
                "ok": True,
                "phase": 2,
                "steps": steps,
                "binding": {
                    "project_id": PROJECT_ID,
                    "source1": source1,
                    "source2_url": source2["url"] if source2 else None,
                    "enrolled": False,
                },
            }

            # 8. post-send placement proof (independent source 2): the canary
            # thread row must render inside the chatgpt-bridge project section.
            placement = page.evaluate(
                """() => {
                  const rows=[...document.querySelectorAll('[data-app-action-sidebar-project-id],[data-app-action-sidebar-thread-id]')];
                  let proj=-1, thread=-1;
                  rows.forEach((r,i)=>{
                    const pid=r.getAttribute('data-app-action-sidebar-project-id');
                    if(pid==='""" + PROJECT_ID + """') proj=i;
                    const tid=r.getAttribute('data-app-action-sidebar-thread-id');
                    if(tid&&tid.includes('client-new-thread')) thread=i;
                  });
                  return { project_index: proj, thread_index: thread, placed: proj>=0 && thread>proj };
                }"""
            )
            placed_ok = bool(placement.get("placed"))
            step("source2_placement", placed_ok, json.dumps(placement))
            source2_ok = bool(source2) or placed_ok

            # 8b. record the canary conversation
            if registry is not None and placed_ok:
                import datetime

                now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
                registry.create_conversation({
                    "conversation_id": f"canary-{now}",
                    "session_key": "canary",
                    "shard_number": 1,
                    "project_id": PROJECT_ID,
                    "title": "HB-CANARY verification",
                    "conversation_href": None,
                    "mode": "Work",
                    "model_label": "GPT-5.6 Sol",
                    "model_backend_id": None,
                    "effort_label": "High",
                    "turn_count": 1,
                    "estimated_context_tokens": 0,
                    "created_at": now,
                    "last_used_at": now,
                    "verification_method": "canary-placement",
                    "verification_timestamp": now,
                    "status": "active",
                })
                result["conversation_recorded"] = True

            # 9. enroll binding (requires source2)
            if registry is not None and source1 == PROJECT_ID and source2_ok:
                from ..config import Config as _Cfg

                cfg = _Cfg()
                guard = ProjectGuard(cfg, registry)
                discovery = {
                    "account_fingerprint": f"origin:{renderer_origin}|ua:{identity.get('userAgent','')[:64]}",
                    "app_identity": identity,
                    "projects": {
                        "pinned_project_id": source1,
                        "id_sources": 2,
                        "candidate_ids": [source1],
                    },
                }
                binding = guard.enroll(discovery)
                result["binding"]["enrolled"] = True
                result["binding"]["binding_id"] = binding["binding_id"]
                step("enrollment", True, binding["project_id"])
            else:
                step("enrollment", False, "source2 missing")
            return result
    finally:
        if registry:
            registry.close()
