"""Metrics collector: counters + JSONL event stream."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class Metrics:
    def __init__(self, jsonl_path: str | None = None):
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._jsonl_path = jsonl_path

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + n

    def event(self, name: str, **fields) -> None:
        rec = {"event": name, "ts": time.time(), **fields}
        if self._jsonl_path:
            with open(self._jsonl_path, "a") as f:
                f.write(json.dumps(rec) + "\n")

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._counters)

    def summary(self) -> dict:
        return {
            "counters": self.snapshot(),
            "jsonl": self._jsonl_path,
        }
