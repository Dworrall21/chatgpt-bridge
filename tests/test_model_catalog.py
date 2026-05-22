#!/usr/bin/env python3
"""Focused checks for the ChatGPT Bridge model registry.

This test does not require a live extension connection. It verifies the
catalog helpers, the best-match resolver, and the live /v1/models endpoint
shape using whatever catalog is currently cached by bridge-host.py.
"""

from __future__ import annotations

import importlib.util
import json
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = REPO_ROOT / "bridge-host.py"
BRIDGE_URL = "http://127.0.0.1:11557"


def load_bridge_module():
    spec = importlib.util.spec_from_file_location("bridge_host", BRIDGE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {BRIDGE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    bridge = load_bridge_module()

    models = bridge.load_available_models()
    if not models:
        raise AssertionError("expected a non-empty cached model catalog")

    catalog = bridge._format_model_catalog(models, 1234567890)
    if not catalog:
        raise AssertionError("formatted catalog is empty")
    for row in catalog:
        if set(row) != {"id", "object", "created", "owned_by"}:
            raise AssertionError(f"unexpected model row keys: {row}")
        if row["object"] != "model":
            raise AssertionError(f"unexpected model object field: {row}")
        if row["owned_by"] != "openai":
            raise AssertionError(f"unexpected owned_by field: {row}")

    if bridge._best_model_match("gpt4o", ["GPT-4o", "gpt 4o mini"]) != "GPT-4o":
        raise AssertionError("compact match gpt4o → GPT-4o failed")
    if bridge._best_model_match("gpt-4o-mini", ["GPT-4o", "gpt 4o mini"]) != "gpt 4o mini":
        raise AssertionError("compact match gpt-4o-mini → gpt 4o mini failed")

    with urllib.request.urlopen(f"{BRIDGE_URL}/v1/models", timeout=10) as resp:
        payload = json.loads(resp.read())
    if payload.get("object") != "list":
        raise AssertionError(f"unexpected /v1/models object: {payload!r}")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise AssertionError(f"unexpected /v1/models data: {payload!r}")
    for row in data:
        if set(row) != {"id", "object", "created", "owned_by"}:
            raise AssertionError(f"unexpected /v1/models row keys: {row}")
        if row["object"] != "model":
            raise AssertionError(f"unexpected /v1/models row object: {row}")
        if row["owned_by"] != "openai":
            raise AssertionError(f"unexpected /v1/models row owner: {row}")

    print(f"model catalog test passed ({len(data)} models)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
