"""
EventLogger: the append-only, structured log that every number in
Abhinav's final report is required to trace back to. Nothing gets typed
into a report by hand — the report generator (report.py) only ever reads
this file.

Format: JSON Lines (one JSON object per line). Chosen over a custom text
format specifically so it's trivial to grep, `jq`, or load with
`json.loads` per line — no bespoke parser to write or trust.

Every record carries:
  - ts_iso        wall-clock timestamp (for humans / cross-referencing)
  - elapsed_ms    milliseconds since this logger was created (for
                   precise within-run timing math)
  - run_id        which scripted run this event belongs to
  - event         event name, e.g. "interrupt_detected"
  - ...           whatever event-specific fields the caller passes
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class EventLogger:
    def __init__(self, path: str | Path, run_id: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        # Append mode: a single log file can accumulate many runs, which
        # is exactly what the "run it 10+ times" requirement needs —
        # one file, many runs, aggregate afterwards.
        self._fh = open(self._path, "a", buffering=1)

    def log(self, event: str, **fields: Any) -> Dict[str, Any]:
        record = {
            "ts_iso": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": round((time.monotonic() - self._t0) * 1000, 3),
            "run_id": self._run_id,
            "event": event,
            **fields,
        }
        line = json.dumps(record, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
        return record

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()

    def __enter__(self) -> "EventLogger":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def path(self) -> Path:
        return self._path


def read_events(path: str | Path):
    """Read every JSONL record back out of a log file. Used only by the
    report generator — reports must be derived from this, never from
    numbers remembered/typed by a person."""
    path = Path(path)
    events = []
    if not path.exists():
        return events
    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events
