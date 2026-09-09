import json

from src.interface_bridge import RuntimeEventBridge


def test_runtime_event_bridge_writes_sanitized_jsonl(tmp_path):
    bridge = RuntimeEventBridge(tmp_path / "events.jsonl")

    bridge({
        "event": "tool-completed",
        "epoch": 2,
        "batch_id": "batch-2",
        "order": {"address": "221B Baker Street, London"},
    })

    record = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8"))
    assert record["event"] == "tool-completed"
    assert record["epoch"] == 2
    assert record["order"]["address"] == "221B Baker Street, London"
    assert record["run_id"]
    assert isinstance(record["timestamp"], float)