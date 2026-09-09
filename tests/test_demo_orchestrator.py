"""
Exercises the ACTUAL orchestrator code in demo.py — not a reimplementation
of it — using the same fakes as the rest of the suite (no network, no
real audio device, no mic). This is what "wiring the orchestrator to the
tests" means: demo.run_interruption_demo() is the thing under test.

Complements tests/scenario.py, which independently drives the components
directly to specify what *should* happen. This file specifically checks
that demo.py's real orchestration wiring matches that spec.
"""
import uuid

import pytest

import demo
from src.batch_registry import BatchRegistry
from src.order_store import OrderStore
from src.rime_speaker import RimeSpeaker

from tests.event_log import EventLogger
from tests.fakes import FakeRimeSession, RecordingSink


def make_fake_speaker_factory(registry, num_chunks=20, chunk_delay=0.01, chunk_size=320, sinks=None):
    """Returns a zero-arg factory matching demo.run_interruption_demo's
    speaker_factory signature, each call producing a fresh RimeSpeaker
    wired to fake network + a recording (fake) sink."""

    def factory():
        def sink_factory(_samplerate):
            sink = RecordingSink()
            if sinks is not None:
                sinks.append(sink)
            return sink

        return RimeSpeaker(
            registry,
            api_key="test-key",
            sink_factory=sink_factory,
            session=FakeRimeSession(num_chunks=num_chunks, chunk_size=chunk_size, chunk_delay=chunk_delay),
        )

    return factory


def test_demo_orchestrator_cancels_stale_batch_and_applies_correction():
    registry = BatchRegistry()
    store = OrderStore(registry, delay_seconds=0.3, poll_interval=0.01)
    sinks = []
    speaker_factory = make_fake_speaker_factory(registry, sinks=sinks)

    result = demo.run_interruption_demo(
        registry, store, speaker_factory, interrupt_after_seconds=0.1
    )

    # batch_1 (interrupted) must be cancelled on both legs.
    assert result.lookup_1.status == "cancelled"
    assert result.speech_1.status == "cancelled"
    assert result.order_after_batch_1["address"] != demo.ADDRESS_1

    # batch_2 (corrected) must complete on both legs and win.
    assert result.lookup_2.status == "completed"
    assert result.speech_2.status == "completed"
    assert result.order_after_batch_2["address"] == demo.ADDRESS_2

    # The stale address must never appear in the final order state.
    assert result.order_after_batch_1["address"] != demo.ADDRESS_1
    assert result.order_after_batch_2["address"] != demo.ADDRESS_1


def test_demo_orchestrator_aborts_audio_for_the_cancelled_batch():
    registry = BatchRegistry()
    store = OrderStore(registry, delay_seconds=0.3, poll_interval=0.01)
    sinks = []
    speaker_factory = make_fake_speaker_factory(
        registry, num_chunks=40, chunk_delay=0.02, sinks=sinks
    )

    demo.run_interruption_demo(registry, store, speaker_factory, interrupt_after_seconds=0.1)

    # First sink (batch_1's speech) should have been aborted, not left to
    # finish playing out in the background.
    assert sinks[0].aborted is True
    # Second sink (batch_2's speech) should have completed normally.
    assert sinks[1].aborted is False
    assert sinks[1].closed is True


def test_demo_orchestrator_feeds_into_the_structured_evidence_log(tmp_path):
    """Confirms demo.py's `log` hook can be wired straight into
    EventLogger, so a real orchestrator run — not just the synthetic
    tests/scenario.py — can also produce an evidence log/report."""
    log_path = tmp_path / "demo_events.jsonl"
    run_id = str(uuid.uuid4())
    logger = EventLogger(log_path, run_id=run_id)

    registry = BatchRegistry()
    store = OrderStore(registry, delay_seconds=0.2, poll_interval=0.01)
    speaker_factory = make_fake_speaker_factory(registry)

    try:
        result = demo.run_interruption_demo(
            registry,
            store,
            speaker_factory,
            interrupt_after_seconds=0.08,
            log=lambda msg: logger.log("demo_step", message=msg),
        )
    finally:
        logger.close()

    assert result.lookup_1.status == "cancelled"

    from tests.event_log import read_events
    events = read_events(log_path)
    assert any(e["event"] == "demo_step" for e in events)
    assert all(e["run_id"] == run_id for e in events)
