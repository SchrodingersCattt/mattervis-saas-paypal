"""Durable job state with a bounded local worker pool."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
import threading
import traceback
from typing import Callable

from .db import Database


@dataclass(slots=True)
class JobOutcome:
    result: dict = field(default_factory=dict)
    artifacts: list[dict] = field(default_factory=list)


class JobManager:
    def __init__(self, db: Database, max_workers: int = 2):
        self.db = db
        self.executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)), thread_name_prefix="mattervis-job"
        )
        self._futures: dict[str, Future] = {}
        self._lock = threading.RLock()

    def submit(self, job_id: str, work: Callable[[], JobOutcome]) -> Future:
        def runner() -> None:
            self.db.update_job(job_id, status="running")
            try:
                outcome = work()
                self.db.update_job(job_id, status="succeeded", result=outcome.result)
            except Exception as exc:  # keep failures inspectable by clients
                self.db.update_job(
                    job_id,
                    status="failed",
                    error={
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(limit=5),
                    },
                )

        future = self.executor.submit(runner)
        with self._lock:
            self._futures[job_id] = future
        return future

    def wait(self, job_id: str, timeout: float) -> bool:
        with self._lock:
            future = self._futures.get(job_id)
        if future is None:
            return True
        try:
            future.result(timeout=max(0.0, float(timeout)))
        except FutureTimeout:
            return False
        return True

    def shutdown(self, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=True)
