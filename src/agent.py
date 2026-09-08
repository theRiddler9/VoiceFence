"""Composition helpers for connecting voice input to order orchestration.

LiveKit runtime wiring belongs in this module, but optional provider imports
must remain inside its runtime entrypoint so the callback contract can be used
in offline tests without LiveKit installed.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from .stt_client import EventSink, VoicePipeline


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
