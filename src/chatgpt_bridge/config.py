"""Configuration loading with fail-closed feature flags.

Phase 0 defaults: everything that touches the app is OFF.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TransportConfig:
    socket_path: str = "/run/user/%U/chatgpt-bridge/bridge.sock"
    require_peer_uid: bool = True
    require_hmac: bool = True
    hmac_secret_env: str = "CHATGPT_BRIDGE_HMAC_SECRET"
    hmac_secret: str | None = None
    max_request_bytes: int = 512 * 1024
    timestamp_tolerance_seconds: int = 30
    nonce_replay_window_seconds: int = 600
    allowed_peer_uid: int | None = None

    def resolve_socket_path(self) -> str:
        p = self.socket_path
        if "%U" in p:
            p = p.replace("%U", str(os.getuid()))
        return p

    def secret(self) -> bytes:
        if self.hmac_secret:
            return self.hmac_secret.encode()
        v = os.environ.get(self.hmac_secret_env)
        if not v:
            raise RuntimeError(f"HMAC secret required: set {self.hmac_secret_env}")
        return v.encode()


@dataclass(frozen=True)
class AppConfig:
    cdp_url: str = "http://127.0.0.1:9222"
    helper_path: str = ""
    required_mode: str = "Work"
    required_project_name: str = "chatgpt-bridge"
    required_project_id: str | None = None
    required_model_label: str = "GPT-5.6 Sol"
    required_effort_label: str = "High"
    renderer_origin: str = "http://127.0.0.1:5175"


@dataclass(frozen=True)
class ConversationConfig:
    reuse_by_hermes_session: bool = True
    max_turns_per_shard: int = 24
    max_estimated_context_tokens: int = 70_000
    max_idle_days: int = 7
    title_prefix: str = "HB"


@dataclass(frozen=True)
class SecurityConfig:
    allow_file_uploads: bool = False
    allow_tool_approvals: bool = False
    allow_arbitrary_cdp_eval: bool = False
    log_prompt_content: bool = False
    max_context_items: int = 64
    max_instruction_chars: int = 100_000
    max_context_item_chars: int = 100_000
    max_result_chars: int = 100_000


@dataclass(frozen=True)
class Flags:
    bridge_enabled: bool = False
    allow_conversation_creation: bool = False
    allow_send: bool = False
    automatic_routing: bool = False

    @property
    def can_touch_app(self) -> bool:
        return self.allow_conversation_creation or self.allow_send

    def assert_send_allowed(self) -> None:
        if not self.bridge_enabled:
            raise RuntimeError("bridge disabled (CHATGPT_BRIDGE_ENABLED != 1)")
        if not self.allow_send:
            raise RuntimeError("sends disabled (CHATGPT_BRIDGE_ALLOW_SEND != 1)")


@dataclass(frozen=True)
class Config:
    transport: TransportConfig = field(default_factory=TransportConfig)
    app: AppConfig = field(default_factory=AppConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    flags: Flags = field(default_factory=Flags)

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        transport = TransportConfig()
        app = AppConfig()
        conversation = ConversationConfig()
        security = SecurityConfig()

        if path:
            p = Path(path)
            if not p.exists():
                raise FileNotFoundError(f"config not found: {path}")
            with p.open("rb") as f:
                data = tomllib.load(f)
            t = data.get("transport", {})
            a = data.get("app", {})
            c = data.get("conversation", {})
            s = data.get("security", {})
            transport = TransportConfig(**{k: v for k, v in t.items() if k in TransportConfig.__dataclass_fields__})
            app = AppConfig(**{k: v for k, v in a.items() if k in AppConfig.__dataclass_fields__})
            conversation = ConversationConfig(**{k: v for k, v in c.items() if k in ConversationConfig.__dataclass_fields__})
            security = SecurityConfig(**{k: v for k, v in s.items() if k in SecurityConfig.__dataclass_fields__})

        # Fail closed: when peer-UID checking is on, pin to the current UID unless
        # an explicit UID was configured.
        if transport.require_peer_uid and transport.allowed_peer_uid is None:
            transport = TransportConfig(**{**transport.__dict__, "allowed_peer_uid": os.getuid()})

        flags = Flags(
            bridge_enabled=_env_bool("CHATGPT_BRIDGE_ENABLED", False),
            allow_conversation_creation=_env_bool("CHATGPT_BRIDGE_ALLOW_CREATE", False),
            allow_send=_env_bool("CHATGPT_BRIDGE_ALLOW_SEND", False),
            automatic_routing=_env_bool("CHATGPT_BRIDGE_AUTOROUTE", False),
        )
        return cls(transport=transport, app=app, conversation=conversation, security=security, flags=flags)
