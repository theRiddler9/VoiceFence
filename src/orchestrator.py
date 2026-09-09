"""Epoch-based orchestration for interruptible order updates.

The orchestrator is deliberately independent of LiveKit, STT, and an LLM.
Those layers only need to call ``start_address_update`` for a new intent and
``interrupt`` when the user barges in.

Joy's components use a cancellable ``batch_id``.  This module owns the
monotonic epoch and maps each epoch to one batch id.  A result is usable only
when both its epoch and batch are still current.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from .batch_registry import BatchRegistry


class OrderStoreLike(Protocol):
    def update_address(self, new_address: str, batch_id: str) -> Any: ...


class SpeakerLike(Protocol):
    def speak(self, text: str, batch_id: str, blocking: bool = True) -> Any: ...


@dataclass(frozen=True)
class EpochContext:
    """Identity attached to every tool call and speech request."""

    epoch: int
    batch_id: str


@dataclass
class TurnState:
    """Mutable state snapshot owned by ``EpochOrchestrator``."""

    current_epoch: int = 0
    active_batch_id: Optional[str] = None
    active_tool_task: Optional[threading.Thread] = None
    active_tts_task: Optional[threading.Thread] = None


@dataclass(frozen=True)
class OrchestratorResult:
    """Outcome of the tool portion of one address-update turn."""

    context: EpochContext
    status: str  # completed | stale-dropped | cancelled | timeout | error
    order: Optional[dict] = None
    error: Optional[str] = None


class UpdateHandle:
    """Joinable handle for one address-update turn."""

    def __init__(self, context: EpochContext):
        self.context = context
        self.thread: Optional[threading.Thread] = None
        self.tts_thread: Optional[Any] = None
        self.result: Optional[OrchestratorResult] = None
        self._done = threading.Event()
        self._tts_ready = threading.Event()
        self._result_lock = threading.Lock()
        self._timeout_claimed = False

    def _set_result(self, result: OrchestratorResult) -> bool:
        """Set the first result only; timeout and late tool completion race."""
        with self._result_lock:
            if self.result is not None or self._timeout_claimed:
                return False
            self.result = result
            self._tts_ready.set()
            self._done.set()
            return True

    def _set_tts_task(self, task: Any) -> None:
        self.tts_thread = task
        self._tts_ready.set()

    def _set_timeout_result(self, result: OrchestratorResult) -> None:
        with self._result_lock:
            if not self._timeout_claimed or self.result is not None:
                return
            self.result = result
            self._done.set()

    def join(self, timeout: Optional[float] = None) -> Optional[OrchestratorResult]:
        self._done.wait(timeout)
        return self.result if self._done.is_set() else None

    def wait_for_tts(self, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        if not self._tts_ready.wait(timeout):
            return False
        if self.tts_thread is None:
            return True
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        self.tts_thread.join(remaining)
        return not self.tts_thread.is_alive()


EventSink = Callable[[dict], None]
ConfirmationFormatter = Callable[[dict], str]


def default_confirmation(order: dict) -> str:
    address = order.get("address", "the new address")
    return f"Sure, I've updated your address to {address}."


class EpochOrchestrator:
    """Coordinate cancellable tool work and Rime speech by epoch."""

    FAILURE_MESSAGE = "I couldn't confirm that address. Can you repeat it?"

    def __init__(
        self,
        registry: BatchRegistry,
        store: OrderStoreLike,
        speaker: SpeakerLike,
        *,
        tool_timeout_seconds: float = 4.0,
        confirmation_formatter: ConfirmationFormatter = default_confirmation,
        event_sink: Optional[EventSink] = None,
    ):
        if tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds must be greater than zero")

        self._registry = registry
        self._store = store
        self._speaker = speaker
        self._tool_timeout_seconds = tool_timeout_seconds
        self._confirmation_formatter = confirmation_formatter
        self._event_sink = event_sink
        self._lock = threading.Lock()
        self._state = TurnState()
        self._active_context: Optional[EpochContext] = None
        # The next turn consumes the epoch advanced by an interrupt.
        self._epoch_reserved_for_next_turn = False

    @property
    def current_epoch(self) -> int:
        with self._lock:
            return self._state.current_epoch

    def get_state(self) -> TurnState:
        """Return a copy suitable for diagnostics and tests."""
        with self._lock:
            return TurnState(
                current_epoch=self._state.current_epoch,
                active_batch_id=self._state.active_batch_id,
                active_tool_task=self._state.active_tool_task,
                active_tts_task=self._state.active_tts_task,
            )

    def begin_turn(self) -> EpochContext:
        """Invalidate any active work and create a fresh epoch/batch."""
        with self._lock:
            previous = self._active_context
            if previous is not None:
                self._registry.cancel(previous.batch_id)

            if self._epoch_reserved_for_next_turn:
                self._epoch_reserved_for_next_turn = False
            else:
                self._state.current_epoch += 1
            context = EpochContext(
                epoch=self._state.current_epoch,
                batch_id=self._registry.new_batch_id(),
            )
            self._active_context = context
            self._state.active_batch_id = context.batch_id
            self._state.active_tool_task = None
            self._state.active_tts_task = None

        if previous is not None:
            self._emit("epoch-invalidated", previous, reason="superseded")
        self._emit("epoch-started", context)
        return context

    def interrupt(self, reason: str = "barge_in") -> None:
        """Invalidate the current turn and cancel its shared batch.

        Incrementing the epoch immediately fences results that return during
        the gap before the next user request arrives.
        """
        with self._lock:
            previous = self._active_context
            self._state.current_epoch += 1
            self._epoch_reserved_for_next_turn = True
            self._active_context = None
            self._state.active_batch_id = None
            self._state.active_tool_task = None
            self._state.active_tts_task = None
            if previous is not None:
                self._registry.cancel(previous.batch_id)

        if previous is not None:
            self._emit("epoch-invalidated", previous, reason=reason)
        self._emit(
            "epoch-advanced",
            EpochContext(self.current_epoch, previous.batch_id if previous else ""),
            reason=reason,
        )

    def is_current(self, context: EpochContext) -> bool:
        with self._lock:
            return (
                self._active_context == context
                and self._state.current_epoch == context.epoch
                and not self._registry.is_cancelled(context.batch_id)
            )

    def start_address_update(self, new_address: str) -> UpdateHandle:
        """Start a fenced address update and its eventual confirmation."""
        context = self.begin_turn()
        handle = UpdateHandle(context)
        worker = threading.Thread(
            target=self._run_update,
            args=(new_address, context, handle),
            name=f"epoch-tool-{context.epoch}",
            daemon=True,
        )
        handle.thread = worker

        with self._lock:
            if self._active_context == context:
                self._state.active_tool_task = worker

        worker.start()
        timeout_watcher = threading.Thread(
            target=self._watch_timeout,
            args=(context, handle),
            name=f"epoch-timeout-{context.epoch}",
            daemon=True,
        )
        timeout_watcher.start()
        return handle

    def on_barge_in(self) -> None:
        """Integration hook for Riya's VAD/LiveKit interruption event."""
        self.interrupt(reason="barge_in")

    def on_address_intent(self, address: str) -> UpdateHandle:
        """Integration hook for the parsed voice intent."""
        return self.start_address_update(address)

    def _run_update(
        self,
        new_address: str,
        context: EpochContext,
        handle: UpdateHandle,
    ) -> None:
        try:
            lookup = self._store.update_address(new_address, context.batch_id)
        except Exception as exc:
            self._emit("tool-error", context, error=str(exc))
            handle._set_result(
                OrchestratorResult(context, "error", error=str(exc))
            )
            self._clear_tool_if_current(context)
            return

        if not self.is_current(context):
            self._emit("stale-dropped", context, source="tool-result")
            handle._set_result(OrchestratorResult(context, "stale-dropped"))
            self._clear_tool_if_current(context)
            return

        if getattr(lookup, "status", None) != "completed" or not lookup.order:
            status = getattr(lookup, "status", "error")
            self._emit("tool-not-completed", context, status=status)
            handle._set_result(
                OrchestratorResult(context, status, error="address update did not complete")
            )
            self._clear_tool_if_current(context)
            return

        if not self.is_current(context):
            self._emit("stale-dropped", context, source="before-tts")
            handle._set_result(OrchestratorResult(context, "stale-dropped"))
            self._clear_tool_if_current(context)
            return

        speech_text = self._confirmation_formatter(lookup.order)
        tts_task = self._speaker.speak(
            speech_text,
            context.batch_id,
            blocking=False,
        )
        handle._set_tts_task(tts_task)
        with self._lock:
            if self._active_context == context:
                self._state.active_tts_task = tts_task
        self._emit("tts-started", context)
        handle._set_result(OrchestratorResult(context, "completed", lookup.order))
        self._clear_tool_if_current(context)

    def _watch_timeout(self, context: EpochContext, handle: UpdateHandle) -> None:
        if handle._done.wait(self._tool_timeout_seconds):
            return

        claimed_epoch = self._claim_timeout(context, handle)
        if claimed_epoch is None:
            return

        self._emit("tool-timeout", context)
        self._emit("epoch-invalidated", context, reason="tool_timeout")
        self._emit(
            "epoch-advanced",
            EpochContext(claimed_epoch, context.batch_id),
            reason="tool_timeout",
        )

        failure_context = self._begin_timeout_failure(claimed_epoch)
        if failure_context is not None:
            self._emit("epoch-started", failure_context)
            if self.is_current(failure_context):
                failure_task = self._speaker.speak(
                    self.FAILURE_MESSAGE,
                    failure_context.batch_id,
                    blocking=False,
                )
                handle._set_tts_task(failure_task)
                with self._lock:
                    if self._active_context == failure_context:
                        self._state.active_tts_task = failure_task
                self._emit("tts-started", failure_context, purpose="timeout-failure")

        handle._tts_ready.set()
        handle._set_timeout_result(
            OrchestratorResult(context, "timeout", error="address lookup timed out")
        )

    def _claim_timeout(
        self,
        context: EpochContext,
        handle: UpdateHandle,
    ) -> Optional[int]:
        """Atomically claim a still-active context and invalidate only it."""
        claimed_epoch: Optional[int] = None

        def claim() -> bool:
            nonlocal claimed_epoch
            with handle._result_lock:
                if handle.result is not None or handle._timeout_claimed:
                    return False

                self._state.current_epoch += 1
                claimed_epoch = self._state.current_epoch
                self._epoch_reserved_for_next_turn = True
                self._active_context = None
                self._state.active_batch_id = None
                self._state.active_tool_task = None
                self._state.active_tts_task = None
                handle._timeout_claimed = True
                return True

        with self._lock:
            if (
                self._active_context != context
                or self._state.current_epoch != context.epoch
            ):
                return None
            if not self._registry.cancel_if_active(context.batch_id, claim):
                return None
            if claimed_epoch is None:
                return None
        return claimed_epoch

    def _begin_timeout_failure(self, claimed_epoch: int) -> Optional[EpochContext]:
        """Create timeout speech only if no newer turn consumed the epoch."""
        with self._lock:
            if (
                self._active_context is not None
                or self._state.current_epoch != claimed_epoch
                or not self._epoch_reserved_for_next_turn
            ):
                return None

            self._epoch_reserved_for_next_turn = False
            failure_context = EpochContext(
                epoch=claimed_epoch,
                batch_id=self._registry.new_batch_id(),
            )
            self._active_context = failure_context
            self._state.active_batch_id = failure_context.batch_id
            self._state.active_tool_task = None
            self._state.active_tts_task = None
            return failure_context

    def _clear_tool_if_current(self, context: EpochContext) -> None:
        with self._lock:
            if self._active_context == context:
                self._state.active_tool_task = None

    def _emit(self, event: str, context: EpochContext, **fields: Any) -> None:
        if self._event_sink is None:
            return
        payload = {
            "event": event,
            "timestamp": time.time(),
            "epoch": context.epoch,
            "batch_id": context.batch_id,
            **fields,
        }
        try:
            self._event_sink(payload)
        except Exception:
            pass
