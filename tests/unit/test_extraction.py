from chatgpt_bridge.app.driver import DesktopDriver


def _body(prompt, response="Real answer. HERMES-DONE"):
    return (
        "Sidebar noise\n"
        "You said:\n\n"
        f"{prompt}\n\n"
        "3:00 PM\n"
        f"ChatGPT said:\n{response}\n"
        "3:05 PM\n"
        "Ask for approval\nOutputs\n"
    )


PROMPT_WITH_MARKER = "[HERMES-BRIDGE v1 request=r1] ... End your response with exactly: HERMES-DONE"


def test_tail_after_user_excludes_prompt_marker():
    body = _body(PROMPT_WITH_MARKER)
    tail = DesktopDriver._tail_after_user(body)
    assert "HERMES-DONE" in tail  # only the assistant's marker remains
    assert "request=r1" not in tail  # prompt itself is gone


def test_tail_after_user_returns_all_when_no_user_block():
    tail = DesktopDriver._tail_after_user("no user marker here")
    assert tail == "no user marker here"


def test_extract_response_strips_chrome():
    body = _body(PROMPT_WITH_MARKER, response="Conclusion: ok\n\nKey rationale\nHERMES-DONE")
    out = DesktopDriver._extract_response(body)
    assert out.startswith("Conclusion: ok")
    assert "Ask for approval" not in out
    assert out.endswith("HERMES-DONE")
    assert "3:05 PM" not in out


def test_extract_response_terminates_at_marker():
    body = _body(PROMPT_WITH_MARKER, response="A\nHERMES-DONE\n9:99 PM junk")
    out = DesktopDriver._extract_response(body)
    assert out.endswith("HERMES-DONE")
    assert "junk" not in out
