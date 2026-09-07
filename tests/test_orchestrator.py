import threading

from src.batch_registry import BatchRegistry
from src.order_store import LookupResult
from src.orchestrator import EpochOrchestrator


class ControlledStore:
    """A deterministic store double that releases one address at a time."""

    def __init__(self, registry):
        self.registry = registry
        self.started = {}
        self.release = {}
        self.address = "123 Placeholder St, Springfield"

    def update_address(self, new_address, batch_id):
        self.started.setdefault(new_address, threading.Event()).set()
        gate = self.release.setdefault(new_address, threading.Event())
        gate.wait(timeout=2)
        if self.registry.is_cancelled(batch_id):
            return LookupResult(batch_id, "cancelled", None)
        self.address = new_address
        return LookupResult(
            batch_id,
            "completed",
            {"address": new_address, "eta_minutes": 30, "status": "updated"},
        )


class RecordingSpeaker:
    def __init__(self, registry, *, hold=False):
        self.registry = registry
        self.hold = hold
        self.calls = []
        self.started = threading.Event()

    def speak(self, text, batch_id, blocking=True):
        self.calls.append((text, batch_id))
        self.started.set()

        def run():
            if self.hold:
                while not self.registry.is_cancelled(batch_id):
                    threading.Event().wait(0.01)

        if blocking:
            run()
            return None
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread


class NeverEndingStore:
    def __init__(self, registry):
        self.registry = registry
        self.started = threading.Event()

    def update_address(self, new_address, batch_id):
        self.started.set()
        while not self.registry.is_cancelled(batch_id):
            threading.Event().wait(0.01)
        return LookupResult(batch_id, "cancelled", None)


def test_epochs_are_monotonic_and_interrupt_cancels_active_batch():
    registry = BatchRegistry()
    store = ControlledStore(registry)
    speaker = RecordingSpeaker(registry)
    orchestrator = EpochOrchestrator(registry, store, speaker)

    first = orchestrator.begin_turn()
    orchestrator.interrupt()
    second = orchestrator.begin_turn()

    assert first.epoch < second.epoch
    assert registry.is_cancelled(first.batch_id) is True
    assert orchestrator.current_epoch == second.epoch


def test_stale_lookup_is_dropped_and_corrected_turn_wins():
    registry = BatchRegistry()
    store = ControlledStore(registry)
    speaker = RecordingSpeaker(registry)
    orchestrator = EpochOrchestrator(registry, store, speaker)

    stale = orchestrator.start_address_update("Stale Address")
    assert store.started["Stale Address"].wait(1)

    orchestrator.on_barge_in()
    current = orchestrator.start_address_update("Correct Address")
    assert store.started["Correct Address"].wait(1)

    store.release["Stale Address"].set()
    store.release["Correct Address"].set()

    assert stale.join(1).status == "stale-dropped"
    assert current.join(1).status == "completed"
    assert current.wait_for_tts(1) is True
    assert store.address == "Correct Address"
    assert [text for text, _ in speaker.calls] == [
        "Sure, I've updated your address to Correct Address."
    ]


def test_interrupt_cancels_current_tts_batch():
    registry = BatchRegistry()
    store = ControlledStore(registry)
    speaker = RecordingSpeaker(registry, hold=True)
    orchestrator = EpochOrchestrator(registry, store, speaker)

    handle = orchestrator.start_address_update("42 Wallaby Way")
    assert store.started["42 Wallaby Way"].wait(1)
    store.release["42 Wallaby Way"].set()

    assert handle.join(1).status == "completed"
    assert speaker.started.wait(1)
    assert handle.tts_thread is not None
    assert handle.tts_thread.is_alive() is True

    orchestrator.on_barge_in()

    assert registry.is_cancelled(handle.context.batch_id) is True
    assert handle.wait_for_tts(1) is True


def test_timeout_speaks_explicit_failure_and_drops_late_tool_result():
    registry = BatchRegistry()
    store = NeverEndingStore(registry)
    speaker = RecordingSpeaker(registry)
    orchestrator = EpochOrchestrator(
        registry,
        store,
        speaker,
        tool_timeout_seconds=0.05,
    )

    handle = orchestrator.start_address_update("Slow Address")
    assert store.started.wait(1)

    result = handle.join(1)
    assert result is not None
    assert result.status == "timeout"
    assert result.error == "address lookup timed out"
    assert handle.wait_for_tts(1) is True
    assert [text for text, _ in speaker.calls] == [
        "I couldn't confirm that address. Can you repeat it?"
    ]
