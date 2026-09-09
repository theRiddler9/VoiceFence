import threading

import pytest

import voice_demo
from voice_demo import run_demo


def test_voice_demo_interrupts_stale_turn_and_commits_correction():
    """Catches a demo that allows the interrupted address to commit or speak."""
    result = run_demo()

    assert result["final_order"]["address"] == "221B Baker Street, London"
    assert result["spoken"] == [
        "Sure, I've updated your address to 221B Baker Street, London."
    ]
    assert any(event["event"] == "barge-in-detected" for event in result["events"])


def test_voice_demo_releases_gated_workers_when_the_correction_pipeline_fails(
    monkeypatch,
):
    """Catches a demo failure path that leaves its daemon workers blocked."""

    captured = {}

    class CapturingStore(voice_demo.DeterministicOrderStore):
        def __init__(self, registry):
            super().__init__(registry)
            self.finished = {
                voice_demo._FIRST_ADDRESS: threading.Event(),
                voice_demo._CORRECTED_ADDRESS: threading.Event(),
            }
            captured["store"] = self

        def update_address(self, new_address, batch_id):
            try:
                return super().update_address(new_address, batch_id)
            finally:
                self.finished[new_address].set()

    original_on_transcript = voice_demo.VoicePipeline.on_transcript

    def fail_after_corrected_intent(self, event):
        original_on_transcript(self, event)
        if event.text.endswith(voice_demo._CORRECTED_ADDRESS):
            raise RuntimeError("simulated correction pipeline failure")

    monkeypatch.setattr(voice_demo, "DeterministicOrderStore", CapturingStore)
    monkeypatch.setattr(
        voice_demo.VoicePipeline,
        "on_transcript",
        fail_after_corrected_intent,
    )

    with pytest.raises(RuntimeError, match="simulated correction pipeline failure"):
        run_demo()

    assert captured["store"].finished[voice_demo._FIRST_ADDRESS].wait(0.5)
    assert captured["store"].finished[voice_demo._CORRECTED_ADDRESS].wait(0.5)
