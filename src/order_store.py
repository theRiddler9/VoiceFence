"""
OrderStore: a fake order system. One pretend delivery order sitting in
memory — no database, no persistence, no retry logic, on purpose. Its
only job is to be slow (adjustably so) and interruptible on demand.

The artificial delay is what simulates a real company's slow backend
lookup, which is the entire reason this demo is interesting: it creates
a window where a "cancel" can arrive *during* the lookup, and the system
has to throw the stale result away instead of applying it.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import config
from .batch_registry import BatchRegistry


@dataclass
class LookupResult:
    batch_id: str
    status: str  # "completed" | "cancelled"
    order: Optional[dict]


class OrderStore:
    def __init__(
        self,
        registry: BatchRegistry,
        delay_seconds: Optional[float] = None,
        poll_interval: float = 0.05,
    ):
        self._registry = registry
        # Allow tests to override the configured delay.
        self._delay_seconds = (
            delay_seconds if delay_seconds is not None else config.MOCK_LOOKUP_DELAY_SECONDS
        )
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self._order = {
            "address": "123 Placeholder St, Springfield",
            "eta_minutes": 30,
            "status": "confirmed",
        }

    def get_order(self) -> dict:
        with self._lock:
            return dict(self._order)

    def update_address(self, new_address: str, batch_id: str) -> LookupResult:
        """Simulates a slow lookup/update. Sleeps in small increments
        (rather than one long time.sleep) so a cancel arriving mid-delay
        is noticed almost immediately instead of only after the full
        delay elapses."""
        if self._registry.is_cancelled(batch_id):
            return LookupResult(batch_id, "cancelled", None)

        elapsed = 0.0
        while elapsed < self._delay_seconds:
            if self._registry.is_cancelled(batch_id):
                return LookupResult(batch_id, "cancelled", None)
            step = min(self._poll_interval, self._delay_seconds - elapsed)
            time.sleep(step)
            elapsed += step

        # Recheck before mutating shared order state.
        if self._registry.is_cancelled(batch_id):
            return LookupResult(batch_id, "cancelled", None)

        with self._lock:
            self._order["address"] = new_address
            self._order["status"] = "updated"
            result = dict(self._order)
        return LookupResult(batch_id, "completed", result)
