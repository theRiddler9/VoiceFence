import asyncio
import threading
from types import SimpleNamespace

from src.batch_registry import BatchRegistry
from src.orchestrator import EpochOrchestrator
from src.order_store import OrderStore
from src.stt_client import TranscriptEvent, VoicePipeline


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


class FakePlaybackHandle:
    def __init__(self):
        self._done = asyncio.Event()

    def finish(self):
        self._done.set()

    async def wait_for_playout(self):
        await self._done.wait()

    def exception(self):
        return None


class LoopBoundSession:
    def __init__(self):
        self.say_calls = []
        self.say_thread_id = None
        self.handle = FakePlaybackHandle()

    def say(self, text, *, allow_interruptions):
        self.say_thread_id = threading.get_ident()
        self.say_calls.append((text, allow_interruptions))
        return self.handle


class FailingLoopBoundSession:
    def say(self, text, *, allow_interruptions):
        raise RuntimeError("session playout failed")


class TimedOutLoopBoundSession:
    def say(self, text, *, allow_interruptions):
        raise TimeoutError("session playout timed out")


class PostPlayoutFailingHandle:
    async def wait_for_playout(self):
        return None

    def exception(self):
        return RuntimeError("Rime playout failed after start")


class PostPlayoutFailingSession:
    def say(self, text, *, allow_interruptions):
        return PostPlayoutFailingHandle()


class InterruptRecordingSession:
    def __init__(self):
        self.interrupt_calls = []

    def interrupt(self, *, force):
        self.interrupt_calls.append(force)


class ControllableFuture:
    def __init__(self):
        self._callbacks = []

    def add_done_callback(self, callback):
        self._callbacks.append(callback)

    def done(self):
        return False

    def result(self, timeout=None):
        raise AssertionError("the scheduling-race test never joins this future")


class InlineCallbackLoop:
    def call_soon_threadsafe(self, callback):
        callback()


class FakeSTT:
    def __init__(self, **kwargs):
        self.options = kwargs


class FakeRimeTTS:
    def __init__(self, **kwargs):
        self.options = kwargs


class FakeVAD:
    load_calls = 0

    @classmethod
    def load(cls):
        cls.load_calls += 1
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
        self.say_calls = []
        self.interrupt_calls = []
        self.playout_handles = []
        self.playout_events = []

    def on(self, event_name, handler):
        self.handlers[event_name] = handler

    def emit(self, event_name, event):
        self.handlers[event_name](event)

    def say(self, text, *, allow_interruptions):
        handle = FakePlaybackHandle()
        self.say_calls.append((text, allow_interruptions))
        self.playout_handles.append(handle)
        self.playout_events.append(("say", text))
        self.emit("agent_state_changed", SimpleNamespace(new_state="speaking"))
        return handle

    def interrupt(self, *, force):
        self.interrupt_calls.append(force)
        self.playout_events.append(("interrupt", force))
        for handle in self.playout_handles:
            handle.finish()
        self.emit("agent_state_changed", SimpleNamespace(new_state="listening"))

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
    return (
        agents,
        SimpleNamespace(STT=FakeSTT),
        SimpleNamespace(VAD=FakeVAD),
        SimpleNamespace(TTS=FakeRimeTTS),
    )


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
            item_id="livekit-turn-44",
        ),
        clock=lambda: 44.0,
    )

    assert pipeline.transcripts == [
        TranscriptEvent(
            "make it 1600 Pennsylvania Avenue",
            False,
            44.0,
            turn_id="livekit-turn-44",
        )
    ]


def test_user_and_agent_state_mapping_keeps_overlap_observable():
    """Catches SDK state adapters that defer speech overlap until a transcript."""
    from src.agent import handle_agent_state, handle_user_state

    pipeline = RecordingPipeline()
    handle_agent_state(pipeline, "speaking")
    handle_user_state(pipeline, "speaking", clock=lambda: 45.0)

    assert pipeline.actions == [("assistant", True), ("user-start", 45.0)]


def test_livekit_session_speaker_crosses_worker_threads_and_tracks_playout():
    """Catches a local-speaker fallback or a direct worker-thread session call."""
    from src.agent import LiveKitSessionSpeaker

    async def exercise():
        registry = BatchRegistry()
        session = LoopBoundSession()
        speaker = LiveKitSessionSpeaker(
            session,
            loop=asyncio.get_running_loop(),
            registry=registry,
        )
        worker_thread_id = []
        playout_task = []

        def invoke_from_worker():
            worker_thread_id.append(threading.get_ident())
            playout_task.append(
                speaker.speak("confirmation", "batch-livekit", blocking=False)
            )

        worker = threading.Thread(target=invoke_from_worker)
        worker.start()
        await asyncio.to_thread(worker.join, 1)

        for _ in range(20):
            if session.say_calls:
                break
            await asyncio.sleep(0.01)

        assert session.say_calls == [("confirmation", True)]
        assert session.say_thread_id != worker_thread_id[0]
        assert playout_task[0].is_alive() is True

        session.handle.finish()
        await asyncio.sleep(0)
        await asyncio.to_thread(playout_task[0].join, 1)
        assert playout_task[0].is_alive() is False

    asyncio.run(exercise())


def test_livekit_session_speaker_publishes_pending_playout_before_scheduling(monkeypatch):
    """Catches a replacement interrupt that races before pending playout is visible."""
    import src.agent as agent_module

    session = InterruptRecordingSession()
    loop = InlineCallbackLoop()
    speaker = agent_module.LiveKitSessionSpeaker(
        session,
        loop=loop,
        registry=BatchRegistry(),
    )
    future = ControllableFuture()

    def schedule(coroutine, _loop):
        coroutine.close()
        speaker.interrupt()
        return future

    monkeypatch.setattr(agent_module.asyncio, "run_coroutine_threadsafe", schedule)
    speaker.speak("confirmation", "batch-livekit", blocking=False)

    assert session.interrupt_calls == [True]


def test_livekit_session_speaker_rolls_back_pending_playout_when_scheduling_fails(monkeypatch):
    """Catches a failed scheduler call that leaves later interruptions armed."""
    import pytest

    import src.agent as agent_module

    session = InterruptRecordingSession()
    speaker = agent_module.LiveKitSessionSpeaker(
        session,
        loop=InlineCallbackLoop(),
        registry=BatchRegistry(),
    )
    scheduled_coroutines = []

    def reject(coroutine, _loop):
        scheduled_coroutines.append(coroutine)
        raise RuntimeError("session loop is closed")

    monkeypatch.setattr(agent_module.asyncio, "run_coroutine_threadsafe", reject)
    with pytest.raises(RuntimeError, match="session loop is closed"):
        speaker.speak("confirmation", "batch-livekit", blocking=False)

    speaker.interrupt()
    assert session.interrupt_calls == []
    assert scheduled_coroutines[0].cr_frame is None


def test_livekit_session_speaker_surfaces_playout_exceptions():
    """Catches a joinable task that silently drops a Rime/session failure."""
    import pytest

    from src.agent import LiveKitSessionSpeaker

    async def exercise():
        speaker = LiveKitSessionSpeaker(
            FailingLoopBoundSession(),
            loop=asyncio.get_running_loop(),
            registry=BatchRegistry(),
        )
        task = speaker.speak("confirmation", "batch-livekit", blocking=False)

        for _ in range(20):
            if not task.is_alive():
                break
            await asyncio.sleep(0.01)

        assert task.is_alive() is False
        with pytest.raises(RuntimeError, match="session playout failed"):
            await asyncio.to_thread(task.join, 1)

    asyncio.run(exercise())


def test_livekit_session_speaker_surfaces_playout_timeout_exceptions():
    """Catches a real session timeout being confused with a join timeout."""
    import pytest

    from src.agent import LiveKitSessionSpeaker

    async def exercise():
        speaker = LiveKitSessionSpeaker(
            TimedOutLoopBoundSession(),
            loop=asyncio.get_running_loop(),
            registry=BatchRegistry(),
        )
        task = speaker.speak("confirmation", "batch-livekit", blocking=False)

        for _ in range(20):
            if not task.is_alive():
                break
            await asyncio.sleep(0.01)

        assert task.is_alive() is False
        with pytest.raises(TimeoutError, match="session playout timed out"):
            await asyncio.to_thread(task.join, 1)

    asyncio.run(exercise())


def test_livekit_session_speaker_surfaces_post_playout_exceptions():
    """Catches a completed speech handle whose provider failure is ignored."""
    import pytest

    from src.agent import LiveKitSessionSpeaker

    async def exercise():
        speaker = LiveKitSessionSpeaker(
            PostPlayoutFailingSession(),
            loop=asyncio.get_running_loop(),
            registry=BatchRegistry(),
        )
        task = speaker.speak("confirmation", "batch-livekit", blocking=False)

        for _ in range(20):
            if not task.is_alive():
                break
            await asyncio.sleep(0.01)

        assert task.is_alive() is False
        with pytest.raises(RuntimeError, match="Rime playout failed after start"):
            await asyncio.to_thread(task.join, 1)

    asyncio.run(exercise())


def test_vad_end_does_not_reset_livekit_turn_dedup_before_final_transcript():
    """Catches VAD end clearing interim intent dedup before its final arrives."""
    from src.agent import handle_transcript_event

    addresses = []
    pipeline = VoicePipeline(lambda: None, addresses.append)
    handle_transcript_event(
        pipeline,
        SimpleNamespace(
            transcript="make it 221B Baker Street",
            is_final=False,
            item_id="livekit-turn-99",
        ),
        clock=lambda: 50.0,
    )
    pipeline.on_user_speech_ended(timestamp=50.1)
    handle_transcript_event(
        pipeline,
        SimpleNamespace(
            transcript="make it 221B Baker Street",
            is_final=True,
            item_id="livekit-turn-99",
        ),
        clock=lambda: 50.2,
    )

    assert addresses == ["221B Baker Street"]


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
    assert session.options["tts"].options == {
        "model": "mistv2",
        "speaker": "astra",
        "lang": "eng",
        "speed_alpha": 1.0,
        "sample_rate": 16000,
        "api_key": "test-rime_api_key",
    }
    assert session.options["vad"] == "silero-vad"
    assert set(session.handlers) == {
        "user_input_transcribed",
        "user_state_changed",
        "agent_state_changed",
    }
    assert room.lifecycle == ["connect", "start"]
    assert session.started_with["room"] is room
    assert session.started_with["agent"].options["allow_interruptions"] is True


def test_session_managed_playout_barge_in_stops_audio_and_advances_epoch(monkeypatch):
    """Catches a bridge that tracks injected state instead of session playout."""
    import src.agent as agent_module

    async def exercise():
        set_runtime_environment(monkeypatch)
        monkeypatch.setattr(agent_module.config, "MOCK_LOOKUP_DELAY_SECONDS", 0.0)
        modules = fake_livekit_modules()
        monkeypatch.setattr(agent_module, "_load_livekit", lambda: modules)
        captured = {}
        real_build_voice_pipeline = agent_module.build_voice_pipeline

        def capture_pipeline(orchestrator, event_sink=None):
            captured["bridge"] = orchestrator
            return real_build_voice_pipeline(orchestrator, event_sink)

        monkeypatch.setattr(agent_module, "build_voice_pipeline", capture_pipeline)
        room = SimpleNamespace(lifecycle=[])
        ctx = SimpleNamespace(room=room)

        async def connect():
            room.lifecycle.append("connect")

        ctx.connect = connect
        await agent_module.entrypoint(ctx)
        session = FakeAgentSession.last
        session.emit(
            "user_input_transcribed",
            SimpleNamespace(
                transcript="make it 10 Downing Street",
                is_final=True,
                item_id="turn-7",
            ),
        )

        for _ in range(50):
            if session.say_calls:
                break
            await asyncio.sleep(0.01)

        bridge = captured["bridge"]
        assert session.say_calls == [
            ("Sure, I've updated your address to 10 Downing Street.", True)
        ]
        assert bridge._orchestrator.current_epoch == 1

        session.emit("user_state_changed", SimpleNamespace(new_state="speaking"))
        await asyncio.sleep(0)

        assert session.interrupt_calls == [True]
        assert bridge._orchestrator.current_epoch == 2

    asyncio.run(exercise())


def test_superseding_address_stops_session_playout_before_new_confirmation(monkeypatch):
    """Catches an epoch-only supersession that leaves stale room audio queued."""
    import src.agent as agent_module

    async def exercise():
        set_runtime_environment(monkeypatch)
        monkeypatch.setattr(agent_module.config, "MOCK_LOOKUP_DELAY_SECONDS", 0.0)
        modules = fake_livekit_modules()
        monkeypatch.setattr(agent_module, "_load_livekit", lambda: modules)
        room = SimpleNamespace(lifecycle=[])
        ctx = SimpleNamespace(room=room)

        async def connect():
            room.lifecycle.append("connect")

        ctx.connect = connect
        await agent_module.entrypoint(ctx)
        session = FakeAgentSession.last
        session.emit(
            "user_input_transcribed",
            SimpleNamespace(
                transcript="make it 1 First Street",
                is_final=True,
                item_id="first-turn",
            ),
        )

        for _ in range(50):
            if len(session.say_calls) == 1:
                break
            await asyncio.sleep(0.01)

        session.emit(
            "user_input_transcribed",
            SimpleNamespace(
                transcript="make it 10 Downing Street",
                is_final=True,
                item_id="corrected-turn",
            ),
        )

        for _ in range(50):
            if len(session.say_calls) == 2:
                break
            await asyncio.sleep(0.01)

        assert session.playout_events == [
            ("say", "Sure, I've updated your address to 1 First Street."),
            ("interrupt", True),
            ("say", "Sure, I've updated your address to 10 Downing Street."),
        ]

    asyncio.run(exercise())


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


def test_run_prewarms_silero_for_the_next_job_process(monkeypatch):
    """Catches per-room VAD model loading that delays the first caller."""
    import src.agent as agent_module

    set_runtime_environment(monkeypatch)
    modules = fake_livekit_modules()
    monkeypatch.setattr(agent_module, "_load_livekit", lambda: modules)
    FakeVAD.load_calls = 0

    agent_module.run()
    assert FakeAgentServer.last.setup_fnc is agent_module.prewarm
    proc = SimpleNamespace(userdata={})
    FakeAgentServer.last.setup_fnc(proc)

    assert proc.userdata == {"voicefence_silero_vad": "silero-vad"}
    assert FakeVAD.load_calls == 1


def test_installed_livekit_rime_session_constructor_smoke():
    """Catches a requirements/API mismatch when optional LiveKit SDKs are installed."""
    import pytest

    agents = pytest.importorskip("livekit.agents")
    rime = pytest.importorskip("livekit.plugins.rime")

    async def construct_session():
        tts = rime.TTS(
            model="mistv2",
            speaker="astra",
            lang="eng",
            speed_alpha=1.0,
            sample_rate=16000,
            api_key="test-rime-key",
        )
        session = agents.AgentSession(tts=tts)
        assert session.tts is tts

    asyncio.run(construct_session())


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
