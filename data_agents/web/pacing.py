"""Pacing of the routes that call a model: so many per user per minute, so many Turns in flight at once. Per process.

A model call costs money and a Turn holds a thread, so neither is free to whoever holds a token. The counts live in this process;
several replicas behind a load balancer would move them to the System Store or a shared cache, and these two functions are
the seam.
"""

import os
import threading
import time
from collections import deque
from typing import Callable

PER_MINUTE = int(os.environ.get("MODEL_CALLS_PER_MINUTE", "20"))
IN_FLIGHT = int(os.environ.get("TURNS_IN_FLIGHT", "4"))


class Throttled(Exception):
    pass


_recent: dict[str, deque[float]] = {}
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(IN_FLIGHT)


def admit(user: str) -> None:
    """Count one model-calling request against the user's last minute, or refuse it."""
    now = time.monotonic()
    with _lock:
        recent = _recent.setdefault(user, deque())
        while recent and recent[0] <= now - 60:
            recent.popleft()
        if len(recent) >= PER_MINUTE:
            raise Throttled(f"{PER_MINUTE} model-calling requests a minute is the limit; try again in a moment")
        recent.append(now)


def slot() -> Callable[[], None]:
    """Take one of the Turn slots and return the way to give it back, or refuse when every slot is busy."""
    if not _slots.acquire(blocking=False):
        raise Throttled(f"{IN_FLIGHT} Turns are already running; try again in a moment")
    return _slots.release
