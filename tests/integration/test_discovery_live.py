"""Live integration test: runs only when the ChatGPT desktop app (CDP 9222) is up."""

import os

import pytest

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("CHATGPT_BRIDGE_LIVE_TESTS") != "1",
    reason="set CHATGPT_BRIDGE_LIVE_TESTS=1 to run live app discovery",
)
def test_discover_live_app():
    from chatgpt_bridge.app.discovery import discover

    result = discover()
    assert result["ok"] is True
    assert result["app_identity"]["origin"] == "http://127.0.0.1:5175"
    assert result["projects"]["pinned_project_id"] == "cloud:Dworrall21/chatgpt-bridge"
    assert result["projects"]["id_sources"] >= 1
    model_items = result["model_picker"]["items"]
    labels = {i["text"] for i in model_items}
    assert "GPT-5.6 Sol" in labels
    assert "High" in labels
