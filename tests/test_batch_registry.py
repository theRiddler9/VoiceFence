from src.batch_registry import BatchRegistry


def test_not_cancelled_by_default():
    reg = BatchRegistry()
    assert reg.is_cancelled("batch-1") is False


def test_cancel_marks_batch():
    reg = BatchRegistry()
    reg.cancel("batch-1")
    assert reg.is_cancelled("batch-1") is True
    assert reg.is_cancelled("batch-2") is False


def test_cancel_is_idempotent():
    reg = BatchRegistry()
    reg.cancel("batch-1")
    reg.cancel("batch-1")
    assert reg.is_cancelled("batch-1") is True


def test_clear_resets_batch():
    reg = BatchRegistry()
    reg.cancel("batch-1")
    reg.clear("batch-1")
    assert reg.is_cancelled("batch-1") is False


def test_new_batch_id_is_unique():
    ids = {BatchRegistry.new_batch_id() for _ in range(50)}
    assert len(ids) == 50
