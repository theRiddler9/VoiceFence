"""Provider-neutral transcript values and deterministic address extraction."""

from dataclasses import dataclass, field
import re
import time


@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    is_final: bool
    timestamp: float = field(default_factory=time.monotonic)


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
