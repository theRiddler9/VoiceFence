"""
Standalone demo/smoke test. Stands in for Person A's orchestrator to show
that both components honor a cancel mid-flight.

Scenario, matching the customer-service phone analogy in the spec:
  1. Orchestrator says "look up address #1" under batch-1, and starts
     speaking a confirmation under batch-1.
  2. Midway through, the person says "actually, make it address #2".
     Orchestrator cancels batch-1 and starts fresh work under batch-2.
  3. batch-1's lookup and speech should both come back "cancelled" and
     never be applied. batch-2 should complete normally.

Run with: python demo.py
(Needs RIME_API_KEY set in the environment for the speech leg to
actually call Rime. Runs fine without it too — the order-store half of
the demo doesn't touch the network, and the speech leg will just report
an "error" status for the missing key instead of crashing the demo.)
"""
import threading
import time

from src.batch_registry import BatchRegistry
from src.order_store import OrderStore
from src.rime_speaker import RimeSpeaker


def main():
    registry = BatchRegistry()
    store = OrderStore(registry, delay_seconds=3.0)
    speaker = RimeSpeaker(registry)

    batch_1 = registry.new_batch_id()
    print(f"[orchestrator] batch {batch_1}: updating address, speaking confirmation")

    lookup_result = {}
    speech_result = {}

    def do_lookup():
        lookup_result["value"] = store.update_address("42 Wallaby Way, Sydney", batch_1)

    def do_speech():
        speech_result["value"] = speaker.speak(
            "Sure, I've updated your address to 42 Wallaby Way, Sydney.", batch_1
        )

    t_lookup = threading.Thread(target=do_lookup)
    t_speech = threading.Thread(target=do_speech)
    t_lookup.start()
    t_speech.start()

    # Person interrupts partway through.
    time.sleep(1.0)
    print(f"[orchestrator] person interrupts -> cancelling {batch_1}")
    registry.cancel(batch_1)

    t_lookup.join()
    t_speech.join()

    print(f"[result] lookup: {lookup_result['value']}")
    print(f"[result] speech: {speech_result['value']}")
    print(f"[order store] current order (should be untouched): {store.get_order()}")

    # Now the corrected request, under a fresh batch id.
    batch_2 = registry.new_batch_id()
    print(f"\n[orchestrator] batch {batch_2}: person's real address")
    result_2 = store.update_address("221B Baker Street, London", batch_2)
    print(f"[result] lookup: {result_2}")
    print(f"[order store] current order (should be updated): {store.get_order()}")


if __name__ == "__main__":
    main()
