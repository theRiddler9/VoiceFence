import threading

from src.batch_registry import BatchRegistry
from src.order_store import LookupResult
from src.orchestrator import (
    EpochOrchestrator,
    OrchestratorResult,
    UpdateHandle,
)


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


class TimeoutEventGate:
    def __init__(self):
        self.events = []
        self.timeout_emitted = threading.Event()
        self.release_timeout = threading.Event()

    def __call__(self, event):
        self.events.append(event)
        if event["event"] == "tool-timeout":
            self.timeout_emitted.set()
            assert self.release_timeout.wait(1)


class ExpiredWaitGate:
    """Return an expired wait only after the test owns the orchestrator lock."""

    def __init__(self):
        self.entered = threading.Event()
        self.release_wait = threading.Event()
        self.returning = threading.Event()

    def wait(self, timeout=None):
        self.entered.set()
        assert self.release_wait.wait(1)
        self.returning.set()
        return False

    def set(self):
        return None


def test_epochs_are_monotonic_and_interrupt_cancels_active_batch():
    registry = BatchRegistry()
    store = ControlledStore(registry)
    speaker = RecordingSpeaker(registry)
    orchestrator = EpochOrchestrator(registry, store, speaker)

    first = orchestrator.begin_turn()
    orchestrator.interrupt()
    second = orchestrator.begin_turn()

    assert second.epoch == first.epoch + 1
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


def test_old_timeout_watcher_cannot_cancel_a_newer_epoch():
    """Catches an unscoped interrupt after timeout eligibility was checked."""
    registry = BatchRegistry()
    speaker = RecordingSpeaker(registry)
    event_gate = TimeoutEventGate()
    orchestrator = EpochOrchestrator(
        registry,
        ControlledStore(registry),
        speaker,
        tool_timeout_seconds=0.01,
        event_sink=event_gate,
    )
    old_context = orchestrator.begin_turn()
    old_handle = UpdateHandle(old_context)
    watcher = threading.Thread(
        target=orchestrator._watch_timeout,
        args=(old_context, old_handle),
    )
    watcher.start()
    assert event_gate.timeout_emitted.wait(1)

    newer_context = orchestrator.begin_turn()
    event_gate.release_timeout.set()
    watcher.join(1)

    assert not watcher.is_alive()
    assert orchestrator.is_current(newer_context) is True
    assert registry.is_cancelled(newer_context.batch_id) is False
    assert speaker.calls == []
    orchestrator.interrupt(reason="test-cleanup")


def test_timeout_watcher_cannot_speak_after_result_was_completed():
    """Catches timeout side effects after losing the handle-result race."""
    registry = BatchRegistry()
    speaker = RecordingSpeaker(registry)
    events = []
    orchestrator = EpochOrchestrator(
        registry,
        ControlledStore(registry),
        speaker,
        tool_timeout_seconds=1.0,
        event_sink=events.append,
    )
    context = orchestrator.begin_turn()
    handle = UpdateHandle(context)
    wait_gate = ExpiredWaitGate()
    handle._done = wait_gate
    watcher = threading.Thread(
        target=orchestrator._watch_timeout,
        args=(context, handle),
    )
    watcher.start()
    assert wait_gate.entered.wait(1)

    orchestrator._lock.acquire()
    try:
        wait_gate.release_wait.set()
        assert wait_gate.returning.wait(1)
        completed = OrchestratorResult(
            context,
            "completed",
            {"address": "Completed Address"},
        )
        assert handle._set_result(completed) is True
    finally:
        orchestrator._lock.release()

    watcher.join(1)

    assert not watcher.is_alive()
    assert handle.result == completed
    assert orchestrator.is_current(context) is True
    assert speaker.calls == []
    assert not any(event["event"] == "tool-timeout" for event in events)
    orchestrator.interrupt(reason="test-cleanup")


def test_event_sink_receives_epoch_events():
    events = []
    registry = BatchRegistry()
    store = ControlledStore(registry)
    speaker = RecordingSpeaker(registry)
    orchestrator = EpochOrchestrator(
        registry,
        store,
        speaker,
        event_sink=events.append,
    )

    handle = orchestrator.start_address_update("Event Address")
    assert store.started["Event Address"].wait(1)
    store.release["Event Address"].set()

    assert handle.join(1).status == "completed"
    assert handle.wait_for_tts(1) is True
    assert any(event["event"] == "epoch-started" for event in events)
    assert any(event["event"] == "tts-started" for event in events)
