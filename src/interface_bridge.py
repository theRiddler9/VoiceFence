"""File-backed event bridge for the local VoiceFence interface."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class RuntimeEventBridge:
    """Append sanitized voice-pipeline events for the browser server to read."""

    _lock = threading.Lock()

    def __init__(self, path: str | os.PathLike[str]):
        self._path = Path(path)
        self._run_id = str(uuid.uuid4())
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_environment(cls) -> "RuntimeEventBridge":
        path = os.environ.get(
            "VOICEFENCE_EVENT_LOG",
            str(Path(".voicefence") / "live_events.jsonl"),
        )
        return cls(path)

    @property
    def path(self) -> Path:
        return self._path

    def clear(self) -> None:
        """Truncate the event log for a fresh session."""
        with self._lock:
            self._path.write_text("", encoding="utf-8")

    def __call__(self, event: dict[str, Any]) -> None:
        record = {
            "run_id": self._run_id,
            **event,
            "timestamp": time.time(),
        }
        line = json.dumps(record, separators=(",", ":"), default=str)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
