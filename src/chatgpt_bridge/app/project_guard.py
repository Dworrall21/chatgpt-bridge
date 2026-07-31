"""ProjectGuard: enrollment and project confinement checks (Phase 2 prep).

Phase 1/2 boundary: enrollment is allowed (read-only discovery + registry write).
`create_project_conversation` exists but is gated by Flags.allow_conversation_creation
and additionally refuses unless enrollment has >=2 independent ID sources.
"""

from __future__ import annotations

import time

from ..config import Config
from ..errors import (
    AccountMismatchError,
    ProjectAmbiguousError,
    ProjectAssertionFailedError,
    ProjectConfinementBreachError,
    ProjectIdChangedError,
    ProjectNotFoundError,
)
from ..registry import Registry

BINDING_ID = "chatgpt-bridge-default"


class ProjectGuard:
    def __init__(self, config: Config, registry: Registry):
        self.config = config
        self.registry = registry

    # ---- enrollment ------------------------------------------------------
    def enroll(self, discovery: dict) -> dict:
        """Persist a binding from a discovery result. Requires >=2 ID sources."""
        projects = discovery.get("projects", {})
        pinned = projects.get("pinned_project_id")
        if not pinned:
            raise ProjectNotFoundError("no pinned project id in discovery result")
        if projects.get("id_sources", 0) < 2:
            raise ProjectAssertionFailedError(
                f"project id sources = {projects.get('id_sources', 0)}; need >=2"
            )
        if projects.get("candidate_ids", []).count(pinned) > 1:
            raise ProjectAmbiguousError("pinned project id appears more than once")
        now = int(time.time())
        binding = {
            "binding_id": BINDING_ID,
            "account_fingerprint": discovery.get("account_fingerprint") or "unknown",
            "project_id": pinned,
            "project_name": self.config.app.required_project_name,
            "project_href": None,
            "project_context_hash": None,
            "app_build": (discovery.get("app_identity") or {}).get("userAgent", "")[:64],
            "selector_profile": "{}",
            "verified_at": now,
        }
        self.registry.upsert_binding(binding)
        return self.registry.get_binding(BINDING_ID) or binding

    # ---- per-conversation confinement -----------------------------------
    def require_binding(self) -> dict:
        binding = self.registry.get_binding(BINDING_ID)
        if binding is None:
            raise ProjectNotFoundError("no enrolled binding; run enroll first")
        if binding.get("disabled_at") is not None:
            raise ProjectConfinementBreachError("binding disabled; re-enroll required")
        return binding

    def assert_conversation_in_project(self, conversation: dict) -> None:
        binding = self.require_binding()
        if conversation.get("project_id") != binding["project_id"]:
            raise ProjectConfinementBreachError(
                f"conversation project {conversation.get('project_id')} != pinned {binding['project_id']}"
            )
        if conversation.get("mode") != self.config.app.required_mode:
            raise ProjectAssertionFailedError(
                f"conversation mode {conversation.get('mode')} != {self.config.app.required_mode}"
            )
        if conversation.get("model_label") != self.config.app.required_model_label:
            raise ProjectAssertionFailedError(
                f"conversation model {conversation.get('model_label')} != {self.config.app.required_model_label}"
            )
        if conversation.get("effort_label") != self.config.app.required_effort_label:
            raise ProjectAssertionFailedError(
                f"conversation effort {conversation.get('effort_label')} != {self.config.app.required_effort_label}"
            )

    def assert_account(self, account_fingerprint: str) -> None:
        binding = self.require_binding()
        if binding["account_fingerprint"] != "unknown" and account_fingerprint != binding["account_fingerprint"]:
            raise AccountMismatchError("account fingerprint changed since enrollment")

    def binding_changed(self, discovery: dict) -> bool:
        binding = self.registry.get_binding(BINDING_ID)
        if binding is None:
            return True
        pinned = discovery.get("projects", {}).get("pinned_project_id")
        return pinned != binding["project_id"]

    # ---- Phase 2 creation (gated) ---------------------------------------
    def create_project_conversation(self, *, session_key: str, title: str) -> dict:
        """UI flow to create a conversation inside the pinned project.

        Not implemented until Phase 2 CDP driver exists. Always refuses while
        Flags.allow_conversation_creation is false, and refuses unless the
        enrollment binding is present and valid.
        """
        self.require_binding()
        if not self.config.flags.allow_conversation_creation:
            raise ProjectAssertionFailedError("conversation creation disabled (CHATGPT_BRIDGE_ALLOW_CREATE != 1)")
        raise ProjectAssertionFailedError("Phase 2 CDP creation driver not installed")
