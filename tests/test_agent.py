import threading

from src.batch_registry import BatchRegistry
from src.orchestrator import EpochOrchestrator
from src.order_store import OrderStore
from src.stt_client import TranscriptEvent


class RecordingOrchestrator:
    def __init__(self):
        self.calls = []

    def on_barge_in(self):
        self.calls.append(("barge_in",))

    def on_address_intent(self, address):
        self.calls.append(("address", address))


class RecordingSpeaker:
    def __init__(self):
        self.calls = []
        self.confirmed = threading.Event()

    def speak(self, text, batch_id, blocking=True):
        self.calls.append((text, batch_id))
        self.confirmed.set()
        return None


def test_pipeline_adapter_calls_existing_orchestrator_contract():
    """Catches an adapter that drops barge-ins or passes timestamps as arguments."""
    from src.agent import build_voice_pipeline

    orchestrator = RecordingOrchestrator()
    pipeline = build_voice_pipeline(orchestrator)

    pipeline.set_assistant_speaking(True)
    pipeline.on_user_speech_started(timestamp=30.0)
    pipeline.on_transcript(
        TranscriptEvent("actually make it 10 Downing Street", True, 30.2)
    )

    assert orchestrator.calls == [
        ("barge_in",),
        ("address", "10 Downing Street"),
    ]


def test_pipeline_handoff_keeps_only_corrected_address_and_confirmation():
    """Catches voice wiring that lets a barged-in turn commit or confirm."""
    from src.agent import build_voice_pipeline

    registry = BatchRegistry()
    store = OrderStore(registry, delay_seconds=0.05)
    speaker = RecordingSpeaker()
    orchestrator = EpochOrchestrator(registry, store, speaker)
    pipeline = build_voice_pipeline(orchestrator)

    pipeline.on_transcript(TranscriptEvent("make it 1 First Street", True, 40.0))
    pipeline.set_assistant_speaking(True)
    pipeline.on_user_speech_started(timestamp=40.1)
    pipeline.on_transcript(TranscriptEvent("make it 10 Downing Street", True, 40.2))

    assert speaker.confirmed.wait(1)
    assert store.get_order()["address"] == "10 Downing Street"
    assert [text for text, _ in speaker.calls] == [
        "Sure, I've updated your address to 10 Downing Street."
    ]
