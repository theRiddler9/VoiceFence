import asyncio
import threading
from types import SimpleNamespace

from src.batch_registry import BatchRegistry
from src.orchestrator import EpochOrchestrator
from src.order_store import OrderStore
from src.stt_client import TranscriptEvent


class RecordingPipeline:
    def __init__(self):
        self.transcripts = []
        self.actions = []

    def on_transcript(self, event):
        self.transcripts.append(event)

    def set_assistant_speaking(self, speaking):
        self.actions.append(("assistant", speaking))

    def on_user_speech_started(self, timestamp=None):
        self.actions.append(("user-start", timestamp))

    def on_user_speech_ended(self, timestamp=None):
        self.actions.append(("user-end", timestamp))


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


class FakeSTT:
    def __init__(self, **kwargs):
        self.options = kwargs


class FakeVAD:
    @staticmethod
    def load():
        return "silero-vad"


class MicrophoneGuard:
    def set_audio_enabled(self, enabled):
        raise AssertionError(f"microphone input was changed to {enabled}")


class FakeAgentSession:
    last = None

    def __init__(self, **kwargs):
        type(self).last = self
        self.options = kwargs
        self.handlers = {}
        self.input = MicrophoneGuard()
        self.started_with = None

    def on(self, event_name, handler):
        self.handlers[event_name] = handler

    async def start(self, **kwargs):
        self.started_with = kwargs
        kwargs["room"].lifecycle.append("start")


class FakeAgent:
    def __init__(self, **kwargs):
        self.options = kwargs


class FakeAgentServer:
    last = None

    def __init__(self):
        type(self).last = self
        self.entrypoint = None

    def rtc_session(self, entrypoint):
        self.entrypoint = entrypoint
        return entrypoint


class FakeCLI:
    def __init__(self):
        self.server = None

    def run_app(self, server):
        self.server = server


def fake_livekit_modules():
    cli = FakeCLI()
    agents = SimpleNamespace(
        Agent=FakeAgent,
        AgentServer=FakeAgentServer,
        AgentSession=FakeAgentSession,
        StopResponse=RuntimeError,
        cli=cli,
    )
    return agents, SimpleNamespace(STT=FakeSTT), SimpleNamespace(VAD=FakeVAD)


def set_runtime_environment(monkeypatch):
    for name in (
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "DEEPGRAM_API_KEY",
        "RIME_API_KEY",
    ):
        monkeypatch.setenv(name, f"test-{name.lower()}")


def test_transcript_adapter_preserves_partial_and_timestamp():
    """Catches an adapter that drops interim text or invents SDK timestamps."""
    from src.agent import handle_transcript_event

    pipeline = RecordingPipeline()
    handle_transcript_event(
        pipeline,
        SimpleNamespace(
            transcript="make it 1600 Pennsylvania Avenue",
            is_final=False,
        ),
        clock=lambda: 44.0,
    )

    assert pipeline.transcripts == [
        TranscriptEvent("make it 1600 Pennsylvania Avenue", False, 44.0)
    ]


def test_user_and_agent_state_mapping_keeps_overlap_observable():
    """Catches SDK state adapters that defer speech overlap until a transcript."""
    from src.agent import handle_agent_state, handle_user_state

    pipeline = RecordingPipeline()
    handle_agent_state(pipeline, "speaking")
    handle_user_state(pipeline, "speaking", clock=lambda: 45.0)

    assert pipeline.actions == [("assistant", True), ("user-start", 45.0)]


def test_entrypoint_wires_current_livekit_events_without_muting_input(monkeypatch):
    """Catches wrong event names, provider options, or start/connect ordering."""
    import src.agent as agent_module

    set_runtime_environment(monkeypatch)
    modules = fake_livekit_modules()
    monkeypatch.setattr(agent_module, "_load_livekit", lambda: modules)
    monkeypatch.setattr(agent_module.config, "DEEPGRAM_MODEL", "nova-3")
    monkeypatch.setattr(agent_module.config, "DEEPGRAM_LANGUAGE", "en")
    room = SimpleNamespace(lifecycle=[])
    ctx = SimpleNamespace(room=room)

    async def connect():
        room.lifecycle.append("connect")

    ctx.connect = connect
    asyncio.run(agent_module.entrypoint(ctx))

    session = FakeAgentSession.last
    assert session.options["stt"].options == {"model": "nova-3", "language": "en"}
    assert session.options["vad"] == "silero-vad"
    assert set(session.handlers) == {
        "user_input_transcribed",
        "user_state_changed",
        "agent_state_changed",
    }
    assert room.lifecycle == ["connect", "start"]
    assert session.started_with["room"] is room
    assert session.started_with["agent"].options["allow_interruptions"] is True


def test_entrypoint_rejects_blank_runtime_credentials(monkeypatch):
    """Catches startup that fails later inside a provider with an opaque error."""
    import pytest

    import src.agent as agent_module

    modules = fake_livekit_modules()
    monkeypatch.setattr(agent_module, "_load_livekit", lambda: modules)
    set_runtime_environment(monkeypatch)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "  ")
    ctx = SimpleNamespace(room=SimpleNamespace(lifecycle=[]))

    async def connect():
        raise AssertionError("must validate credentials before connecting")

    ctx.connect = connect
    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY"):
        asyncio.run(agent_module.entrypoint(ctx))


def test_run_registers_entrypoint_on_current_agent_server(monkeypatch):
    """Catches legacy WorkerOptions construction against current LiveKit releases."""
    import src.agent as agent_module

    set_runtime_environment(monkeypatch)
    modules = fake_livekit_modules()
    monkeypatch.setattr(agent_module, "_load_livekit", lambda: modules)

    agent_module.run()

    agents = modules[0]
    assert FakeAgentServer.last.entrypoint is agent_module.entrypoint
    assert agents.cli.server is FakeAgentServer.last


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
