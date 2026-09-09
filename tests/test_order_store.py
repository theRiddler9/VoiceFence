import threading
import time

from src.batch_registry import BatchRegistry
from src.order_store import OrderStore


class CommitBoundaryRegistry(BatchRegistry):
    """Pause a store update at its final eligibility/commit boundary."""

    def __init__(self):
        super().__init__()
        self.eligibility_reached = threading.Event()
        self.release_commit = threading.Event()
        self.cancel_positioned = threading.Event()
        self.sequence = []
        self._checks = 0
        self.used_guard = False

    def is_cancelled(self, batch_id):
        result = super().is_cancelled(batch_id)
        self._checks += 1
        if self._checks == 2 and not self.used_guard:
            # This is the unguarded implementation's final check. Its registry
            # lock has already been released, exposing the stale-write gap.
            self.eligibility_reached.set()
            assert self.release_commit.wait(1)
        return result

    def run_if_active(self, batch_id, operation):
        self.used_guard = True

        def gated_operation():
            self.eligibility_reached.set()
            assert self.release_commit.wait(1)
            operation()
            self.sequence.append("commit")

        return super().run_if_active(batch_id, gated_operation)

    def cancel(self, batch_id):
        # Probe the real registry lock without timing assumptions. If the
        # commit guard owns it, cancellation queues behind the commit;
        # otherwise cancellation linearizes immediately.
        if self._lock.acquire(blocking=False):
            try:
                self._cancelled.add(batch_id)
            finally:
                self._lock.release()
            self.sequence.append("cancel")
            self.cancel_positioned.set()
            return

        self.cancel_positioned.set()
        super().cancel(batch_id)
        self.sequence.append("cancel")


def test_completes_normally_when_not_cancelled():
    reg = BatchRegistry()
    store = OrderStore(reg, delay_seconds=0.1)

    result = store.update_address("1 New Address", "batch-1")

    assert result.status == "completed"
    assert result.order["address"] == "1 New Address"
    assert store.get_order()["address"] == "1 New Address"


def test_cancelled_before_start_never_runs():
    reg = BatchRegistry()
    store = OrderStore(reg, delay_seconds=0.2)
    reg.cancel("batch-1")

    start = time.monotonic()
    result = store.update_address("1 New Address", "batch-1")
    elapsed = time.monotonic() - start

    assert result.status == "cancelled"
    assert result.order is None
    assert elapsed < 0.1
    assert store.get_order()["address"] != "1 New Address"


def test_cancel_mid_delay_is_ignored_and_leaves_order_untouched():
    reg = BatchRegistry()
    store = OrderStore(reg, delay_seconds=1.0, poll_interval=0.02)

    result_holder = {}

    def run():
        result_holder["result"] = store.update_address("Stale Address", "batch-1")

    t = threading.Thread(target=run)
    start = time.monotonic()
    t.start()
    time.sleep(0.15)
    reg.cancel("batch-1")
    t.join()
    elapsed = time.monotonic() - start

    assert result_holder["result"].status == "cancelled"
    assert result_holder["result"].order is None
    assert elapsed < 0.5
    original_address = store.get_order()["address"]
    assert original_address != "Stale Address"


def test_delay_is_configurable_per_instance():
    reg = BatchRegistry()
    fast_store = OrderStore(reg, delay_seconds=0.05)

    start = time.monotonic()
    result = fast_store.update_address("Quick Address", "batch-1")
    elapsed = time.monotonic() - start

    assert result.status == "completed"
    assert elapsed < 0.3


def test_cancellation_and_commit_are_linearized_at_final_eligibility_boundary():
    """Catches cancellation completing between the final check and write."""
    registry = CommitBoundaryRegistry()
    store = OrderStore(registry, delay_seconds=0.0)
    result_holder = {}

    def update():
        result_holder["result"] = store.update_address(
            "Atomic Address",
            "batch-race",
        )
        if not registry.used_guard:
            registry.sequence.append("commit")

    update_thread = threading.Thread(target=update)
    update_thread.start()
    assert registry.eligibility_reached.wait(1)

    cancel_thread = threading.Thread(
        target=registry.cancel,
        args=("batch-race",),
    )
    cancel_thread.start()
    assert registry.cancel_positioned.wait(1)

    registry.release_commit.set()
    update_thread.join(1)
    cancel_thread.join(1)

    assert not update_thread.is_alive()
    assert not cancel_thread.is_alive()
    assert registry.sequence == ["commit", "cancel"]
    assert result_holder["result"].status == "completed"
    assert store.get_order()["address"] == "Atomic Address"
    assert registry.is_cancelled("batch-race") is True
