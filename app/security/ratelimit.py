"""In-process rate limiting for authentication endpoints.

Simple sliding-window limiter keyed by client IP. Suitable for a
single-process, low-traffic self-hosted deployment. Data is stored in a
dict guarded by a lock (no external infrastructure).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

_lock = Lock()
_events: dict[str, deque] = defaultdict(deque)

WINDOW_SECONDS = 60


def _client_ip(request) -> str:
    """Best-effort client IP, honoring a trusted X-Forwarded-For header."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


def is_rate_limited(request, limit_per_minute: int) -> bool:
    """Record a request and return True if it should be blocked."""
    key = _client_ip(request)
    now = time.monotonic()
    with _lock:
        q = _events[key]
        # Drop events outside the window.
        while q and q[0] < now - WINDOW_SECONDS:
            q.popleft()
        q.append(now)
        if len(q) > limit_per_minute:
            return True
    return False


def reset_rate_limiter() -> None:
    """Clear all recorded events (used by tests between cases)."""
    with _lock:
        _events.clear()