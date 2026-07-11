#!/usr/bin/env python3
"""
chatgpt-cdp.py — Send prompts to ChatGPT through Chrome CDP.

The standalone CDP client is session-aware. By default it:
  * selects model "5.6 sol" with "high" reasoning effort;
  * reuses one ChatGPT conversation per HERMES_SESSION_ID;
  * creates new conversations inside the "MoA" ChatGPT project; and
  * names each conversation "<summary> — <Hermes session id>" so it can be
    recovered even if the local state file is lost.

Usage:
    python3 chatgpt-cdp.py "Your prompt here"
    python3 chatgpt-cdp.py --file prompt.txt
    echo "prompt" | python3 chatgpt-cdp.py

Environment overrides:
    HERMES_SESSION_ID, HERMES_SESSION_SUMMARY
    CHATGPT_CDP_MODEL, CHATGPT_CDP_EFFORT, CHATGPT_CDP_PROJECT
    CHATGPT_CDP_STATE_FILE

Requirements:
    Chrome running with --remote-debugging-port=9222
    Signed in to ChatGPT
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request

CDP_PORT = int(os.getenv("CHATGPT_CDP_PORT", "9222"))
DEFAULT_MODEL = os.getenv("CHATGPT_CDP_MODEL", "5.6 sol")
DEFAULT_EFFORT = os.getenv("CHATGPT_CDP_EFFORT", "high")
DEFAULT_PROJECT = os.getenv("CHATGPT_CDP_PROJECT", "MoA")
DEFAULT_TIMEOUT = int(os.getenv("CHATGPT_CDP_TIMEOUT", "600"))
STATE_FILE = Path(
    os.getenv(
        "CHATGPT_CDP_STATE_FILE",
        str(Path.home() / ".hermes" / "chatgpt-cdp" / "state.json"),
    )
).expanduser()
TITLE_SEPARATOR = " — "
MAX_TITLE_LENGTH = 100


def _squash_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_summary(value: str | None) -> str:
    text = _squash_space(value)
    text = re.sub(r"^(user|system|assistant)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"^[#>*_`\-\s]+", "", text)
    text = re.sub(r"\s*[.!?]+$", "", text)
    return text or "Hermes session"


def infer_summary(prompt: str, explicit: str | None = None, stored: str | None = None) -> str:
    """Choose a stable human-readable session summary.

    Explicit CLI/environment summaries win. A previously stored summary is kept
    for follow-up turns. Only a brand-new session derives its summary from the
    first non-empty prompt line.
    """
    if explicit:
        return _clean_summary(explicit)
    if stored:
        return _clean_summary(stored)
    first_line = next((line.strip() for line in prompt.splitlines() if line.strip()), prompt.strip())
    words = _clean_summary(first_line).split()
    summary = " ".join(words[:12])
    return summary[:72].rstrip(" -—:;,.") or "Hermes session"


def build_conversation_title(summary: str, session_id: str) -> str:
    """Return ``<summary> — <session-id>`` while preserving the full id."""
    session_id = _squash_space(session_id) or "standalone"
    suffix = f"{TITLE_SEPARATOR}{session_id}"
    room = max(1, MAX_TITLE_LENGTH - len(suffix))
    short_summary = _clean_summary(summary)[:room].rstrip(" -—:;,.") or "Session"
    return f"{short_summary}{suffix}"


def title_summary(title: str | None, session_id: str) -> str | None:
    """Extract the summary from a deterministic session title."""
    if not title:
        return None
    suffix = f"{TITLE_SEPARATOR}{session_id}"
    if str(title).endswith(suffix):
        return str(title)[: -len(suffix)].strip() or None
    return None


def resolve_session_id(cli_value: str | None = None) -> str:
    for value in (
        cli_value,
        os.getenv("HERMES_SESSION_ID"),
        os.getenv("HERMES_SESSION_KEY"),
        os.getenv("HERMES_SESSION"),
    ):
        if value and str(value).strip():
            return str(value).strip()
    return "standalone"


def load_state(path: Path = STATE_FILE) -> dict:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data.setdefault("version", 1)
            data.setdefault("projects", {})
            data.setdefault("sessions", {})
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return {"version": 1, "projects": {}, "sessions": {}}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


@contextmanager
def process_lock(path: Path = STATE_FILE):
    """Serialize CDP use so concurrent Hermes calls cannot create duplicate chats."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()


def find_chatgpt_tab() -> tuple[str | None, str | None]:
    req = urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3)
    tabs = json.loads(req.read())
    candidates = [t for t in tabs if "chatgpt.com" in t.get("url", "")]
    if not candidates:
        return None, None
    # Prefer a normal page target over workers or extension targets.
    candidates.sort(key=lambda t: (t.get("type") != "page", "temporary-chat" in t.get("url", "")))
    tab = candidates[0]
    return tab.get("id"), tab.get("url")


class CDPPage:
    def __init__(self, page_id: str):
        self.page_id = page_id
        self.ws = None
        self._next_id = 1

    async def __aenter__(self):
        import websockets

        ws_url = f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{self.page_id}"
        self.ws = await websockets.connect(ws_url, max_size=2**22)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.ws is not None:
            await self.ws.close()

    async def call(self, method: str, params: dict | None = None, timeout: float = 15):
        assert self.ws is not None
        request_id = self._next_id
        self._next_id += 1
        await self.ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            data = json.loads(raw)
            if data.get("id") != request_id:
                continue
            if data.get("error"):
                raise RuntimeError(f"CDP {method} failed: {data['error']}")
            return data.get("result", {})

    async def js(self, expression: str, timeout: float = 15, await_promise: bool = False):
        result = await self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "timeout": int(timeout * 1000),
            },
            timeout=timeout + 5,
        )
        exc = result.get("exceptionDetails")
        if exc:
            description = result.get("result", {}).get("description") or exc.get("text") or "CDP eval error"
            raise RuntimeError(description)
        return result.get("result", {}).get("value")

    async def navigate(self, url: str, timeout: float = 30):
        await self.call("Page.navigate", {"url": url}, timeout=10)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready = await self.js("document.readyState", timeout=5)
            if ready in {"interactive", "complete"}:
                return
            await asyncio.sleep(0.25)
        raise RuntimeError(f"Timed out navigating to {url}")

    async def wait_for_input(self, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = await self.js(
                "!!document.querySelector('#prompt-textarea, div[contenteditable=\"true\"][role=\"textbox\"], div.ProseMirror[contenteditable]')",
                timeout=5,
            )
            if found:
                return True
            await asyncio.sleep(0.5)
        return False

    async def page_info(self) -> dict:
        raw = await self.js(
            "JSON.stringify({url: location.href, path: location.pathname, title: document.title})",
            timeout=5,
        )
        return json.loads(raw or "{}")

    async def current_conversation(self) -> tuple[str | None, str | None]:
        raw = await self.js(
            r"""
JSON.stringify((() => {
  const match = location.pathname.match(/\/c\/([a-zA-Z0-9-]+)/);
  let title = document.title || '';
  title = title.replace(/\s*[-–—]\s*ChatGPT\s*$/i, '').trim();
  const current = document.querySelector('a[href="' + location.pathname + '"]');
  if ((!title || /^ChatGPT$/i.test(title)) && current) title = (current.textContent || '').trim();
  return {id: match ? match[1] : null, title: title || null};
})())
""",
            timeout=5,
        )
        data = json.loads(raw or "{}")
        return data.get("id"), data.get("title")


async def find_project_url(page: CDPPage, project_name: str) -> str | None:
    project_json = json.dumps(project_name)
    raw = await page.js(
        f"""
JSON.stringify((() => {{
  const wanted = {project_json}.trim().toLowerCase();
  const rows = Array.from(document.querySelectorAll('a[href]')).map(a => {{
    const text = ((a.textContent || a.getAttribute('aria-label') || a.getAttribute('title') || '')).replace(/\\s+/g, ' ').trim();
    const href = a.href || '';
    let score = 99;
    if (text.toLowerCase() === wanted) score = 0;
    else if (text.toLowerCase().includes(wanted)) score = 1;
    if (!(new RegExp('project|g-p-|/g/', 'i')).test(href)) score += 10;
    return {{text, href, score}};
  }}).filter(x => x.href && x.score < 12).sort((a, b) => a.score - b.score);
  return rows[0] || null;
}})())
""",
        timeout=8,
    )
    data = json.loads(raw or "null")
    return data.get("href") if isinstance(data, dict) else None


async def open_project(page: CDPPage, project_name: str, cached_url: str | None = None) -> str:
    candidates = [cached_url] if cached_url else []
    if not cached_url:
        candidates.append(await find_project_url(page, project_name))
    for url in [u for u in candidates if u]:
        try:
            await page.navigate(url)
            if await page.wait_for_input(25):
                return url
        except Exception:
            pass

    # Return to the home sidebar and resolve the project link again. This also
    # handles stale cached URLs after ChatGPT changes a project route.
    await page.navigate("https://chatgpt.com/")
    await page.wait_for_input(20)
    project_url = await find_project_url(page, project_name)
    if not project_url:
        raise RuntimeError(f'Could not find the ChatGPT project "{project_name}" in the sidebar')
    await page.navigate(project_url)
    if not await page.wait_for_input(30):
        raise RuntimeError(f'Project "{project_name}" opened, but its new-chat composer did not appear')
    return project_url


async def find_conversation_by_session(page: CDPPage, session_id: str) -> dict | None:
    """Recover a conversation from ChatGPT's own list using the id in its title."""
    session_json = json.dumps(session_id)
    raw = await page.js(
        f"""
(async () => {{
  const needle = {session_json};
  for (let offset = 0; offset < 1000; offset += 100) {{
    let response;
    try {{
      response = await fetch(`/backend-api/conversations?offset=${{offset}}&limit=100&order=updated`);
    }} catch (_) {{ return null; }}
    if (!response.ok) return null;
    const data = await response.json();
    const items = data.items || data.conversations || [];
    const hit = items.find(item => String(item.title || '').includes(needle));
    if (hit) return {{id: hit.id || hit.conversation_id, title: hit.title || null}};
    if (!data.has_missing_conversations && items.length < 100) break;
  }}
  return null;
}})()
""",
        timeout=30,
        await_promise=True,
    )
    return raw if isinstance(raw, dict) and raw.get("id") else None


async def open_conversation(page: CDPPage, conversation_id: str) -> bool:
    await page.navigate(f"https://chatgpt.com/c/{conversation_id}")
    if not await page.wait_for_input(30):
        return False
    current_id, _ = await page.current_conversation()
    return current_id == conversation_id


async def _open_named_picker(page: CDPPage, kind: str) -> bool:
    kind_json = json.dumps(kind.lower())
    return bool(
        await page.js(
            f"""
(() => {{
  const kind = {kind_json};
  const visible = el => {{
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  }};
  const text = el => [el.getAttribute('aria-label'), el.getAttribute('title'), el.textContent]
    .filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim().toLowerCase();
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter(visible);
  let candidates;
  if (kind === 'model') {{
    candidates = buttons.filter(el => /model selector|switch model|select model|choose model|change model/.test(text(el)));
    if (!candidates.length) candidates = buttons.filter(el => /model/.test(text(el)) && text(el).length < 80);
  }} else {{
    candidates = buttons.filter(el => /reasoning effort|thinking effort|effort|thinking time|compute/.test(text(el)));
  }}
  const target = candidates[0];
  if (!target) return false;
  target.scrollIntoView({{block: 'center'}});
  target.click();
  return true;
}})()
""",
            timeout=8,
        )
    )


async def _choose_visible_option(page: CDPPage, search_term: str) -> str | None:
    search_json = json.dumps(search_term)
    raw = await page.js(
        f"""
JSON.stringify((() => {{
  const wanted = {search_json}.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const tokens = wanted.split(' ').filter(Boolean);
  const visible = el => {{
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  }};
  const nodes = Array.from(document.querySelectorAll('[role="menuitem"], [role="menuitemradio"], [role="option"], [role="dialog"] button'))
    .filter(visible)
    .map((el, index) => {{
      const label = (el.textContent || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
      const norm = label.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
      let rank = 99;
      if (norm === wanted) rank = 0;
      else if (norm.startsWith(wanted)) rank = 1;
      else if (norm.includes(wanted)) rank = 2;
      else if (tokens.length && tokens.every(t => norm.includes(t))) rank = 3;
      return {{el, label, rank, index}};
    }})
    .filter(x => x.label && x.rank < 99)
    .sort((a, b) => a.rank - b.rank || a.label.length - b.label.length || a.index - b.index);
  const target = nodes[0];
  if (!target) return null;
  target.el.scrollIntoView({{block: 'nearest'}});
  target.el.click();
  return target.label;
}})())
""",
        timeout=8,
    )
    return json.loads(raw or "null")


async def select_model(page: CDPPage, model: str) -> str:
    if not await _open_named_picker(page, "model"):
        raise RuntimeError("Could not find ChatGPT's model selector")
    await asyncio.sleep(0.5)
    selected = await _choose_visible_option(page, model)
    if not selected:
        await page.js("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        raise RuntimeError(f'Could not select ChatGPT model "{model}"')
    await asyncio.sleep(0.7)
    return selected


async def select_effort(page: CDPPage, effort: str) -> str:
    # Some UI builds leave the effort submenu open after model selection.
    selected = await _choose_visible_option(page, effort)
    if selected:
        await asyncio.sleep(0.4)
        return selected
    if not await _open_named_picker(page, "effort"):
        raise RuntimeError("Could not find ChatGPT's reasoning-effort selector")
    await asyncio.sleep(0.4)
    selected = await _choose_visible_option(page, effort)
    if not selected:
        await page.js("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        raise RuntimeError(f'Could not select ChatGPT reasoning effort "{effort}"')
    await asyncio.sleep(0.4)
    return selected


async def rename_conversation(page: CDPPage, conversation_id: str, title: str) -> bool:
    conversation_json = json.dumps(conversation_id)
    title_json = json.dumps(title)
    result = await page.js(
        f"""
(async () => {{
  try {{
    const response = await fetch('/backend-api/conversation/' + {conversation_json}, {{
      method: 'PATCH',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{title: {title_json}}}),
    }});
    if (response.ok) {{ document.title = {title_json} + ' - ChatGPT'; return true; }}
  }} catch (_) {{}}
  return false;
}})()
""",
        timeout=15,
        await_promise=True,
    )
    if result:
        return True

    # DOM fallback for UI versions that no longer expose the backend route.
    return bool(
        await page.js(
            f"""
(async () => {{
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const path = location.pathname;
  const link = document.querySelector('a[href="' + path + '"]');
  const box = link && (link.closest('li') || link.parentElement);
  const menuButton = box && Array.from(box.querySelectorAll('button')).find(b =>
    /more|option|menu/i.test([b.getAttribute('aria-label'), b.getAttribute('title')].filter(Boolean).join(' '))
  );
  if (!menuButton) return false;
  menuButton.click();
  await sleep(300);
  const rename = Array.from(document.querySelectorAll('[role="menuitem"], button')).find(el =>
    /^rename$/i.test((el.textContent || '').trim())
  );
  if (!rename) return false;
  rename.click();
  await sleep(300);
  const input = document.querySelector('[role="dialog"] input, input[placeholder*="rename" i]');
  if (!input) return false;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, {title_json});
  input.dispatchEvent(new Event('input', {{bubbles: true}}));
  input.dispatchEvent(new Event('change', {{bubbles: true}}));
  const save = Array.from(document.querySelectorAll('[role="dialog"] button')).find(el =>
    /save|rename/i.test((el.textContent || '').trim())
  );
  if (save) save.click();
  else input.dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter', code:'Enter', bubbles:true}}));
  await sleep(400);
  return true;
}})()
""",
            timeout=15,
            await_promise=True,
        )
    )


async def set_prompt(page: CDPPage, prompt: str) -> None:
    escaped = json.dumps(prompt)
    ok = await page.js(
        f"""
(() => {{
  let input = document.querySelector('#prompt-textarea');
  if (!input) input = document.querySelector('div[contenteditable="true"][role="textbox"]');
  if (!input) input = document.querySelector('div.ProseMirror[contenteditable]');
  if (!input) return false;
  input.focus();
  document.execCommand('selectAll', false, null);
  document.execCommand('delete', false, null);
  document.execCommand('insertText', false, {escaped});
  input.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: {escaped}}}));
  input.dispatchEvent(new Event('change', {{bubbles: true}}));
  return true;
}})()
""",
        timeout=10,
    )
    if not ok:
        raise RuntimeError("Could not find ChatGPT input box")


async def send_current_prompt(page: CDPPage) -> None:
    sent = await page.js(
        """
(() => {
  const button = document.querySelector('#composer-submit-button, button[data-testid="send-button"], button[aria-label="Send prompt"]');
  if (button && !button.disabled) { button.click(); return 'button'; }
  const input = document.querySelector('#prompt-textarea, div[contenteditable="true"][role="textbox"]');
  if (!input) return null;
  input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true, cancelable:true}));
  return 'enter';
})()
""",
        timeout=8,
    )
    if not sent:
        raise RuntimeError("Could not send ChatGPT prompt")


async def wait_for_response(page: CDPPage, before_count: int, timeout_s: int) -> str:
    poll_s = 1.5
    deadline = time.monotonic() + timeout_s
    last_text = ""
    stable_count = 0
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_s)
        raw = await page.js(
            f"""
JSON.stringify((() => {{
  const assistants = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
  const generating = !!document.querySelector('[data-testid="stop-button"], [class*="result-streaming"]');
  if (assistants.length <= {before_count}) return {{found:false, generating, count:assistants.length}};
  const last = assistants[assistants.length - 1];
  const content = last.querySelector('.markdown, .prose') || last;
  const text = (content.textContent || '').trim();
  return {{found: text.length > 0, text: text.slice(0, 200000), generating, count:assistants.length}};
}})())
""",
            timeout=8,
        )
        data = json.loads(raw or "{}")
        text = data.get("text", "") if data.get("found") else ""
        if text:
            if text == last_text:
                stable_count += 1
            else:
                last_text = text
                stable_count = 0
            if not data.get("generating") and stable_count >= 1:
                return text
        else:
            stable_count = 0
    if last_text:
        return last_text + "\n\n[WARNING: timed out waiting for completion]"
    info = await page.page_info()
    raise RuntimeError(f"Timed out waiting for ChatGPT response. Page state: {info}")


async def send_prompt(
    prompt: str,
    timeout_s: int = DEFAULT_TIMEOUT,
    *,
    session_id: str,
    summary: str | None,
    project: str = DEFAULT_PROJECT,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    state_path: Path = STATE_FILE,
    force_new: bool = False,
) -> str:
    page_id, _ = find_chatgpt_tab()
    if not page_id:
        raise RuntimeError("No ChatGPT tab found. Open https://chatgpt.com first.")

    state = load_state(state_path)
    sessions = state.setdefault("sessions", {})
    projects = state.setdefault("projects", {})
    entry = sessions.get(session_id) if isinstance(sessions.get(session_id), dict) else {}
    stored_summary = entry.get("summary") if entry else None
    chosen_summary = infer_summary(prompt, explicit=summary, stored=stored_summary)
    desired_title = build_conversation_title(chosen_summary, session_id)

    async with CDPPage(page_id) as page:
        stop = await page.js("!!document.querySelector('[data-testid=\"stop-button\"]')")
        if stop:
            await page.js("document.querySelector('[data-testid=\"stop-button\"]')?.click()")
            await asyncio.sleep(0.5)

        conversation_id = None
        existing_title = entry.get("conversation_title") if entry else None

        if force_new:
            sessions.pop(session_id, None)
            entry = {}
        else:
            candidate_id = entry.get("conversation_id") if entry else None
            if candidate_id:
                try:
                    if await open_conversation(page, candidate_id):
                        conversation_id = candidate_id
                except Exception:
                    conversation_id = None

            if not conversation_id:
                recovered = await find_conversation_by_session(page, session_id)
                if recovered:
                    try:
                        if await open_conversation(page, recovered["id"]):
                            conversation_id = recovered["id"]
                            existing_title = recovered.get("title")
                            if not summary and not stored_summary:
                                recovered_summary = title_summary(existing_title, session_id)
                                if recovered_summary:
                                    chosen_summary = recovered_summary
                                    desired_title = build_conversation_title(chosen_summary, session_id)
                    except Exception:
                        conversation_id = None

        project_url = projects.get(project)
        if not conversation_id:
            project_url = await open_project(page, project, project_url)
            projects[project] = project_url
            save_state(state, state_path)

        # Enforce the requested model and effort for every turn. Existing chats can
        # retain stale UI selections, so this is intentionally not a one-time setup.
        selected_model = await select_model(page, model)
        selected_effort = await select_effort(page, effort)

        before_count = int(
            await page.js("document.querySelectorAll('[data-message-author-role=\"assistant\"]').length", timeout=5)
            or 0
        )
        await set_prompt(page, prompt)
        await asyncio.sleep(0.4)
        await send_current_prompt(page)
        response = await wait_for_response(page, before_count, timeout_s)

        # A new conversation receives its id only after the first message is sent.
        for _ in range(20):
            current_id, current_title = await page.current_conversation()
            if current_id:
                conversation_id = current_id
                existing_title = current_title or existing_title
                break
            await asyncio.sleep(0.5)
        if not conversation_id:
            raise RuntimeError("ChatGPT responded but no conversation id appeared in the URL")

        renamed = True
        if existing_title != desired_title:
            renamed = await rename_conversation(page, conversation_id, desired_title)

        sessions[session_id] = {
            "conversation_id": conversation_id,
            "conversation_title": desired_title if renamed else (existing_title or desired_title),
            "summary": chosen_summary,
            "project": project,
            "project_url": project_url,
            "model": selected_model,
            "effort": selected_effort,
            "updated_at": int(time.time()),
        }
        save_state(state, state_path)

        if not renamed:
            print(
                f'WARNING: conversation {conversation_id} could not be renamed to "{desired_title}"; '
                "the local session mapping was still saved.",
                file=sys.stderr,
            )
        print(f"[conversation_id: {conversation_id}]", file=sys.stderr)
        print(f"[conversation_title: {sessions[session_id]['conversation_title']}]", file=sys.stderr)
        print(f"[model: {selected_model}; effort: {selected_effort}; project: {project}]", file=sys.stderr)
        return response


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Send prompts to ChatGPT via CDP")
    parser.add_argument("prompt", nargs="?", help="Prompt text")
    parser.add_argument("--file", "-f", help="Read prompt from file")
    parser.add_argument("--timeout", "-t", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--session-id", "-s", default=None, help="Hermes session id (defaults to HERMES_SESSION_ID)")
    parser.add_argument("--summary", help="Human-readable Hermes session summary")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help=f'ChatGPT project (default: "{DEFAULT_PROJECT}")')
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f'ChatGPT model (default: "{DEFAULT_MODEL}")')
    parser.add_argument("--effort", default=DEFAULT_EFFORT, help=f'Reasoning effort (default: "{DEFAULT_EFFORT}")')
    parser.add_argument("--state-file", default=str(STATE_FILE), help="Persistent session map")
    parser.add_argument("--new", action="store_true", help="Start a new project chat for this Hermes session")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.timeout <= 0:
        raise SystemExit("--timeout must be a positive integer")

    if args.file:
        prompt = Path(args.file).read_text()
    elif args.prompt:
        prompt = args.prompt
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read()
    else:
        parse_args(["--help"])
        return 1

    if not prompt.strip():
        raise SystemExit("Prompt is empty")

    session_id = resolve_session_id(args.session_id)
    explicit_summary = args.summary or os.getenv("HERMES_SESSION_SUMMARY") or os.getenv("HERMES_SUMMARY")
    state_path = Path(args.state_file).expanduser()

    print(
        f'Sending prompt with model="{args.model}", effort="{args.effort}", '
        f'project="{args.project}", session="{session_id}"...',
        file=sys.stderr,
    )
    with process_lock(state_path):
        response = asyncio.run(
            send_prompt(
                prompt,
                args.timeout,
                session_id=session_id,
                summary=explicit_summary,
                project=args.project,
                model=args.model,
                effort=args.effort,
                state_path=state_path,
                force_new=args.new,
            )
        )
    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
