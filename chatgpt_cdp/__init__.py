"""Shared implementation for the standalone chatgpt-cdp.py CLI."""

from .runner import send_prompt
from .state import (
    CdpError,
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    DEFAULT_PROJECT_NAME,
    DEFAULT_PROJECT_URL,
    DEFAULT_STATE_FILE,
    DEFAULT_TIMEOUT,
    RunConfig,
    SessionStore,
    _collapse_ws,
    _env_first,
    _locked_file,
    conversation_id_from_url,
    derive_summary,
    make_chat_title,
    project_url_from_conversation_url,
    resolve_chat_summary,
    sanitize_session_id,
    validate_chatgpt_url,
)

__all__ = [
    "CdpError", "DEFAULT_EFFORT", "DEFAULT_MODEL", "DEFAULT_PROJECT_NAME",
    "DEFAULT_PROJECT_URL", "DEFAULT_STATE_FILE", "DEFAULT_TIMEOUT",
    "RunConfig", "SessionStore", "conversation_id_from_url", "derive_summary",
    "make_chat_title", "project_url_from_conversation_url",
    "resolve_chat_summary", "sanitize_session_id", "send_prompt",
    "validate_chatgpt_url",
]
