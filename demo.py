"""
Standalone demo/smoke test for the Epoch orchestrator.

Scenario, matching the customer-service phone analogy in the spec:
  1. Orchestrator starts a delayed lookup for address #1.
  2. Midway through, the person says "actually, make it address #2".
     Orchestrator cancels batch-1 and starts fresh work under batch-2:
     a new lookup and a new spoken confirmation.
  3. batch-1's lookup and speech should both come back "cancelled" and
     never be applied. batch-2 should complete normally, on both legs.

Run with: python demo.py
(Needs RIME_API_KEY set in the environment for the speech legs to
actually call Rime. Runs fine without it too — the order-store half of
the demo doesn't touch the network, and the speech legs will just report
an "error" status for the missing key instead of crashing the demo.)

The orchestration logic lives in run_interruption_demo() so it can be
exercised directly by tests (see tests/test_demo_orchestrator.py) with
fake network/audio dependencies injected, instead of only being
reachable via `python demo.py`.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from src.batch_registry import BatchRegistry
from src.order_store import OrderStore
from src.rime_speaker import RimeSpeaker


ADDRESS_1 = "42 Wallaby Way, Sydney"
ADDRESS_2 = "221B Baker Street, London"


@dataclass
class DemoRunResult:
    batch_1: str
    batch_2: str
    lookup_1: object
    speech_1: object
    lookup_2: object
    speech_2: object
    order_after_batch_1: dict
    order_after_batch_2: dict


def run_interruption_demo(
    registry: BatchRegistry,
    store: OrderStore,
    speaker_factory: Callable[[], RimeSpeaker],
    interrupt_after_seconds: float = 1.0,
    log: Optional[Callable[[str], None]] = None,
) -> DemoRunResult:
    """The actual orchestration logic: stamp batch_1, run a lookup +
    speech confirmation, interrupt mid-flight, stamp batch_2, run the
    corrected lookup + confirmation.

    `speaker_factory` is a zero-arg callable returning a RimeSpeaker —
    a factory rather than a single shared instance so tests can hand
    back a fresh fake session/sink per call if they want to, the same
    way the real orchestrator would construct one per turn (or reuse a
    long-lived one; either works since RimeSpeaker itself holds no
    per-call state).

    `log`, if given, is called with a short human-readable string at
    each notable step — this is intentionally decoupled from
    tests.event_log.EventLogger so demo.py has no test-only dependency,
    but a caller (like a test) can pass a wrapper around logger.log to
    get everything into the structured JSONL log too.
    """
    def emit(msg: str) -> None:
        if log:
            log(msg)

    batch_1 = registry.new_batch_id()
    emit(f"batch {batch_1}: updating address, speaking confirmation")

    speaker_1 = speaker_factory()
    lookup_result = {}
    speech_result = {}

    def do_lookup():
        lookup_result["value"] = store.update_address(ADDRESS_1, batch_1)

    def do_speech():
        speech_result["value"] = speaker_1.speak(
            f"Sure, I've updated your address to {ADDRESS_1}.", batch_1
        )

    t_lookup = threading.Thread(target=do_lookup)
    t_speech = threading.Thread(target=do_speech)
    t_lookup.start()
    t_speech.start()

    # Person interrupts partway through.
    time.sleep(interrupt_after_seconds)
    emit(f"person interrupts -> cancelling {batch_1}")
    registry.cancel(batch_1)

    t_lookup.join()
    t_speech.join()

    emit(f"lookup: {lookup_result['value']}")
    emit(f"speech: {speech_result['value']}")
    order_after_batch_1 = store.get_order()
    emit(f"order store (should be untouched): {order_after_batch_1}")

    # Now the corrected request, under a fresh batch id — lookup AND a
    # spoken confirmation, mirroring batch_1's shape.
    batch_2 = registry.new_batch_id()
    emit(f"batch {batch_2}: person's real address")

    speaker_2 = speaker_factory()
    lookup_2_result = {}
    speech_2_result = {}

    def do_lookup_2():
        lookup_2_result["value"] = store.update_address(ADDRESS_2, batch_2)

    def do_speech_2():
        speech_2_result["value"] = speaker_2.speak(
            f"Got it, updating your address to {ADDRESS_2}.", batch_2
        )

    t_lookup_2 = threading.Thread(target=do_lookup_2)
    t_speech_2 = threading.Thread(target=do_speech_2)
    t_lookup_2.start()
    t_speech_2.start()
    t_lookup_2.join()
    t_speech_2.join()

    emit(f"lookup: {lookup_2_result['value']}")
    emit(f"speech: {speech_2_result['value']}")
    order_after_batch_2 = store.get_order()
    emit(f"order store (should be updated): {order_after_batch_2}")

    return DemoRunResult(
        batch_1=batch_1,
        batch_2=batch_2,
        lookup_1=lookup_result["value"],
        speech_1=speech_result["value"],
        lookup_2=lookup_2_result["value"],
        speech_2=speech_2_result["value"],
        order_after_batch_1=order_after_batch_1,
        order_after_batch_2=order_after_batch_2,
    )


def main():
    registry = BatchRegistry()
    store = OrderStore(registry, delay_seconds=3.0)

    def make_speaker():
        return RimeSpeaker(registry)

    def log(msg: str) -> None:
        print(f"[orchestrator] {msg}")

    result = run_interruption_demo(registry, store, make_speaker, log=log)

    print(f"\n[final] batch_1={result.batch_1} lookup={result.lookup_1}")
    print(f"[final] batch_1={result.batch_1} speech={result.speech_1}")
    print(f"[final] batch_2={result.batch_2} lookup={result.lookup_2}")
    print(f"[final] batch_2={result.batch_2} speech={result.speech_2}")
    print(f"[final] order store: {result.order_after_batch_2}")


if __name__ == "__main__":
    main()