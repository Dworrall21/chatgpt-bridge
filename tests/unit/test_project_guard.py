import time

import pytest

from chatgpt_bridge.app.project_guard import BINDING_ID, ProjectGuard
from chatgpt_bridge.config import Config
from chatgpt_bridge.errors import (
    ProjectAmbiguousError,
    ProjectAssertionFailedError,
    ProjectConfinementBreachError,
    ProjectNotFoundError,
)
from chatgpt_bridge.registry import Registry


def _cfg(**flags):
    return Config()


def _discovery(pinned="cloud:owner/chatgpt-bridge", sources=2, count=1):
    return {
        "account_fingerprint": "fp-1",
        "app_identity": {"userAgent": "UA/1"},
        "projects": {
            "pinned_project_id": pinned,
            "id_sources": sources,
            "candidate_ids": [pinned] * count,
        },
    }


def test_enroll_requires_two_sources(tmp_path):
    reg = Registry(tmp_path / "g.sqlite3")
    try:
        guard = ProjectGuard(_cfg(), reg)
        with pytest.raises(ProjectAssertionFailedError):
            guard.enroll(_discovery(sources=1))
        binding = guard.enroll(_discovery(sources=2))
        assert binding["project_id"] == "cloud:owner/chatgpt-bridge"
    finally:
        reg.close()


def test_enroll_rejects_missing_or_ambiguous(tmp_path):
    reg = Registry(tmp_path / "g2.sqlite3")
    try:
        guard = ProjectGuard(_cfg(), reg)
        with pytest.raises(ProjectNotFoundError):
            guard.enroll(_discovery(pinned=None))
        with pytest.raises(ProjectAmbiguousError):
            guard.enroll(_discovery(count=2))
    finally:
        reg.close()


def test_require_binding_and_confinement(tmp_path):
    reg = Registry(tmp_path / "g3.sqlite3")
    try:
        guard = ProjectGuard(_cfg(), reg)
        with pytest.raises(ProjectNotFoundError):
            guard.require_binding()
        guard.enroll(_discovery())
        ok = {
            "conversation_id": "c1",
            "session_key": "s",
            "project_id": "cloud:owner/chatgpt-bridge",
            "mode": "Work",
            "model_label": "GPT-5.6 Sol",
            "effort_label": "High",
        }
        guard.assert_conversation_in_project(ok)
        bad = dict(ok, project_id="cloud:owner/other")
        with pytest.raises(ProjectConfinementBreachError):
            guard.assert_conversation_in_project(bad)
        chat = dict(ok, mode="Chat")
        with pytest.raises(ProjectAssertionFailedError):
            guard.assert_conversation_in_project(chat)
    finally:
        reg.close()


def test_disabled_binding_fails_closed(tmp_path):
    reg = Registry(tmp_path / "g4.sqlite3")
    try:
        guard = ProjectGuard(_cfg(), reg)
        guard.enroll(_discovery())
        reg.disable_binding(BINDING_ID)
        with pytest.raises(ProjectConfinementBreachError):
            guard.require_binding()
    finally:
        reg.close()


def test_creation_gated_by_flag(tmp_path):
    reg = Registry(tmp_path / "g5.sqlite3")
    try:
        guard = ProjectGuard(_cfg(), reg)
        guard.enroll(_discovery())
        with pytest.raises(ProjectAssertionFailedError) as e:
            guard.create_project_conversation(session_key="s", title="HB · canary")
        assert "disabled" in str(e.value)
    finally:
        reg.close()
