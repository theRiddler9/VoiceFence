"""
RimeSpeaker: turns text into spoken audio via Rime, and can be told to
stop *immediately* — not muted, not faded, actually stopped.

The trick that makes near-instant cancellation possible: Rime's PCM
format is headerless raw audio. We pull it from the HTTP response in
small chunks and hand each chunk to the speakers one at a time. Before
writing each chunk we check the shared BatchRegistry. If the batch has
been cancelled we:
  1. stop pulling more audio from Rime (close the HTTP connection), and
  2. abort the output stream rather than just stopping new writes, so a
     chunk that's already buffered for playback doesn't sneak out after
     "cancel" — that's the failure mode the spec explicitly warns about.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional, Protocol

import requests

from . import config
from .batch_registry import BatchRegistry


class AudioSink(Protocol):
    """Minimal interface for "a thing that plays PCM bytes". Swappable so
    the speaker can be unit-tested (or run headless in CI/sandboxes)
    without a real audio device."""

    def write(self, chunk: bytes) -> None: ...
    def abort(self) -> None: ...
    def close(self) -> None: ...


class SoundDeviceSink:
    """Real playback via sounddevice. This is what actually reaches the
    user's speakers."""

    def __init__(self, samplerate: int, channels: int = 1):
        import sounddevice as sd  # load lazily for headless tests

        self._stream = sd.RawOutputStream(
            samplerate=samplerate, channels=channels, dtype="int16"
        )
        self._stream.start()

    def write(self, chunk: bytes) -> None:
        self._stream.write(chunk)

    def abort(self) -> None:
        # Drop buffered audio immediately.
        self._stream.abort()

    def close(self) -> None:
        try:
            self._stream.close()
        except Exception:
            pass


class NullSink:
    """No-op sink for environments with no audio hardware (e.g. this
    sandbox, or CI). Preserves the timing/cancellation *logic* being
    tested even though nothing audible happens."""

    def write(self, chunk: bytes) -> None:
        pass

    def abort(self) -> None:
        pass

    def close(self) -> None:
        pass


def _default_sink_factory(samplerate: int) -> AudioSink:
    try:
        return SoundDeviceSink(samplerate)
    except Exception as exc:  # fall back to silent output
        print(f"[RimeSpeaker] No audio output available ({exc}); "
              f"falling back to a silent sink.")
        return NullSink()


@dataclass
class SpeakResult:
    batch_id: str
    status: str  # "completed" | "cancelled" | "error"
    bytes_played: int
    error: Optional[str] = None


class RimeSpeaker:
    def __init__(
        self,
        registry: BatchRegistry,
        api_key: Optional[str] = None,
        speaker: str = config.RIME_SPEAKER,
        model_id: str = config.RIME_MODEL_ID,
        language: str = config.RIME_LANGUAGE,
        sampling_rate: int = config.RIME_SAMPLING_RATE,
        speed_alpha: float = config.RIME_SPEED_ALPHA,
        sink_factory=_default_sink_factory,
        session: Optional[requests.Session] = None,
        chunk_size: int = 4096,
    ):
        self._registry = registry
        self._api_key = api_key or config.RIME_API_KEY
        self._speaker = speaker
        self._model_id = model_id
        self._language = language
        self._sampling_rate = sampling_rate
        self._speed_alpha = speed_alpha
        self._sink_factory = sink_factory
        self._session = session or requests.Session()
        self._chunk_size = chunk_size

    def speak(self, text: str, batch_id: str, blocking: bool = True):
        """Speak `text`, tagged with `batch_id`. If `blocking` is False,
        runs in a background thread and returns the Thread object instead
        of a SpeakResult; the caller reads the outcome via the registry
        (is_cancelled) or by joining the thread."""
        if blocking:
            return self._run(text, batch_id)
        thread = threading.Thread(
            target=self._run, args=(text, batch_id), daemon=True
        )
        thread.start()
        return thread

    def _run(self, text: str, batch_id: str) -> SpeakResult:
        if not self._api_key:
            return SpeakResult(batch_id, "error", 0, error="RIME_API_KEY not set")

        if self._registry.is_cancelled(batch_id):
            # Avoid the network call for cancelled batches.
            return SpeakResult(batch_id, "cancelled", 0)

        payload = {
            "text": text,
            "modelId": self._model_id,
            "speaker": self._speaker,
            "lang": self._language,
            "samplingRate": self._sampling_rate,
            "speedAlpha": self._speed_alpha,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/pcm",
        }

        sink = self._sink_factory(self._sampling_rate)
        bytes_played = 0
        try:
            with self._session.post(
                config.RIME_TTS_URL, headers=headers, json=payload, stream=True, timeout=30
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_content(chunk_size=self._chunk_size):
                    if not chunk:
                        continue
                    if self._registry.is_cancelled(batch_id):
                        sink.abort()  # drop anything already buffered
                        response.close()  # stop pulling more audio from Rime
                        return SpeakResult(batch_id, "cancelled", bytes_played)
                    sink.write(chunk)
                    bytes_played += len(chunk)
        except requests.RequestException as exc:
            return SpeakResult(batch_id, "error", bytes_played, error=str(exc))
        finally:
            sink.close()

        if self._registry.is_cancelled(batch_id):
            return SpeakResult(batch_id, "cancelled", bytes_played)
        return SpeakResult(batch_id, "completed", bytes_played)
