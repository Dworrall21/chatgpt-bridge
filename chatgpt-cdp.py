#!/usr/bin/env python3
"""
Completion-aware entrypoint for the session-aware ChatGPT CDP client.

The implementation lives in ``chatgpt_cdp_impl.py``. This wrapper replaces
``wait_for_response`` so callers receive an answer only after ChatGPT renders
the completed-turn action controls beneath the newest assistant message.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import time


_IMPL_PATH = Path(__file__).with_name("chatgpt_cdp_impl.py")
_SPEC = importlib.util.spec_from_file_location("chatgpt_cdp_impl", _IMPL_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load ChatGPT CDP implementation from {_IMPL_PATH}")
_impl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_impl)

# Preserve the original public API for imports and existing tests.
for _name, _value in vars(_impl).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


def response_snapshot_complete(snapshot: dict, stable_count: int) -> bool:
    """Return true only after ChatGPT has rendered a completed assistant turn."""
    actions = snapshot.get("actions") or {}
    return bool(
        snapshot.get("found")
        and not snapshot.get("generating")
        and actions.get("copy")
        and actions.get("good")
        and actions.get("bad")
        and stable_count >= 1
    )


async def wait_for_response(page, before_count: int, timeout_s: int) -> str:
    """Wait for the newest response's action bar before returning its text.

    Copy, thumbs-up, and thumbs-down controls are scoped to the newest assistant
    turn. Their presence is the primary completion signal; text stability and
    the absence of an active generation indicator provide secondary guards.
    """
    poll_s = 1.0
    deadline = time.monotonic() + timeout_s
    last_text = ""
    stable_count = 0
    last_snapshot: dict = {}

    while time.monotonic() < deadline:
        await asyncio.sleep(poll_s)
        raw = await page.js(
            f"""
JSON.stringify((() => {{
  const assistants = Array.from(
    document.querySelectorAll('[data-message-author-role="assistant"]')
  );
  const generating = !!document.querySelector(
    '[data-testid="stop-button"], [class*="result-streaming"]'
  );
  const emptyActions = {{copy:false, good:false, bad:false}};

  if (assistants.length <= {before_count}) {{
    return {{
      found: false,
      generating,
      count: assistants.length,
      actions: emptyActions
    }};
  }}

  const last = assistants[assistants.length - 1];
  const turn =
    last.closest('[data-testid^="conversation-turn-"], article') ||
    last.parentElement ||
    last;
  const content = last.querySelector('.markdown, .prose') || last;
  const text = (content.textContent || '').trim();
  const buttons = Array.from(
    turn.querySelectorAll('button, [role="button"]')
  );

  const normalize = value =>
    String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();

  const values = button => [
    button.getAttribute('data-testid'),
    button.getAttribute('aria-label'),
    button.getAttribute('title'),
  ].map(normalize).filter(Boolean);

  const hasAction = (testids, labels) => buttons.some(button =>
    values(button).some(value =>
      testids.some(testid => value.includes(testid)) ||
      labels.includes(value)
    )
  );

  const actions = {{
    copy: hasAction(
      ['copy-turn-action-button'],
      ['copy', 'copy response', 'copy message', 'copy answer']
    ),
    good: hasAction(
      ['good-response-turn-action-button'],
      ['good response', 'thumbs up', 'like', 'helpful']
    ),
    bad: hasAction(
      ['bad-response-turn-action-button'],
      ['bad response', 'thumbs down', 'dislike', 'not helpful']
    ),
  }};

  return {{
    found: text.length > 0,
    text: text.slice(0, 200000),
    generating,
    count: assistants.length,
    actions,
  }};
}})())
""",
            timeout=8,
        )

        data = json.loads(raw or "{}")
        last_snapshot = data
        text = data.get("text", "") if data.get("found") else ""

        if text:
            if text == last_text:
                stable_count += 1
            else:
                last_text = text
                stable_count = 0

            if response_snapshot_complete(data, stable_count):
                return text
        else:
            stable_count = 0

    info = await page.page_info()
    completion = {
        "page": info,
        "assistant_count": last_snapshot.get("count"),
        "generating": last_snapshot.get("generating"),
        "actions": last_snapshot.get("actions"),
        "has_text": bool(last_text),
    }
    raise RuntimeError(
        "Timed out waiting for ChatGPT's completed-turn action bar; "
        "refusing to return a partial response to the aggregator. "
        f"Last state: {completion}"
    )


# send_prompt resolves this global from the implementation module at runtime.
_impl.wait_for_response = wait_for_response


if __name__ == "__main__":
    raise SystemExit(_impl.main())
