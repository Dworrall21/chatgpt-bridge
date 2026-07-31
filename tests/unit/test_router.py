from chatgpt_bridge.hermes.router import should_delegate


def test_delegates_planning():
    r = should_delegate(kind="planning", instruction="x" * 200, context_chars=20_000)
    assert r.delegate


def test_delegates_review_and_design():
    for kind in ("review", "design"):
        assert should_delegate(kind=kind, instruction="x" * 200, context_chars=20_000).delegate


def test_rejects_synthesis_and_drafting():
    for kind in ("synthesis", "drafting", "reasoning"):
        assert not should_delegate(kind=kind, instruction="x" * 200, context_chars=20_000).delegate


def test_rejects_tools():
    r = should_delegate(kind="planning", instruction="x" * 200, context_chars=20_000, requires_tools=True)
    assert not r.delegate


def test_rejects_small_tasks():
    r = should_delegate(kind="planning", instruction="short", context_chars=0)
    assert not r.delegate
    assert "overhead" in r.reason
