"""SSE broadcast hub: tracks connected watchers and fans out poll events.

The scheduler uses watcher state to decide poll cadence: while at least one
browser holds the event stream open (or disconnected less than GRACE_SECONDS
ago), polling runs at the "active" intervals; otherwise it relaxes to the
idle intervals.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

GRACE_SECONDS = 90

_subscribers: set[asyncio.Queue] = set()
_last_disconnect: datetime | None = None


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    logger.info("SSE client connected (%d active)", len(_subscribers))
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    global _last_disconnect
    _subscribers.discard(q)
    _last_disconnect = datetime.utcnow()
    logger.info("SSE client disconnected (%d active)", len(_subscribers))


def watcher_count() -> int:
    return len(_subscribers)


def is_watched() -> bool:
    """True if a browser is watching now, or disconnected within the grace period."""
    if _subscribers:
        return True
    return (
        _last_disconnect is not None
        and datetime.utcnow() - _last_disconnect < timedelta(seconds=GRACE_SECONDS)
    )


def publish(event: dict) -> None:
    """Fan out an event to all connected clients. Never blocks the poller."""
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # slow client — it will catch up from the next event
