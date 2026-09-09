"""Credential-free end-to-end VoiceFence overlap demonstration.

Run ``python voice_demo.py`` to exercise the real transcript pipeline and
epoch orchestrator without LiveKit, provider credentials, audio hardware, or
network access.  The two store operations are held behind explicit events so
the interrupted update cannot win a timing race.
"""
from __future__ import annotations

import threading
from typing import Any

from src.batch_registry import BatchRegistry
from src.orchestrator import EpochOrchestrator, OrchestratorResult
from src.order_store import OrderStore
from src.stt_client import TranscriptEvent, VoicePipeline


_FIRST_ADDRESS = "42 Wallaby Way, Sydney"
_CORRECTED_ADDRESS = "221B Baker Street, London"
_WORKER_TIMEOUT_SECONDS = 2.0


class _CompletedRecordingTask:
    """Minimal completed-task surface expected by ``EpochOrchestrator``."""

    def join(self, timeout: float | None = None) -> None:
        return None

    def is_alive(self) -> bool:
        return False


class RecordingSpeaker:
    """Local confirmation sink that never opens an audio device or network."""

    def __init__(self, registry: BatchRegistry):
        self._registry = registry
        self._lock = threading.Lock()
        self._spoken: list[str] = []

    @property
    def spoken(self) -> list[str]:
        with self._lock:
            return list(self._spoken)

    def speak(
        self,
        text: str,
        batch_id: str,
        blocking: bool = True,
    ) -> _CompletedRecordingTask:
        # The orchestrator already fences this call.  Keep the same safety
        # boundary at the local sink so a cancelled batch is never recorded.
        if not self._registry.is_cancelled(batch_id):
            with self._lock:
                self._spoken.append(text)
        return _CompletedRecordingTask()


class DeterministicOrderStore(OrderStore):
    """Real in-memory order store with explicit gates around its two updates."""

    def __init__(self, registry: BatchRegistry):
        super().__init__(registry, delay_seconds=0.0)
        self._started = {
            _FIRST_ADDRESS: threading.Event(),
            _CORRECTED_ADDRESS: threading.Event(),
        }
        self._release = {
            _FIRST_ADDRESS: threading.Event(),
            _CORRECTED_ADDRESS: threading.Event(),
        }

    def wait_until_started(self, address: str) -> None:
        if not self._started[address].wait(_WORKER_TIMEOUT_SECONDS):
            raise RuntimeError(f"store work for {address!r} did not start")

    def release(self, address: str) -> None:
        self._release[address].set()

    def update_address(self, new_address: str, batch_id: str):
        try:
            started = self._started[new_address]
            release = self._release[new_address]
        except KeyError as exc:
            raise RuntimeError(f"unexpected demo address: {new_address!r}") from exc

        started.set()
        if not release.wait(_WORKER_TIMEOUT_SECONDS):
            raise RuntimeError(f"store work for {new_address!r} was not released")
        return super().update_address(new_address, batch_id)


def _serialize_result(result: OrchestratorResult) -> dict[str, Any]:
    return {
        "context": {
            "epoch": result.context.epoch,
            "batch_id": result.context.batch_id,
        },
        "status": result.status,
        "order": dict(result.order) if result.order is not None else None,
        "error": result.error,
    }


def _wait_for_result(label: str, handle: Any) -> OrchestratorResult:
    result = handle.join(_WORKER_TIMEOUT_SECONDS)
    if result is None:
        raise RuntimeError(f"{label} worker did not finish")
    if not handle.wait_for_tts(_WORKER_TIMEOUT_SECONDS):
        raise RuntimeError(f"{label} confirmation worker did not finish")
    return result


def run_demo() -> dict[str, Any]:
    """Run a fenced overlap/correction scenario and return serializable evidence."""

    registry = BatchRegistry()
    store = DeterministicOrderStore(registry)
    speaker = RecordingSpeaker(registry)
    orchestrator = EpochOrchestrator(
        registry,
        store,
        speaker,
        tool_timeout_seconds=_WORKER_TIMEOUT_SECONDS,
    )
    events: list[dict[str, Any]] = []
    handles: list[Any] = []

    def on_address_intent(address: str) -> None:
        handles.append(orchestrator.on_address_intent(address))

    pipeline = VoicePipeline(
        on_barge_in=orchestrator.on_barge_in,
        on_address_intent=on_address_intent,
        event_sink=events.append,
    )

    # First transcript starts delayed store work.  The event confirms the
    # worker is blocked before we create the speech overlap.
    pipeline.on_transcript(TranscriptEvent(f"make it {_FIRST_ADDRESS}", True, 1.0))
    store.wait_until_started(_FIRST_ADDRESS)

    pipeline.set_assistant_speaking(True)
    pipeline.on_user_speech_started(timestamp=2.0)
    pipeline.on_transcript(
        TranscriptEvent(f"make it {_CORRECTED_ADDRESS}", True, 2.1)
    )
    store.wait_until_started(_CORRECTED_ADDRESS)

    # Both releases are deterministic.  The first now observes its cancelled
    # batch in the real OrderStore; the corrected request commits and speaks.
    store.release(_FIRST_ADDRESS)
    store.release(_CORRECTED_ADDRESS)

    if len(handles) != 2:
        raise RuntimeError(f"expected two address workers, received {len(handles)}")
    first_result = _wait_for_result("first address", handles[0])
    corrected_result = _wait_for_result("corrected address", handles[1])
    if first_result.status != "stale-dropped":
        raise RuntimeError(f"first address was not stale-dropped: {first_result.status}")
    if corrected_result.status != "completed":
        raise RuntimeError(
            f"corrected address did not complete: {corrected_result.status}"
        )

    return {
        "final_order": store.get_order(),
        "results": {
            "first": _serialize_result(first_result),
            "corrected": _serialize_result(corrected_result),
        },
        "spoken": speaker.spoken,
        "events": list(events),
    }


def main() -> None:
    """Print the serializable demo evidence for quick local inspection."""
    import json

    print(json.dumps(run_demo(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
