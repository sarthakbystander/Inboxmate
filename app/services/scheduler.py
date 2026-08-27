"""Lightweight in-process scheduler.

Runs a periodic background job (currently: periodic inbox sync and
session cleanup) inside the running uvicorn process. It is a plain
``threading.Thread`` with a stop event -- deliberately no Celery/Redis.
Guarded so multiple calls to ``start`` are harmless, and the thread is a
daemon so it never blocks shutdown.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("inboxmate.scheduler")


class Scheduler:
    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.jobs: list[tuple[callable, int]] = []  # (fn, interval_seconds)

    def add_job(self, fn: callable, interval_seconds: int) -> None:
        self.jobs.append((fn, interval_seconds))

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="inboxmate-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        logger.info("Scheduler started")
        next_run = {id(job): time.monotonic() for job in self.jobs}
        while self._running and not self._stop.is_set():
            now = time.monotonic()
            for job, interval in list(self.jobs):
                jid = id(job)
                if next_run.get(jid, 0) <= now:
                    next_run[jid] = now + interval
                    try:
                        job()
                    except Exception:
                        logger.exception("Scheduled job %r failed", getattr(job, "__name__", job))
            self._stop.wait(1.0)


scheduler = Scheduler()