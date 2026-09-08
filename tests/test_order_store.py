import threading
import time

from src.batch_registry import BatchRegistry
from src.order_store import OrderStore


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
