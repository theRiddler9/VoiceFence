"""Provider-neutral transcript values and deterministic address extraction."""

from dataclasses import dataclass, field
from collections import OrderedDict
import re
import threading
import time
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    is_final: bool
    timestamp: float = field(default_factory=time.monotonic)
    turn_id: Optional[str] = None


@dataclass(frozen=True)
class AddressIntent:
    address: str
    transcript: str
    is_final: bool
    timestamp: float


_ADDRESS_UPDATE = re.compile(
    r"(?:change|update|set)\b.*?\baddress\s+to\s+(.+?)(?=\s+actually\b|$)",
    re.IGNORECASE,
)
_MAKE_ADDRESS = re.compile(
    r"\bmake\s+(?:(?:my|the|a)\s+)?(?:delivery\s+)?address(?:\s+to)?\s+(.+?)(?=\s*,?\s+(?:actually\b|make\s+it\b)|$)",
    re.IGNORECASE,
)
_CORRECTION = re.compile(
    r"(?:actually\b(?:(?!\bmake\s+it\b).)*?)?\bmake\s+it\s+(.+?)(?=\s*,?\s+(?:actually\b|make\s+it\b)|$)",
    re.IGNORECASE,
)


def _clean_payload(payload: str) -> str:
    return payload.strip().rstrip(".!?;:").strip()


def _plausible(payload: str) -> bool:
    return bool(re.search(r"\d", payload) and re.search(r"[A-Za-z]", payload))


def extract_address(event: TranscriptEvent) -> AddressIntent | None:
    """Extract the latest explicit delivery-address correction, if present."""

    text = " ".join(event.text.split()).strip()
    if not text:
        return None

    matches = [
        *(_ADDRESS_UPDATE.finditer(text)),
        *(_MAKE_ADDRESS.finditer(text)),
        *(_CORRECTION.finditer(text)),
    ]
    matches.sort(key=lambda match: match.start())
    for match in reversed(matches):
        address = _clean_payload(match.group(1))
        if _plausible(address):
            return AddressIntent(address, event.text, event.is_final, event.timestamp)
    return None


EventSink = Callable[[dict[str, Any]], None]


class VoicePipeline:
    """Route continuous-listening speech events to interruption and intent hooks."""

    MAX_TURN_DEDUP_ENTRIES = 32

    def __init__(
        self,
        on_barge_in: Callable[[], None],
        on_address_intent: Callable[[str], None],
        event_sink: Optional[EventSink] = None,
    ):
        self._on_barge_in = on_barge_in
        self._on_address_intent = on_address_intent
        self._event_sink = event_sink
        self._assistant_speaking = False
        self._user_speaking = False
        self._barge_in_emitted = False
        self._last_address_by_turn: OrderedDict[str, str] = OrderedDict()
        self._anonymous_turn = 0
        self._anonymous_last_address: Optional[str] = None
        self._anonymous_interim_address: Optional[str] = None
        self._lock = threading.Lock()

    def set_assistant_speaking(self, speaking: bool) -> None:
        with self._lock:
            self._assistant_speaking = speaking

    def on_user_speech_started(self, timestamp: Optional[float] = None) -> None:
        detected_at = time.monotonic() if timestamp is None else timestamp
        should_barge_in = False
        with self._lock:
            is_new_segment = not self._user_speaking
            self._user_speaking = True
            if is_new_segment:
                self._anonymous_turn += 1
                self._anonymous_last_address = None
            if is_new_segment and self._assistant_speaking and not self._barge_in_emitted:
                self._barge_in_emitted = True
                should_barge_in = True

        if should_barge_in:
            self._on_barge_in()
            self._emit("barge-in-detected", detected_at)

    def on_user_speech_ended(self, timestamp: Optional[float] = None) -> None:
        with self._lock:
            self._user_speaking = False
            self._barge_in_emitted = False

    def on_transcript(self, event: TranscriptEvent) -> None:
        intent = extract_address(event)
        if intent is None:
            if event.turn_id is None:
                with self._lock:
                    self._anonymous_interim_address = None
            return

        with self._lock:
            if event.turn_id is not None:
                self._anonymous_interim_address = None
                turn_key = f"item:{event.turn_id}"
                if intent.address == self._last_address_by_turn.get(turn_key):
                    self._last_address_by_turn.move_to_end(turn_key)
                    return
                self._remember_turn_address(turn_key, intent.address)
            elif (
                event.is_final
                and intent.address == self._anonymous_interim_address
            ):
                # LiveKit can deliver this final after VAD has ended and the
                # next anonymous speech segment has begun.
                self._anonymous_interim_address = None
                return
            elif intent.address == self._anonymous_last_address:
                if not event.is_final and intent.address == self._anonymous_interim_address:
                    return
                if event.is_final:
                    self._anonymous_interim_address = None
                    return
                self._anonymous_interim_address = intent.address
            else:
                self._anonymous_last_address = intent.address
                self._anonymous_interim_address = (
                    intent.address if not event.is_final else None
                )

        self._on_address_intent(intent.address)
        self._emit("address-intent", event.timestamp, address=intent.address)

    def _remember_turn_address(self, turn_key: str, address: str) -> None:
        self._last_address_by_turn[turn_key] = address
        self._last_address_by_turn.move_to_end(turn_key)
        while len(self._last_address_by_turn) > self.MAX_TURN_DEDUP_ENTRIES:
            self._last_address_by_turn.popitem(last=False)

    def _emit(self, event: str, timestamp: float, **fields: Any) -> None:
        if self._event_sink is None:
            return
        self._event_sink({"event": event, "timestamp": timestamp, **fields})
