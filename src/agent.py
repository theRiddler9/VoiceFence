"""Composition helpers for connecting voice input to order orchestration.

LiveKit runtime wiring belongs in this module, but optional provider imports
must remain inside its runtime loader so the callback contract can be used
in offline tests without LiveKit installed.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from concurrent.futures import Future, TimeoutError
from typing import Any, Optional, Protocol

from . import config
from .batch_registry import BatchRegistry
from .orchestrator import EpochOrchestrator
from .order_store import OrderStore
from .stt_client import EventSink, TranscriptEvent, VoicePipeline


_REQUIRED_RUNTIME_ENV = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "DEEPGRAM_API_KEY",
    "RIME_API_KEY",
)


class VoiceOrchestrator(Protocol):
    """The narrow orchestration boundary consumed by the voice pipeline."""

    def on_barge_in(self) -> None: ...

    def on_address_intent(self, address: str) -> Any: ...


class LiveKitPlayoutTask:
    """Thread-like view of one session-owned playout task."""

    def __init__(self, future: Future[None]):
        self._future = future

    def join(self, timeout: Optional[float] = None) -> None:
        """Match the orchestrator's joinable speaker-task contract."""
        try:
            self._future.result(timeout)
        except TimeoutError:
            if self._future.done():
                # The playout itself raised TimeoutError; only a pending future
                # represents the caller's join timeout.
                self._future.result()
            return

    def is_alive(self) -> bool:
        return not self._future.done()


class LiveKitSessionSpeaker:
    """Route worker-thread confirmations through LiveKit session playout."""

    def __init__(
        self,
        session: Any,
        *,
        loop: Any,
        registry: BatchRegistry,
    ):
        self._session = session
        self._loop = loop
        self._registry = registry

    def speak(
        self,
        text: str,
        batch_id: str,
        blocking: bool = True,
    ) -> LiveKitPlayoutTask:
        """Schedule interruptible Rime playout on LiveKit's owning event loop."""
        future = asyncio.run_coroutine_threadsafe(
            self._play(text, batch_id),
            self._loop,
        )
        task = LiveKitPlayoutTask(future)
        if blocking:
            task.join()
        return task

    async def _play(self, text: str, batch_id: str) -> None:
        if self._registry.is_cancelled(batch_id):
            return

        speech = self._session.say(text, allow_interruptions=True)
        if self._registry.is_cancelled(batch_id):
            speech.interrupt(force=True)
            return
        await speech.wait_for_playout()

    def interrupt(self) -> None:
        """Stop current session playout from either SDK or worker callbacks."""
        self._loop.call_soon_threadsafe(self._interrupt_on_session_loop)

    def _interrupt_on_session_loop(self) -> None:
        try:
            self._session.interrupt(force=True)
        except RuntimeError:
            # There is no queued speech yet (or the session is already closing).
            pass


class LiveKitBargeInBridge:
    """Keep the fenced epoch and LiveKit's actual audio output in lockstep."""

    def __init__(
        self,
        orchestrator: VoiceOrchestrator,
        speaker: LiveKitSessionSpeaker,
    ):
        self._orchestrator = orchestrator
        self._speaker = speaker

    def on_barge_in(self) -> None:
        self._orchestrator.on_barge_in()
        self._speaker.interrupt()

    def on_address_intent(self, address: str) -> Any:
        return self._orchestrator.on_address_intent(address)


def build_voice_pipeline(
    orchestrator: VoiceOrchestrator,
    event_sink: Optional[EventSink] = None,
) -> VoicePipeline:
    """Adapt the existing orchestrator callback contract to voice events."""
    return VoicePipeline(
        on_barge_in=orchestrator.on_barge_in,
        on_address_intent=orchestrator.on_address_intent,
        event_sink=event_sink,
    )


def handle_transcript_event(
    pipeline: VoicePipeline,
    event: Any,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Map one LiveKit transcript event into the provider-neutral pipeline."""
    pipeline.on_transcript(
        TranscriptEvent(
            text=event.transcript,
            is_final=event.is_final,
            timestamp=clock(),
            turn_id=getattr(event, "item_id", None),
        )
    )


def handle_user_state(
    pipeline: VoicePipeline,
    state: Any,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Map LiveKit's user state to speech-segment boundaries."""
    new_state = getattr(state, "new_state", state)
    if new_state == "speaking":
        pipeline.on_user_speech_started(timestamp=clock())
    else:
        pipeline.on_user_speech_ended(timestamp=clock())


def handle_agent_state(pipeline: VoicePipeline, state: Any) -> None:
    """Keep assistant speech state synchronized with LiveKit."""
    new_state = getattr(state, "new_state", state)
    pipeline.set_assistant_speaking(new_state == "speaking")


def _load_livekit() -> tuple[Any, Any, Any, Any]:
    """Load optional runtime providers only when the live worker starts."""
    try:
        from livekit import agents
        from livekit.plugins import deepgram, rime, silero
    except ImportError as exc:
        raise RuntimeError(
            "LiveKit runtime packages are unavailable; install requirements.txt "
            "before starting the live agent"
        ) from exc
    return agents, deepgram, silero, rime


def _require_runtime_environment() -> None:
    missing = [
        name
        for name in _REQUIRED_RUNTIME_ENV
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Missing required runtime environment variables: " + ", ".join(missing)
        )


def _prewarm_vad(proc: Any, silero: Any) -> None:
    """Load Silero once per worker process before the first room is assigned."""
    proc.userdata["voicefence_silero_vad"] = silero.VAD.load()


def prewarm(proc: Any) -> None:
    """Pickle-safe AgentServer setup callback for spawned worker processes."""
    _, _, silero, _ = _load_livekit()
    _prewarm_vad(proc, silero)


def _prewarmed_vad(ctx: Any, silero: Any) -> Any:
    """Reuse the process VAD while keeping direct entrypoint tests supported."""
    userdata = getattr(getattr(ctx, "proc", None), "userdata", None)
    if userdata is not None and "voicefence_silero_vad" in userdata:
        return userdata["voicefence_silero_vad"]
    return silero.VAD.load()


async def entrypoint(ctx: Any) -> None:
    """Join one LiveKit room and feed its voice events to the fenced pipeline."""
    agents, deepgram, silero, rime = _load_livekit()
    _require_runtime_environment()

    registry = BatchRegistry()
    store = OrderStore(registry)

    session = agents.AgentSession(
        stt=deepgram.STT(
            model=config.DEEPGRAM_MODEL,
            language=config.DEEPGRAM_LANGUAGE,
        ),
        tts=rime.TTS(
            model=config.RIME_MODEL_ID,
            speaker=config.RIME_SPEAKER,
            lang=config.RIME_LANGUAGE,
            speed_alpha=config.RIME_SPEED_ALPHA,
            sample_rate=config.RIME_SAMPLING_RATE,
            api_key=os.environ["RIME_API_KEY"].strip(),
        ),
        vad=_prewarmed_vad(ctx, silero),
    )
    speaker = LiveKitSessionSpeaker(
        session,
        loop=asyncio.get_running_loop(),
        registry=registry,
    )
    orchestrator = EpochOrchestrator(registry, store, speaker)
    pipeline = build_voice_pipeline(LiveKitBargeInBridge(orchestrator, speaker))
    session.on(
        "user_input_transcribed",
        lambda event: handle_transcript_event(pipeline, event),
    )
    session.on(
        "user_state_changed",
        lambda event: handle_user_state(pipeline, event),
    )
    session.on(
        "agent_state_changed",
        lambda event: handle_agent_state(pipeline, event),
    )

    class VoiceFenceAgent(agents.Agent):
        def __init__(self) -> None:
            super().__init__(
                instructions=(
                    "Transcribe user speech for the VoiceFence order orchestrator. "
                    "Application responses are produced outside LiveKit."
                ),
                allow_interruptions=True,
            )

        async def on_user_turn_completed(self, chat_ctx: Any, new_message: Any) -> None:
            raise agents.StopResponse()

    await ctx.connect()
    await session.start(room=ctx.room, agent=VoiceFenceAgent())


def run() -> None:
    """Start the LiveKit worker CLI using the current AgentServer API."""
    agents, _, _, _ = _load_livekit()
    _require_runtime_environment()
    server = agents.AgentServer()
    server.setup_fnc = prewarm
    server.rtc_session(entrypoint)
    agents.cli.run_app(server)


if __name__ == "__main__":
    run()
