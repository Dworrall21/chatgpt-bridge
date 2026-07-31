"""Phase 1 read-only discovery.

Connects to the running app via CDP, identifies the renderer, records account/
app identity signals, enumerates the Projects sidebar, locates chatgpt-bridge,
captures opaque project ID from available DOM sources, and records the model
picker structure. NO clicks that create conversations; NO sends.
"""

from __future__ import annotations

import json
import re

from ..errors import AppIdentityMismatchError, CdpUnavailableError, ProjectAmbiguousError, ProjectNotFoundError
from .cdp_client import CdpClient, find_renderer

_PROJECT_NAME = "chatgpt-bridge"

_JS_PROJECT_ENTRIES = """
() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('a,button,[role=button],[role=link]')) {
    const text = (el.innerText || el.textContent || '').trim();
    if (text !== 'chatgpt-bridge') continue;
    const href = el.getAttribute('href') || el.getAttribute('data-href') || '';
    const attrs = {};
    for (const a of el.attributes) {
      const n = a.name;
      if (n.startsWith('data-') || n === 'href' || n === 'id' || n === 'aria-label') attrs[n] = a.value;
    }
    const key = text + '|' + href + '|' + JSON.stringify(attrs);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ tag: el.tagName, href, attrs });
  }
  return out;
}
"""

_JS_PROJECT_SIDEBAR = """
() => {
  // Find a sidebar section labeled Projects, then list its entries.
  const text = (el) => (el.innerText || el.textContent || '').trim();
  let section = null;
  for (const el of document.querySelectorAll('nav,aside,div')) {
    if (text(el) === 'Projects' && el.children.length > 0 && el.children.length < 12) { section = el; break; }
  }
  if (!section) return { section_found: false, entries: [] };
  const entries = [];
  let container = section.parentElement;
  for (const el of container.querySelectorAll('a,button,[role=button]')) {
    const t = text(el);
    if (t && t.length < 80 && !entries.some(e => e.text === t)) {
      entries.push({ text: t, href: el.getAttribute('href') || '', attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value]).filter(([k]) => k.startsWith('data-'))) });
    }
  }
  return { section_found: true, entries };
}
"""

_JS_APP_IDENTITY = """
() => {
  const identity = {
    origin: location.origin,
    title: document.title,
    electronBridge: typeof window.electronBridge === 'object',
    bridgeKeys: Object.keys(window.electronBridge || {}).slice(0, 30),
    codexRoot: typeof window.__codexRoot === 'object',
    userAgent: navigator.userAgent.slice(0, 160),
    hasModelPicker: !!document.querySelector('[aria-label="Select ChatGPT model"]'),
    modelPickerText: (document.querySelector('[aria-label="Select ChatGPT model"]') || {}).innerText || null,
  };
  return identity;
}
"""

_JS_MODEL_MENU = """
() => {
  const items = [];
  for (const m of document.querySelectorAll('[role=menuitem][data-orientation="vertical"]')) {
    const t = (m.innerText || '').trim().replace(/\\n/g, '|');
    if (!t) continue;
    items.push({ text: t, selected: m.getAttribute('data-chatgpt-model-selected'), state: m.getAttribute('data-state') });
  }
  return { found: true, items };
}
"""


def _capture_project_id(entries: list[dict]) -> list[str]:
    """Collect candidate opaque IDs from hrefs/data attributes."""
    ids: list[str] = []
    patterns = [
        re.compile(r"projects?[/=]([0-9a-fA-F-]{20,})"),
        re.compile(r"p[/=]([0-9a-fA-F-]{20,})"),
        re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
        re.compile(r"(cloud:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"),
    ]
    for e in entries:
        for v in [e.get("href", "")] + list(e.get("attrs", {}).values()):
            if not isinstance(v, str):
                continue
            for pat in patterns:
                m = pat.search(v)
                if m:
                    ids.append(m.group(1) if m.lastindex else m.group(0))
    return ids


def discover(*, cdp_url: str = "http://127.0.0.1:9222", renderer_origin: str = "http://127.0.0.1:5175") -> dict:
    with CdpClient(cdp_url) as client:
        renderer = find_renderer(client, renderer_origin)
        if renderer is None:
            raise CdpUnavailableError("no renderer with body text found")
        pg = renderer.page

        identity = pg.evaluate(_JS_APP_IDENTITY)
        if identity.get("origin") != renderer_origin or not identity.get("electronBridge"):
            raise AppIdentityMismatchError(
                f"renderer identity unexpected: origin={identity.get('origin')} bridge={identity.get('electronBridge')}"
            )

        project_entries = pg.evaluate(_JS_PROJECT_ENTRIES)
        sidebar = pg.evaluate(_JS_PROJECT_SIDEBAR)

        matches = [e for e in project_entries if (e.get("attrs", {}).get("data-") is not None or True)]
        if not project_entries:
            raise ProjectNotFoundError("no chatgpt-bridge entry found in DOM")

        candidate_ids = _capture_project_id(project_entries + sidebar.get("entries", []))
        unique_ids = sorted(set(candidate_ids))

        # Prefer the exact chatgpt-bridge row attribute as the authoritative ID source.
        pinned_row_id = None
        for e in sidebar.get("entries", []):
            pid = (e.get("attrs", {}) or {}).get("data-app-action-sidebar-project-id")
            label = (e.get("attrs", {}) or {}).get("data-app-action-sidebar-project-label")
            if label == _PROJECT_NAME and pid:
                pinned_row_id = pid
                break
        if pinned_row_id is None:
            for e in project_entries:
                pid = (e.get("attrs", {}) or {}).get("data-app-action-sidebar-project-id")
                if pid and _PROJECT_NAME in pid:
                    pinned_row_id = pid
                    break
        # Independent source 2: a DIFFERENT DOM element carrying the same pinned id.
        carriers = []
        for x in project_entries:
            attrs = x.get("attrs", {}) or {}
            if attrs.get("data-app-action-sidebar-project-id") == pinned_row_id or attrs.get("href") == pinned_row_id:
                carriers.append(x)
        id_sources = len({id(e) for e in carriers}) if pinned_row_id else 0

        # Model picker observation (read-only; trusted CDP click, closes menu afterwards).
        model = None
        try:
            pg.locator('[aria-label="Select ChatGPT model"]').click(force=True)
            pg.wait_for_timeout(700)
            model = pg.evaluate(_JS_MODEL_MENU)
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(200)
        except Exception as e:  # noqa: BLE001
            model = {"found": False, "error": str(e)}

        account_fingerprint = identity.get("bridgeKeys", []) and f"origin:{identity.get('origin')}|ua:{identity.get('userAgent','')[:64]}"

        return {
            "ok": True,
            "phase": 1,
            "renderer": {"url": renderer.url, "body_chars": renderer.body_chars},
            "app_identity": identity,
            "projects": {
                "direct_matches": len(project_entries),
                "sidebar": sidebar,
                "project_name": _PROJECT_NAME,
                "pinned_project_id": pinned_row_id,
                "candidate_ids": unique_ids,
                "id_sources": id_sources,
                "enrollment_ready": id_sources >= 2,
                "note": "opaque ID must be confirmed from >=2 independent DOM sources before Phase 2",
            },
            "model_picker": model,
            "account_fingerprint": account_fingerprint,
        }
