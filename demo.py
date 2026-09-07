"""
Standalone demo/smoke test for the Epoch orchestrator.

Scenario, matching the customer-service phone analogy in the spec:
  1. Orchestrator starts a delayed lookup for address #1.
  2. Midway through, the person says "actually, make it address #2".
     Epoch invalidates the first batch and starts fresh work.
  3. The first result is dropped, and only address #2 is confirmed.

Run with: python demo.py
(Needs RIME_API_KEY set in the environment for the speech leg to
actually call Rime. Runs fine without it too — the order-store half of
the demo doesn't touch the network, and the speech leg will just report
an "error" status for the missing key instead of crashing the demo.)
"""
import time

from src.batch_registry import BatchRegistry
from src.order_store import OrderStore
from src.orchestrator import EpochOrchestrator
from src.rime_speaker import RimeSpeaker


def main():
    registry = BatchRegistry()
    store = OrderStore(registry, delay_seconds=3.0)
    speaker = RimeSpeaker(registry)
    orchestrator = EpochOrchestrator(
        registry,
        store,
        speaker,
        tool_timeout_seconds=4.0,
    )

    print("[orchestrator] starting first address update")
    first = orchestrator.start_address_update("42 Wallaby Way, Sydney")

    time.sleep(1.0)
    print("[orchestrator] person interrupts -> invalidating current epoch")
    orchestrator.on_barge_in()

    print("\n[orchestrator] starting corrected address update")
    second = orchestrator.on_address_intent("221B Baker Street, London")

    first_result = first.join(timeout=5)
    second_result = second.join(timeout=5)
    second.wait_for_tts(timeout=5)

    print(f"[result] first: {first_result}")
    print(f"[result] second: {second_result}")
    print(f"[order store] final order: {store.get_order()}")


if __name__ == "__main__":
    main()
