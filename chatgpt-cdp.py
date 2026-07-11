#!/usr/bin/env python3
"""
chatgpt-cdp.py — Send prompts to ChatGPT via Chrome CDP.

Hermes-aware defaults:
  * model: GPT-5.6 Sol
  * reasoning effort: High
  * one ChatGPT conversation per HERMES_SESSION_ID
  * conversations are created inside the MoA ChatGPT project
  * title: "<Hermes session summary> — <Hermes session id>"

Usage:
    python3 chatgpt-cdp.py "Your prompt here"
    python3 chatgpt-cdp.py --file prompt.txt
    echo "prompt" | python3 chatgpt-cdp.py

Environment:
    HERMES_SESSION_ID          Durable Hermes session identifier.
    HERMES_SESSION_SUMMARY     Preferred title/summary for the Hermes session.
    CHATGPT_PROJECT_URL        Direct URL for the MoA project (preferred).
    CHATGPT_PROJECT_NAME       Project sidebar label (default: MoA).
    CHATGPT_CDP_MODEL          Model picker search text (default: 5.6 sol).
    CHATGPT_CDP_EFFORT         Reasoning effort (default: high).
    CHATGPT_CDP_STATE_FILE     Session mapping file.
    HERMES_STATE_DB            Hermes SQLite state database.

Requirements:
    Chrome running with --remote-debugging-port=9222
    Signed in to ChatGPT
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

import websockets

CDP_PORT = int(os.getenv("CHATGPT_CDP_PORT", "9222"))
DEFAULT_MODEL = os.getenv("CHATGPT_CDP_MODEL", "5.6 sol").strip() or "5.6 sol"
DEFAULT_EFFORT = os.getenv("CHATGPT_CDP_EFFORT", "high").strip() or "high"
DEFAULT_PROJECT_NAME = os.getenv("CHATGPT_PROJECT_NAME", "MoA").strip() or "MoA"
DEFAULT_TIMEOUT = int(os.getenv("CHATGPT_CDP_TIMEOUT", "600"))
DEFAULT_STATE_FILE = Path(
    os.getenv(
        "CHATGPT_CDP_STATE_FILE",
        str(Path.home() / ".hermes" / "chatgpt_bridge_state" / "cdp_sessions.json"),
    )
).expanduser()
DEFAULT_HERMES_DB = Path(
    os.getenv("HERMES_STATE_DB", str(Path.home() / ".hermes" / "state.db"))
).expanduser()


def _clean_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()


def _content_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            with contextlib.suppress(json.JSONDecodeError):
                return _content_to_text(json.loads(stripped))
        return stripped
    if isinstance(value, list):
        return " ".join(filter(None, (_content_to_text(item) for item in value)))
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            if key in value:
                text = _content_to_text(value[key])
                if text:
                    return text
        return ""
    return str(value)


def read_hermes_session_summary(session_id: str, db_path: Path = DEFAULT_HERMES_DB) -> str:
    """Read the best available Hermes summary/title without importing Hermes."""
    if not session_id or not db_path.exists():
        return ""

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return ""

    try:
        session_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        candidate_columns = [
            name for name in ("summary", "title", "name") if name in session_columns
        ]
        if candidate_columns:
            sql = f"SELECT {', '.join(candidate_columns)} FROM sessions WHERE id = ? LIMIT 1"
            row = conn.execute(sql, (session_id,)).fetchone()
            if row:
                for name in candidate_columns:
                    value = _clean_text(row[name])
                    if value:
                        return value

        message_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if {"session_id", "role", "content"}.issubset(message_columns):
            order_column = next(
                (name for name in ("timestamp", "created_at", "id") if name in message_columns),
                None,
            )
            order_sql = f" ORDER BY {order_column} ASC" if order_column else ""
            row = conn.execute(
                "SELECT content FROM messages "
                "WHERE session_id = ? AND role = 'user'" + order_sql + " LIMIT 1",
                (session_id,),
            ).fetchone()
            if row:
                return _clean_text(_content_to_text(row["content"]))
    except sqlite3.Error:
        return ""
    finally:
        conn.close()
    return ""


def resolve_session_summary(session_id: str, prompt: str, explicit: str | None = None) -> str:
    candidates = [
        explicit,
        os.getenv("HERMES_SESSION_SUMMARY"),
        os.getenv("HERMES_SESSION_TITLE"),
        os.getenv("HERMES_SESSION_NAME"),
        os.getenv("HERMES_SESSION_CHAT_NAME"),
        read_hermes_session_summary(session_id),
        prompt,
        "Hermes session",
    ]
    for candidate in candidates:
        cleaned = _clean_text(candidate)
        if cleaned:
            return cleaned
    return "Hermes session"


def build_conversation_title(summary: str, session_id: str, max_length: int = 100) -> str:
    sid = _clean_text(session_id) or "manual"
    suffix = f" — {sid}"
    available = max(1, max_length - len(suffix))
    clean_summary = _clean_text(summary) or "Hermes session"
    if len(clean_summary) > available:
        clean_summary = clean_summary[: max(1, available - 1)].rstrip(" -–—:;,.") + "…"
    return clean_summary + suffix


def _empty_state() -> dict[str, Any]:
    return {"version": 1, "sessions": {}}


def _load_state_unlocked(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        data["sessions"] = {}
    data.setdefault("version", 1)
    return data


def _with_state_lock(path: Path, callback: Callable[[dict[str, Any]], Any], write: bool) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        state = _load_state_unlocked(path)
        result = callback(state)
        if write:
            fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
            try:
                with os.fdopen(fd, "w") as tmp:
                    json.dump(state, tmp, indent=2, sort_keys=True)
                    tmp.write("\n")
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(tmp_name, path)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp_name)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return result


def get_session_mapping(session_id: str, path: Path = DEFAULT_STATE_FILE) -> dict[str, Any] | None:
    def read(state: dict[str, Any]) -> dict[str, Any] | None:
        value = state.get("sessions", {}).get(session_id)
        return dict(value) if isinstance(value, dict) else None

    return _with_state_lock(path, read, write=False)


def save_session_mapping(
    session_id: str,
    mapping: dict[str, Any],
    path: Path = DEFAULT_STATE_FILE,
) -> None:
    def update(state: dict[str, Any]) -> None:
        sessions = state.setdefault("sessions", {})
        previous = sessions.get(session_id)
        merged = dict(previous) if isinstance(previous, dict) else {}
        merged.update(mapping)
        merged["updated_at"] = int(time.time())
        sessions[session_id] = merged

    _with_state_lock(path, update, write=True)


def delete_session_mapping(session_id: str, path: Path = DEFAULT_STATE_FILE) -> None:
    def update(state: dict[str, Any]) -> None:
        state.setdefault("sessions", {}).pop(session_id, None)

    _with_state_lock(path, update, write=True)


def find_chatgpt_tab(preferred_url: str | None = None) -> str | None:
    req = urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3)
    tabs = json.loads(req.read())
    pages = [
        tab
        for tab in tabs
        if tab.get("type", "page") == "page" and "chatgpt.com" in tab.get("url", "")
    ]
    if preferred_url:
        preferred_path = preferred_url.split("chatgpt.com", 1)[-1].rstrip("/")
        for tab in pages:
            if preferred_path and preferred_path in tab.get("url", ""):
                return tab.get("id")
    return pages[0].get("id") if pages else None


async def send_prompt(
    prompt: str,
    *,
    timeout_s: int = DEFAULT_TIMEOUT,
    session_id: str,
    summary: str,
    project_url: str | None,
    project_name: str,
    model: str,
    effort: str,
    state_file: Path = DEFAULT_STATE_FILE,
    force_new: bool = False,
) -> tuple[str, dict[str, Any]]:
    title = build_conversation_title(summary, session_id)
    mapping = None if force_new else get_session_mapping(session_id, state_file)
    preferred_url = (mapping or {}).get("conversation_url") or project_url
    page_id = find_chatgpt_tab(preferred_url)
    if not page_id:
        raise RuntimeError("No ChatGPT tab found. Open https://chatgpt.com first.")

    ws_url = f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{page_id}"
    async with websockets.connect(ws_url, max_size=2**22) as ws:
        request_id = 0

        async def js(expr: str, timeout: float = 15) -> Any:
            nonlocal request_id
            request_id += 1
            rid = request_id
            await ws.send(
                json.dumps(
                    {
                        "id": rid,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": expr,
                            "returnByValue": True,
                            "awaitPromise": True,
                            "timeout": int(timeout * 1000),
                        },
                    }
                )
            )
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout + 5)
                data = json.loads(raw)
                if data.get("id") != rid:
                    continue
                error = data.get("error")
                if error:
                    raise RuntimeError(error.get("message", "CDP evaluation failed"))
                result = data.get("result", {})
                exc = result.get("exceptionDetails")
                if exc:
                    description = (
                        exc.get("exception", {}).get("description")
                        or exc.get("text")
                        or "CDP eval error"
                    )
                    raise RuntimeError(description)
                return result.get("result", {}).get("value")

        async def wait_for_input(timeout: float = 20) -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if await js("!!document.querySelector('#prompt-textarea, div[contenteditable=\"true\"][role=\"textbox\"]')", 5):
                    return
                await asyncio.sleep(0.5)
            raise RuntimeError("ChatGPT input box did not appear after navigation")

        async def navigate(url: str) -> None:
            await js(f"location.href = {json.dumps(url)}; true")
            await asyncio.sleep(1)
            await wait_for_input(30)

        await js(
            """
            (() => {
              const stop = document.querySelector('[data-testid="stop-button"]');
              if (stop) stop.click();
              return true;
            })()
            """
        )
        await asyncio.sleep(0.5)

        conversation_found = False
        if mapping and mapping.get("conversation_url"):
            try:
                await navigate(str(mapping["conversation_url"]))
                expected_id = mapping.get("conversation_id")
                current_id = await js("(location.pathname.match(/\\/c\\/([^/?]+)/)||[])[1] || null")
                conversation_found = not expected_id or current_id == expected_id
            except Exception as exc:
                print(f"Warning: saved ChatGPT conversation could not be opened: {exc}", file=sys.stderr)
                delete_session_mapping(session_id, state_file)
                mapping = None

        if not conversation_found:
            if project_url:
                await navigate(project_url)
            else:
                project_result = await js(
                    f"""
                    (() => {{
                      const wanted = {json.dumps(project_name)}.trim().toLowerCase();
                      const links = [...document.querySelectorAll('a[href*="/g/g-p-"], a[href*="/project"]')];
                      const match = links.find(a => {{
                        const text = (a.textContent || a.getAttribute('aria-label') || a.title || '')
                          .replace(/\\s+/g, ' ').trim().toLowerCase();
                        return text === wanted || text.startsWith(wanted + ' ');
                      }});
                      if (!match) return null;
                      const href = match.href;
                      match.click();
                      return href;
                    }})()
                    """
                )
                if not project_result:
                    raise RuntimeError(
                        f'ChatGPT project "{project_name}" was not found. Set CHATGPT_PROJECT_URL to its direct URL.'
                    )
                project_url = str(project_result)
                await asyncio.sleep(1)
                await wait_for_input(30)

            # Recovery path: the title contains the full Hermes session id, so an
            # existing project conversation can be rediscovered if the local map
            # is deleted.
            recovered_url = None
            if not force_new:
                recovered_url = await js(
                    f"""
                    (() => {{
                      const sid = {json.dumps(session_id)};
                      const links = [...document.querySelectorAll('a[href*="/c/"]')];
                      const match = links.find(a => (a.textContent || '').includes(sid));
                      if (!match) return null;
                      const href = match.href;
                      match.click();
                      return href;
                    }})()
                    """
                )
            if recovered_url:
                await asyncio.sleep(1)
                await wait_for_input(30)
                conversation_found = True
            else:
                # Project home pages already provide the composer. Sending from
                # this page creates the new conversation inside the project.
                current_path = await js("location.pathname")
                if "/c/" in str(current_path):
                    await navigate(project_url)

        selection_script = f"""
        (async () => {{
          const sleep = ms => new Promise(r => setTimeout(r, ms));
          const norm = value => String(value || '').toLowerCase().replace(/[^a-z0-9.]+/g, ' ').replace(/\\s+/g, ' ').trim();
          const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
          const label = el => [el.getAttribute?.('aria-label'), el.getAttribute?.('title'), el.textContent]
            .filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
          const click = el => {{
            el.scrollIntoView?.({{block: 'center'}});
            el.focus?.();
            el.dispatchEvent(new PointerEvent('pointerdown', {{bubbles:true, cancelable:true, pointerId:1, pointerType:'mouse', isPrimary:true}}));
            el.dispatchEvent(new MouseEvent('mousedown', {{bubbles:true, cancelable:true, button:0}}));
            el.dispatchEvent(new MouseEvent('mouseup', {{bubbles:true, cancelable:true, button:0}}));
            el.click();
          }};
          const score = (wanted, candidate) => {{
            const w = norm(wanted), c = norm(candidate);
            if (!w || !c) return null;
            if (c === w) return [0, c.length];
            if (c.startsWith(w)) return [1, c.length];
            if (c.includes(w)) return [2, c.length];
            const tokens = w.split(' ').filter(Boolean);
            if (tokens.every(token => c.includes(token))) return [3, c.length];
            return null;
          }};
          async function choose(kind, wanted) {{
            const buttons = [...document.querySelectorAll('button, [role="button"]')].filter(visible);
            let opener = null;
            if (kind === 'model') {{
              opener = buttons.find(el => /model selector|switch model|select model|choose model|change model/.test(norm(label(el))));
              if (!opener) opener = buttons.find(el => norm(label(el)).includes('model'));
            }} else {{
              const current = buttons.find(el => norm(label(el)) === norm(wanted));
              if (current) return {{ok:true, selected:label(current), already:true}};
              opener = buttons.find(el => /reasoning effort|thinking effort|reasoning time|thinking time|compute|effort/.test(norm(label(el))));
              if (!opener) opener = buttons.find(el => /^(low|medium|high|max|maximum)$/.test(norm(label(el))));
            }}
            if (!opener) return {{ok:false, error:`${{kind}} selector not found`}};
            click(opener);
            await sleep(500);

            const search = [...document.querySelectorAll('input')].find(el => visible(el) && /search/.test(norm(label(el) + ' ' + el.placeholder)));
            if (search && kind === 'model') {{
              const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
              if (setter) setter.call(search, wanted); else search.value = wanted;
              search.dispatchEvent(new Event('input', {{bubbles:true}}));
              search.dispatchEvent(new Event('change', {{bubbles:true}}));
              await sleep(500);
            }}

            const candidates = [...document.querySelectorAll('[role="menuitem"], [role="menuitemradio"], [role="option"], [role="dialog"] button')]
              .filter(visible)
              .map((el, index) => ({{el, index, text:label(el), rank:score(wanted, label(el))}}))
              .filter(item => item.rank)
              .sort((a,b) => a.rank[0]-b.rank[0] || a.rank[1]-b.rank[1] || a.index-b.index);
            if (!candidates.length) {{
              document.dispatchEvent(new KeyboardEvent('keydown', {{key:'Escape', bubbles:true}}));
              return {{ok:false, error:`${{kind}} option "${{wanted}}" not found`}};
            }}
            const selected = candidates[0];
            click(selected.el);
            await sleep(500);
            return {{ok:true, selected:selected.text}};
          }}
          const modelResult = await choose('model', {json.dumps(model)});
          if (!modelResult.ok) return {{ok:false, stage:'model', ...modelResult}};
          const effortResult = await choose('effort', {json.dumps(effort)});
          if (!effortResult.ok) return {{ok:false, stage:'effort', model:modelResult, ...effortResult}};
          return {{ok:true, model:modelResult.selected, effort:effortResult.selected}};
        }})()
        """
        selection = await js(selection_script, 25)
        if not isinstance(selection, dict) or not selection.get("ok"):
            raise RuntimeError(f"Could not enforce model/effort selection: {selection}")

        before = await js(
            """
            (() => {
              const assistants = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
              const users = document.querySelectorAll('[data-message-author-role="user"]').length;
              const last = assistants.at(-1);
              return {
                assistants: assistants.length,
                users,
                lastText: (last?.querySelector('.markdown, .prose')?.textContent || last?.textContent || '').trim()
              };
            })()
            """
        )

        escaped_prompt = json.dumps(prompt)
        input_ok = await js(
            f"""
            (() => {{
              const input = document.querySelector('#prompt-textarea, div[contenteditable="true"][role="textbox"], div.ProseMirror[contenteditable], textarea');
              if (!input) return false;
              input.focus();
              if (input instanceof HTMLTextAreaElement || input instanceof HTMLInputElement) {{
                const proto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (setter) setter.call(input, {escaped_prompt}); else input.value = {escaped_prompt};
              }} else {{
                document.execCommand('selectAll', false, null);
                document.execCommand('delete', false, null);
                document.execCommand('insertText', false, {escaped_prompt});
              }}
              input.dispatchEvent(new InputEvent('input', {{bubbles:true, inputType:'insertText', data:{escaped_prompt}}}));
              input.dispatchEvent(new Event('change', {{bubbles:true}}));
              return true;
            }})()
            """
        )
        if not input_ok:
            raise RuntimeError("Could not find ChatGPT input box")
        await asyncio.sleep(0.5)

        sent = await js(
            """
            (() => {
              const button = document.querySelector('#composer-submit-button, button[data-testid="send-button"], button[aria-label="Send prompt"]');
              if (button && !button.disabled) { button.click(); return 'button'; }
              const input = document.querySelector('#prompt-textarea, div[contenteditable="true"][role="textbox"]');
              if (!input) return null;
              input.dispatchEvent(new KeyboardEvent('keydown', {
                key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true, cancelable:true
              }));
              return 'enter';
            })()
            """
        )
        if not sent:
            raise RuntimeError("Could not send prompt")

        deadline = time.monotonic() + timeout_s
        last_text = ""
        stable_count = 0
        response_text = ""
        while time.monotonic() < deadline:
            await asyncio.sleep(1)
            result = await js(
                """
                (() => {
                  const assistants = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
                  const users = document.querySelectorAll('[data-message-author-role="user"]').length;
                  const last = assistants.at(-1);
                  const text = (last?.querySelector('.markdown, .prose')?.textContent || last?.textContent || '').trim();
                  const generating = !!document.querySelector('[data-testid="stop-button"], [class*="result-streaming"]');
                  return {assistants:assistants.length, users, text, generating, url:location.href};
                })()
                """,
                5,
            )
            if not isinstance(result, dict):
                continue
            is_new = (
                result.get("assistants", 0) > int((before or {}).get("assistants", 0))
                or (
                    result.get("text")
                    and result.get("text") != (before or {}).get("lastText", "")
                    and result.get("users", 0) > int((before or {}).get("users", 0))
                )
            )
            if not is_new or not result.get("text"):
                continue
            response_text = str(result["text"])
            if response_text == last_text:
                stable_count += 1
            else:
                stable_count = 0
                last_text = response_text
            if not result.get("generating") and stable_count >= 1:
                break
        else:
            if not response_text:
                diag = await js(
                    "JSON.stringify({url:location.href, hasInput:!!document.querySelector('#prompt-textarea'), stop:!!document.querySelector('[data-testid=\"stop-button\"]')})"
                )
                raise RuntimeError(f"Timed out waiting for ChatGPT response. Page state: {diag}")
            response_text += "\n\n[WARNING: timed out before generation completed]"

        conversation = await js(
            """
            (() => {
              const match = location.pathname.match(/\/c\/([^/?]+)/);
              return {conversation_id: match ? match[1] : null, conversation_url: location.href};
            })()
            """
        )
        conversation_id = (conversation or {}).get("conversation_id")
        conversation_url = (conversation or {}).get("conversation_url")
        if not conversation_id:
            raise RuntimeError("Prompt completed, but ChatGPT conversation id was not found in the URL")

        rename_result = await js(
            f"""
            (async () => {{
              const sleep = ms => new Promise(r => setTimeout(r, ms));
              const wanted = {json.dumps(title)};
              const convId = {json.dumps(conversation_id)};
              const norm = value => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
              const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
              const setInput = (input, value) => {{
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                if (setter) setter.call(input, value); else input.value = value;
                input.dispatchEvent(new Event('input', {{bubbles:true}}));
                input.dispatchEvent(new Event('change', {{bubbles:true}}));
              }};

              const currentLink = [...document.querySelectorAll('a[href*="/c/"]')]
                .find(a => a.href.includes(convId));
              if (currentLink && norm(currentLink.textContent).includes(norm(wanted))) return {{ok:true, already:true}};

              const containers = [];
              if (currentLink) {{
                let node = currentLink;
                for (let i=0; node && i<6; i++, node=node.parentElement) containers.push(node);
              }}
              containers.push(document.querySelector('header'), document.querySelector('main'));
              let menuButton = null;
              for (const container of containers.filter(Boolean)) {{
                menuButton = [...container.querySelectorAll('button')].find(button => {{
                  const text = norm([button.getAttribute('aria-label'), button.title, button.textContent].filter(Boolean).join(' '));
                  return visible(button) && (/more|options|menu/.test(text) || button.getAttribute('aria-haspopup') === 'menu');
                }});
                if (menuButton) break;
              }}
              if (!menuButton) return {{ok:false, error:'conversation menu button not found'}};
              menuButton.click();
              await sleep(400);

              const rename = [...document.querySelectorAll('[role="menuitem"], [role="option"], button')]
                .find(el => visible(el) && /^rename( chat| conversation)?$/.test(norm(el.textContent || el.getAttribute('aria-label'))));
              if (!rename) return {{ok:false, error:'rename action not found'}};
              rename.click();
              await sleep(400);

              const input = [...document.querySelectorAll('input')].find(el => visible(el) && (
                el.closest('[role="dialog"]') || /rename|title/.test(norm(el.getAttribute('aria-label') + ' ' + el.placeholder))
              ));
              if (!input) return {{ok:false, error:'rename input not found'}};
              input.focus();
              setInput(input, wanted);
              await sleep(150);

              const dialog = input.closest('[role="dialog"]') || document;
              const save = [...dialog.querySelectorAll('button')].find(el => visible(el) && /^(save|rename|confirm|done)$/.test(norm(el.textContent || el.getAttribute('aria-label'))));
              if (save && !save.disabled) save.click();
              else input.dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter', code:'Enter', keyCode:13, bubbles:true}}));
              await sleep(500);
              return {{ok:true}};
            }})()
            """,
            15,
        )
        if not isinstance(rename_result, dict) or not rename_result.get("ok"):
            print(f"Warning: ChatGPT conversation rename failed: {rename_result}", file=sys.stderr)

        final_mapping = {
            "conversation_id": conversation_id,
            "conversation_url": conversation_url,
            "project_url": project_url,
            "project_name": project_name,
            "title": title,
            "model": model,
            "effort": effort,
        }
        save_session_mapping(session_id, final_mapping, state_file)
        return response_text, final_mapping


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send prompts to ChatGPT via CDP")
    parser.add_argument("prompt", nargs="?", help="Prompt text")
    parser.add_argument("--file", "-f", help="Read prompt from file")
    parser.add_argument("--timeout", "-t", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--session-id", default=os.getenv("HERMES_SESSION_ID") or "manual")
    parser.add_argument("--summary", help="Hermes session summary/title")
    parser.add_argument("--project-url", default=os.getenv("CHATGPT_PROJECT_URL"))
    parser.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--new", action="store_true", help="Forget this session's saved ChatGPT conversation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.file:
        prompt = Path(args.file).read_text()
    elif args.prompt:
        prompt = args.prompt
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read()
    else:
        print("No prompt supplied", file=sys.stderr)
        return 2

    if args.timeout <= 0:
        print("--timeout must be positive", file=sys.stderr)
        return 2
    prompt = prompt.strip()
    if not prompt:
        print("Prompt is empty", file=sys.stderr)
        return 2

    session_id = _clean_text(args.session_id) or "manual"
    summary = resolve_session_summary(session_id, prompt, args.summary)
    title = build_conversation_title(summary, session_id)
    print(
        f'Sending to ChatGPT project "{args.project_name}" as "{title}" '
        f'(model={args.model}, effort={args.effort})...',
        file=sys.stderr,
    )

    try:
        response, mapping = asyncio.run(
            send_prompt(
                prompt,
                timeout_s=args.timeout,
                session_id=session_id,
                summary=summary,
                project_url=args.project_url,
                project_name=args.project_name,
                model=args.model,
                effort=args.effort,
                state_file=args.state_file.expanduser(),
                force_new=args.new,
            )
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(response)
    print(f"[conversation_id: {mapping['conversation_id']}]", file=sys.stderr)
    print(f"[conversation_title: {mapping['title']}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
