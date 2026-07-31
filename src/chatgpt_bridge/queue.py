"""Job queue: one global UI lock, ordered per-session queues.

Phase 0: executor is a stub — it advances the journal without touching the app.
Real CDP send path is gated by Flags.allow_send.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .config import Flags
from .errors import BridgeError
from .registry import Registry
from .state_machine import RequestState


@dataclass
class Job:
    request_id: str
    session_key: str


@dataclass
class QueueStats:
    queued: int = 0
    running: int = 0
    per_session: dict[str, int] = field(default_factory=dict)


class JobQueue:
    def __init__(self, registry: Registry, flags: Flags, executor=None):
        self.registry = registry
        self.flags = flags
        self._queues: dict[str, deque[Job]] = {}
        self._running: dict[str, str | None] = {}  # session_key -> request_id
        self._lock = threading.Lock()
        self._ui_lock = threading.Lock()
        self._executor = executor or self._stub_executor

    def enqueue(self, request_id: str, session_key: str) -> None:
        with self._lock:
            self._queues.setdefault(session_key, deque()).append(Job(request_id, session_key))
        self._pump()

    def _pump(self) -> None:
        with self._lock:
            for session_key, q in list(self._queues.items()):
                if self._running.get(session_key) is not None:
                    continue
                if not q:
                    continue
                job = q.popleft()
                self._running[session_key] = job.request_id
                t = threading.Thread(target=self._run_job, args=(job,), daemon=True)
                t.start()

    def _run_job(self, job: Job) -> None:
        try:
            if not self._ui_lock.acquire(timeout=30):
                self.registry.set_request_state(job.request_id, RequestState.NEEDS_RECONCILIATION)
                return
            try:
                self.registry.set_request_state(job.request_id, RequestState.QUEUED)
                self.registry.set_request_state(job.request_id, RequestState.UI_LOCKED)
                self._executor(job, self.registry, self.flags)
            finally:
                self._ui_lock.release()
        except BridgeError as e:
            self.registry.set_request_state(
                job.request_id, RequestState.FAILED, error_code=e.code, error_stage=e.stage
            )
        except Exception as e:  # noqa: BLE001 — journal the failure, never crash the daemon
            self.registry.set_request_state(job.request_id, RequestState.FAILED, error_code="INTERNAL", error_stage="executor")
        finally:
            with self._lock:
                self._running[job.session_key] = None

    def _stub_executor(self, job: Job, registry: Registry, flags: Flags) -> None:
        """Phase 0 stub: verify project bound + flags, then fail closed because
        sends are disabled. Never touches the app."""
        registry.set_request_state(job.request_id, RequestState.PROJECT_VERIFIED)
        registry.set_request_state(job.request_id, RequestState.CONVERSATION_BOUND)
        registry.set_request_state(job.request_id, RequestState.MODEL_VERIFIED)
        if not flags.allow_send:
            registry.set_request_state(
                job.request_id,
                RequestState.FAILED,
                error_code="SENDS_DISABLED",
                error_stage="phase0",
            )
            return
        raise RuntimeError("allow_send=true but no CDP executor installed (Phase 2+)")

    def stats(self) -> QueueStats:
        s = QueueStats()
        with self._lock:
            s.queued = sum(len(q) for q in self._queues.values())
            s.running = sum(1 for v in self._running.values() if v is not None)
            s.per_session = {k: len(q) for k, q in self._queues.items()}
        return s
