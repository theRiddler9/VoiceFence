"""
BatchRegistry is the one piece of shared state Joy's two components
(Rime speaker + order store) and Person A's orchestrator all agree on.

The orchestrator tags every "speak this" / "look this up" request with a
batch id. When something becomes outdated, the orchestrator calls
cancel(batch_id) exactly once. Both components poll is_cancelled(batch_id)
frequently enough that they stop almost immediately instead of finishing
quietly in the background.

This is intentionally the simplest thing that could work: a thread-safe
set of cancelled ids. No pub/sub, no async event bus — a shared process,
one shared boolean per batch, checked often.
"""
import threading
import itertools
from collections.abc import Callable


class BatchRegistry:
    _counter = itertools.count(1)

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = set()

    @classmethod
    def new_batch_id(cls) -> str:
        """Return a fresh process-wide batch identifier."""
        return f"batch-{next(cls._counter)}"

    def cancel(self, batch_id: str) -> None:
        """Mark a batch id as outdated. Idempotent — safe to call more than
        once, and safe to call for a batch id that already finished."""
        with self._lock:
            self._cancelled.add(batch_id)

    def is_cancelled(self, batch_id: str) -> bool:
        with self._lock:
            return batch_id in self._cancelled

    def run_if_active(
        self,
        batch_id: str,
        operation: Callable[[], None],
    ) -> bool:
        """Run ``operation`` only while cancellation cannot interleave.

        The registry lock is the linearization point shared with ``cancel``.
        Callers must not invoke registry methods from ``operation``.
        """
        with self._lock:
            if batch_id in self._cancelled:
                return False
            operation()
            return True

    def clear(self, batch_id: str) -> None:
        """Remove a cancelled batch after all work has stopped."""
        with self._lock:
            self._cancelled.discard(batch_id)
