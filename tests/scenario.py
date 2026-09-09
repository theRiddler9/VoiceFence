"""
The one scenario every teammate's doc points at:

    "a slow lookup starts, then partway through, the user changes their
    mind with a new address" — and the system must (a) go silent fast,
    (b) never let the old address reach the store, and (c) end up with
    the new address confirmed.

This module plays that scenario out against the *real* BatchRegistry,
OrderStore, and RimeSpeaker (only the network call and audio device are
faked — see tests/fakes.py), acting as the user directly instead of
needing a microphone. It's deliberately just a function, not a pytest
test, so both pytest (test_interruption_scenario.py) and a standalone
repeated-run CLI (run_scenario_report.py) can call it identically.

Every observation is logged as it happens; the returned dict is a
convenience for immediate assertions, but it's derived from — and must
agree with — the log, not the other way around.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from src.batch_registry import BatchRegistry
from src.order_store import OrderStore
from src.rime_speaker import RimeSpeaker

from tests.event_log import EventLogger
from tests.fakes import FakeRimeSession, RecordingSink


OLD_ADDRESS = "111 Old Ave, Springfield"
NEW_ADDRESS = "222 New Blvd, Shelbyville"


@dataclass
class ScenarioConfig:
    lookup_delay_seconds: float = 0.6      # OrderStore's artificial delay
    lookup_poll_interval: float = 0.02
    speak_num_chunks: int = 30             # fake TTS stream length
    speak_chunk_size: int = 320
    speak_chunk_delay: float = 0.03        # per-chunk streaming delay
    interrupt_after_seconds: float = 0.25  # when the "user" interrupts
    # Akash's spec: "a tight time window (e.g., a few hundred
    # milliseconds)" for audio to go silent after interrupt.
    max_silence_ms: float = 300.0


@dataclass
class ScenarioResult:
    run_id: str
    passed: bool
    failures: list = field(default_factory=list)
    lookup1_status: Optional[str] = None
    speak1_status: Optional[str] = None
    lookup2_status: Optional[str] = None
    speak2_status: Optional[str] = None
    time_to_audio_silence_ms: Optional[float] = None
    final_address: Optional[str] = None
    stale_address_leaked: bool = False


def run_interruption_scenario(
    run_id: str,
    logger: EventLogger,
    cfg: ScenarioConfig = ScenarioConfig(),
) -> ScenarioResult:
    registry = BatchRegistry()
    order_store = OrderStore(
        registry,
        delay_seconds=cfg.lookup_delay_seconds,
        poll_interval=cfg.lookup_poll_interval,
    )

    lookup_results: dict = {}
    speak_results: dict = {}
    sinks: dict = {}

    def do_lookup(address, batch_id, key):
        lookup_results[key] = order_store.update_address(address, batch_id)

    def make_speaker(batch_id, key):
        sink_holder = {}

        def sink_factory(_samplerate):
            sink = RecordingSink(logger=logger, batch_id=batch_id)
            sink_holder["sink"] = sink
            sinks[key] = sink
            return sink

        speaker = RimeSpeaker(
            registry,
            api_key="test-key",
            sink_factory=sink_factory,
            session=FakeRimeSession(
                num_chunks=cfg.speak_num_chunks,
                chunk_size=cfg.speak_chunk_size,
                chunk_delay=cfg.speak_chunk_delay,
            ),
        )
        return speaker

    # --- Turn 1: user states the "wrong" address; a slow lookup and a
    # spoken confirmation both start, tagged with batch1. ------------
    batch1 = registry.new_batch_id()
    logger.log("batch_started", batch_id=batch1, action="lookup+speak", address=OLD_ADDRESS)

    lookup1_thread = threading.Thread(target=do_lookup, args=(OLD_ADDRESS, batch1, "b1"))
    speaker1 = make_speaker(batch1, "b1")
    speak1_thread = threading.Thread(
        target=lambda: speak_results.__setitem__(
            "b1", speaker1.speak(f"Confirming your address as {OLD_ADDRESS}", batch1)
        )
    )
    lookup1_thread.start()
    speak1_thread.start()

    # --- Mid-flight: the user interrupts with a correction. ----------
    time.sleep(cfg.interrupt_after_seconds)
    logger.log("interrupt_detected", batch_id=batch1)

    t_cancel = time.monotonic()
    registry.cancel(batch1)
    logger.log("batch_cancelled", batch_id=batch1)

    # --- Turn 2: the corrected address, tagged with a fresh batch. ---
    batch2 = registry.new_batch_id()
    logger.log("batch_started", batch_id=batch2, action="lookup+speak", address=NEW_ADDRESS)

    lookup2_thread = threading.Thread(target=do_lookup, args=(NEW_ADDRESS, batch2, "b2"))
    speaker2 = make_speaker(batch2, "b2")
    speak2_thread = threading.Thread(
        target=lambda: speak_results.__setitem__(
            "b2", speaker2.speak(f"Got it, updating your address to {NEW_ADDRESS}", batch2)
        )
    )
    lookup2_thread.start()
    speak2_thread.start()

    for t in (lookup1_thread, speak1_thread, lookup2_thread, speak2_thread):
        t.join(timeout=cfg.lookup_delay_seconds + cfg.speak_num_chunks * cfg.speak_chunk_delay + 5)

    lookup1 = lookup_results.get("b1")
    lookup2 = lookup_results.get("b2")
    speak1 = speak_results.get("b1")
    speak2 = speak_results.get("b2")
    sink1 = sinks.get("b1")

    logger.log("lookup_result", batch_id=batch1, status=getattr(lookup1, "status", None))
    logger.log("lookup_result", batch_id=batch2, status=getattr(lookup2, "status", None))
    logger.log("speak_result", batch_id=batch1, status=getattr(speak1, "status", None),
               bytes_played=getattr(speak1, "bytes_played", None))
    logger.log("speak_result", batch_id=batch2, status=getattr(speak2, "status", None),
               bytes_played=getattr(speak2, "bytes_played", None))

    time_to_silence_ms = None
    if sink1 is not None and sink1.aborted and sink1.abort_ts is not None:
        time_to_silence_ms = round((sink1.abort_ts - t_cancel) * 1000, 3)

    final_order = order_store.get_order()
    logger.log("final_order_state", **final_order)

    # --- Pass/fail checks -------------------------------------------
    failures = []

    if lookup1 is None or lookup1.status != "cancelled":
        failures.append(f"stale lookup not cancelled (batch1 lookup status={getattr(lookup1, 'status', None)})")

    if speak1 is None or speak1.status != "cancelled":
        failures.append(f"stale speech not cancelled (batch1 speak status={getattr(speak1, 'status', None)})")

    stale_leaked = final_order.get("address") == OLD_ADDRESS
    if stale_leaked:
        failures.append("stale address reached the order store")

    if final_order.get("address") != NEW_ADDRESS:
        failures.append(f"final address is not the corrected address (got {final_order.get('address')!r})")

    if lookup2 is None or lookup2.status != "completed":
        failures.append(f"corrected lookup did not complete (status={getattr(lookup2, 'status', None)})")

    if speak2 is None or speak2.status != "completed":
        failures.append(f"corrected speech did not complete (status={getattr(speak2, 'status', None)})")

    if time_to_silence_ms is None:
        failures.append("no audio-abort timestamp recorded — could not measure time-to-silence")
    elif time_to_silence_ms > cfg.max_silence_ms:
        failures.append(
            f"time-to-silence {time_to_silence_ms}ms exceeded budget of {cfg.max_silence_ms}ms"
        )
    elif time_to_silence_ms < 0:
        failures.append(f"time-to-silence was negative ({time_to_silence_ms}ms) — clock/ordering bug")

    passed = len(failures) == 0

    logger.log(
        "scenario_result",
        passed=passed,
        failures=failures,
        lookup1_status=getattr(lookup1, "status", None),
        speak1_status=getattr(speak1, "status", None),
        lookup2_status=getattr(lookup2, "status", None),
        speak2_status=getattr(speak2, "status", None),
        time_to_audio_silence_ms=time_to_silence_ms,
        final_address=final_order.get("address"),
        stale_address_leaked=stale_leaked,
    )

    return ScenarioResult(
        run_id=run_id,
        passed=passed,
        failures=failures,
        lookup1_status=getattr(lookup1, "status", None),
        speak1_status=getattr(speak1, "status", None),
        lookup2_status=getattr(lookup2, "status", None),
        speak2_status=getattr(speak2, "status", None),
        time_to_audio_silence_ms=time_to_silence_ms,
        final_address=final_order.get("address"),
        stale_address_leaked=stale_leaked,
    )
