"""Composition helpers for connecting voice input to order orchestration.

LiveKit runtime wiring belongs in this module, but optional provider imports
must remain inside its runtime loader so the callback contract can be used
in offline tests without LiveKit installed.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any, Optional, Protocol

from . import config
from .batch_registry import BatchRegistry
from .orchestrator import EpochOrchestrator
from .order_store import OrderStore
from .rime_speaker import RimeSpeaker
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


def _load_livekit() -> tuple[Any, Any, Any]:
    """Load optional runtime providers only when the live worker starts."""
    try:
        from livekit import agents
        from livekit.plugins import deepgram, silero
    except ImportError as exc:
        raise RuntimeError(
            "LiveKit runtime packages are unavailable; install requirements.txt "
            "before starting the live agent"
        ) from exc
    return agents, deepgram, silero


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


async def entrypoint(ctx: Any) -> None:
    """Join one LiveKit room and feed its voice events to the fenced pipeline."""
    agents, deepgram, silero = _load_livekit()
    _require_runtime_environment()

    registry = BatchRegistry()
    store = OrderStore(registry)
    speaker = RimeSpeaker(registry, api_key=os.environ["RIME_API_KEY"].strip())
    orchestrator = EpochOrchestrator(registry, store, speaker)
    pipeline = build_voice_pipeline(orchestrator)

    session = agents.AgentSession(
        stt=deepgram.STT(
            model=config.DEEPGRAM_MODEL,
            language=config.DEEPGRAM_LANGUAGE,
        ),
        vad=silero.VAD.load(),
    )
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
    agents, _, _ = _load_livekit()
    _require_runtime_environment()
    server = agents.AgentServer()
    server.rtc_session(entrypoint)
    agents.cli.run_app(server)


if __name__ == "__main__":
    run()
