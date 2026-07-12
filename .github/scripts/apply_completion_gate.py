from pathlib import Path

SOURCE_PATH = Path("chatgpt-cdp.py")
TESTS_PATH = Path("tests/test_chatgpt_cdp.py")

source = SOURCE_PATH.read_text()
start = source.index("async def wait_for_response(")
end = source.index("\n\nasync def send_prompt(", start)
replacement = '''def response_snapshot_complete(snapshot: dict, stable_count: int) -> bool:
    """Return true only when ChatGPT has rendered the completed-turn action bar."""
    actions = snapshot.get("actions") or {}
    return bool(
        snapshot.get("found")
        and not snapshot.get("generating")
        and actions.get("copy")
        and actions.get("good")
        and actions.get("bad")
        and stable_count >= 1
    )


async def wait_for_response(page: CDPPage, before_count: int, timeout_s: int) -> str:
    """Wait until the newest assistant turn is complete and safe to aggregate.

    ChatGPT renders copy, thumbs-up, and thumbs-down controls only after a
    response turn has finalized. Requiring all three controls prevents a
    temporarily stable streaming response from being returned early.
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
  const assistants = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
  const generating = !!document.querySelector('[data-testid="stop-button"], [class*="result-streaming"]');
  const emptyActions = {{copy:false, good:false, bad:false}};
  if (assistants.length <= {before_count}) {{
    return {{found:false, generating, count:assistants.length, actions:emptyActions}};
  }}

  const last = assistants[assistants.length - 1];
  const turn = last.closest('[data-testid^="conversation-turn-"], article') || last.parentElement || last;
  const content = last.querySelector('.markdown, .prose') || last;
  const text = (content.textContent || '').trim();
  const buttons = Array.from(turn.querySelectorAll('button, [role="button"]'));
  const normalize = value => String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const values = button => [
    button.getAttribute('data-testid'),
    button.getAttribute('aria-label'),
    button.getAttribute('title'),
  ].map(normalize).filter(Boolean);
  const hasAction = (testids, labels) => buttons.some(button =>
    values(button).some(value =>
      testids.some(testid => value.includes(testid)) || labels.includes(value)
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
'''
SOURCE_PATH.write_text(source[:start] + replacement + source[end:])

tests = TESTS_PATH.read_text()
marker = "def test_response_completion_requires_all_action_controls():"
if marker not in tests:
    tests += '''


def _completed_snapshot():
    return {
        "found": True,
        "generating": False,
        "actions": {"copy": True, "good": True, "bad": True},
    }


def test_response_completion_requires_all_action_controls():
    snapshot = _completed_snapshot()
    assert mod.response_snapshot_complete(snapshot, stable_count=1)

    for action in ("copy", "good", "bad"):
        incomplete = _completed_snapshot()
        incomplete["actions"][action] = False
        assert not mod.response_snapshot_complete(incomplete, stable_count=1)


def test_response_completion_requires_stable_finished_text():
    snapshot = _completed_snapshot()
    assert not mod.response_snapshot_complete(snapshot, stable_count=0)

    snapshot["generating"] = True
    assert not mod.response_snapshot_complete(snapshot, stable_count=2)

    snapshot = _completed_snapshot()
    snapshot["found"] = False
    assert not mod.response_snapshot_complete(snapshot, stable_count=2)
'''
    TESTS_PATH.write_text(tests)
