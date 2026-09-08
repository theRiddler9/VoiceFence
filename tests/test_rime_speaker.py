from src.batch_registry import BatchRegistry
from src.rime_speaker import RimeSpeaker


class FakeResponse:
    """Small streaming response fake for cancellation tests."""

    def __init__(self, chunks, on_each_chunk=None):
        self._chunks = chunks
        self._on_each_chunk = on_each_chunk
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=4096):
        for chunk in self._chunks:
            if self.closed:
                return
            if self._on_each_chunk:
                self._on_each_chunk()
            yield chunk

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeSession:
    def __init__(self, response):
        self._response = response
        self.post_calls = 0

    def post(self, url, headers=None, json=None, stream=None, timeout=None):
        self.post_calls += 1
        return self._response


class FakeSink:
    def __init__(self, samplerate):
        self.samplerate = samplerate
        self.written = []
        self.aborted = False
        self.closed = False

    def write(self, chunk):
        self.written.append(chunk)

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


def _speaker_with(chunks, registry, on_each_chunk=None):
    fake_sink = FakeSink(16000)
    response = FakeResponse(chunks, on_each_chunk=on_each_chunk)
    session = FakeSession(response)
    speaker = RimeSpeaker(
        registry,
        api_key="fake-key-for-tests",
        sink_factory=lambda samplerate: fake_sink,
        session=session,
    )
    return speaker, fake_sink, session


def test_completes_normally_and_plays_all_chunks():
    reg = BatchRegistry()
    chunks = [b"aaaa", b"bbbb", b"cccc"]
    speaker, sink, session = _speaker_with(chunks, reg)

    result = speaker.speak("hello", "batch-1")

    assert result.status == "completed"
    assert sink.written == chunks
    assert sink.aborted is False
    assert sink.closed is True
    assert session.post_calls == 1


def test_cancel_before_start_skips_the_network_call_entirely():
    reg = BatchRegistry()
    reg.cancel("batch-1")
    chunks = [b"aaaa", b"bbbb"]
    speaker, sink, session = _speaker_with(chunks, reg)

    result = speaker.speak("hello", "batch-1")

    assert result.status == "cancelled"
    assert session.post_calls == 0
    assert sink.written == []


def test_cancel_mid_stream_stops_playback_and_drops_buffered_audio():
    reg = BatchRegistry()
    chunks = [b"aaaa", b"bbbb", b"cccc", b"dddd"]

    call_count = {"n": 0}

    def on_each_chunk():
        call_count["n"] += 1
        if call_count["n"] == 2:
            reg.cancel("batch-1")

    speaker, sink, session = _speaker_with(chunks, reg, on_each_chunk=on_each_chunk)

    result = speaker.speak("hello", "batch-1")

    assert result.status == "cancelled"
    assert sink.written == [b"aaaa"]
    assert sink.aborted is True
    assert sink.closed is True


def test_missing_api_key_returns_error_without_crashing():
    reg = BatchRegistry()
    fake_sink = FakeSink(16000)
    speaker = RimeSpeaker(
        reg,
        api_key="",
        sink_factory=lambda samplerate: fake_sink,
        session=FakeSession(FakeResponse([])),
    )

    result = speaker.speak("hello", "batch-1")

    assert result.status == "error"
    assert result.error == "RIME_API_KEY not set"
