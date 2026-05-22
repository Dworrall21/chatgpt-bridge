#!/usr/bin/env python3
"""Cache behavior checks for load_available_models()."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = REPO_ROOT / "bridge-host.py"


def load_bridge_module():
    spec = importlib.util.spec_from_file_location("bridge_host", BRIDGE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {BRIDGE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reset_cache(bridge):
    bridge._MODEL_CATALOG_CACHE_MODELS = None
    bridge._MODEL_CATALOG_CACHE_SOURCE_PATH = None
    bridge._MODEL_CATALOG_CACHE_SOURCE_MTIME = None
    bridge._MODEL_CATALOG_CACHE_LOADED_AT = None


def main() -> int:
    bridge = load_bridge_module()

    with tempfile.TemporaryDirectory() as tmp:
        catalog_path = Path(tmp) / "model_catalog.json"
        catalog_path.write_text(json.dumps({"providers": {"openai": {"models": ["gpt-4o-mini", "gpt-4o"]}}}))

        bridge._MODEL_CATALOG_CANDIDATES = [catalog_path]
        bridge._MODEL_CATALOG_CACHE_TTL_SECONDS = 30
        reset_cache(bridge)

        first = bridge.load_available_models()
        second = bridge.load_available_models()
        if first != ["gpt-4o", "gpt-4o-mini"]:
            raise AssertionError(f"unexpected model set: {first}")
        if second != first:
            raise AssertionError(f"cache changed unexpectedly: {second} vs {first}")

        time.sleep(1.05)
        catalog_path.write_text(json.dumps({"providers": {"openai": {"models": ["gpt-4o", "o3"]}}}))

        third = bridge.load_available_models()
        if third != ["gpt-4o", "o3"]:
            raise AssertionError(f"cache did not invalidate after mtime change: {third}")

    print("model catalog cache tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
