"""
Test doubles for RimeSpeaker.

Abhinav's brief is explicit: tests should not need a human talking into a
microphone, should be repeatable, and reported numbers should be real
measured timings, not guessed. These fakes swap out the network call and
the audio device, but deliberately keep *real* elapsed time (real
threading, real time.sleep) so that timing assertions ("stopped within
300ms") are measuring the actual code path, not a mocked clock.
"""
from __future__ import annotations

import threading
import time
from typing import List, Optional


class FakeRimeResponse:
    """Stands in for the `requests` streaming response RimeSpeaker reads
    from. Yields `num_chunks` chunks of `chunk_size` bytes, sleeping
    `chunk_delay` seconds before each one — so a cancel arriving mid
    playback has a real window to land in, just like a real Rime stream
    trickling in over the network."""

    def __init__(self, num_chunks: int = 40, chunk_size: int = 320, chunk_delay: float = 0.05):
        self._num_chunks = num_chunks
        self._chunk_size = chunk_size
        self._chunk_delay = chunk_delay
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size: int = 4096):
        for _ in range(self._num_chunks):
            if self.closed:
                return
            time.sleep(self._chunk_delay)
            if self.closed:
                return
            yield b"\x00" * self._chunk_size

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


class FakeRimeSession:
    """Drop-in for requests.Session, used via RimeSpeaker's `session=`
    constructor arg. Never touches the network."""

    def __init__(self, num_chunks: int = 40, chunk_size: int = 320, chunk_delay: float = 0.05):
        self._num_chunks = num_chunks
        self._chunk_size = chunk_size
        self._chunk_delay = chunk_delay
        self.requests_made: List[dict] = []

    def post(self, url, headers=None, json=None, stream=True, timeout=None):
        self.requests_made.append({"url": url, "json": json})
        return FakeRimeResponse(self._num_chunks, self._chunk_size, self._chunk_delay)


class RecordingSink:
    """Stands in for the audio device. Records *when* audio started,
    when (if ever) it was aborted, and how many chunks got written before
    that — the raw numbers Abhinav's "how fast did it go silent" metric
    is built from.

    Optionally reports events to an EventLogger as they happen.
    """

    def __init__(self, logger=None, batch_id: Optional[str] = None):
        self._logger = logger
        self._batch_id = batch_id
        self._lock = threading.Lock()
        self.chunks_written = 0
        self.aborted = False
        self.closed = False
        self.first_write_ts: Optional[float] = None
        self.abort_ts: Optional[float] = None
        self.close_ts: Optional[float] = None

    def write(self, chunk: bytes) -> None:
        with self._lock:
            if self.first_write_ts is None:
                self.first_write_ts = time.monotonic()
            self.chunks_written += 1
        if self._logger:
            self._logger.log(
                "audio_chunk_written",
                batch_id=self._batch_id,
                chunk_bytes=len(chunk),
                chunk_index=self.chunks_written,
            )

    def abort(self) -> None:
        with self._lock:
            self.aborted = True
            self.abort_ts = time.monotonic()
        if self._logger:
            self._logger.log("audio_aborted", batch_id=self._batch_id, chunks_written=self.chunks_written)

    def close(self) -> None:
        with self._lock:
            self.closed = True
            self.close_ts = time.monotonic()
