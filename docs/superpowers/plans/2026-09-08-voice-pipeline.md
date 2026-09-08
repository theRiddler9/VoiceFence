# Voice Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a continuously listening LiveKit/Deepgram voice input pipeline that detects barge-in immediately, extracts corrected delivery addresses, and drives the existing fenced orchestrator end to end.

**Architecture:** Keep provider-neutral transcript parsing and overlap state in `src/stt_client.py`; isolate LiveKit SDK wiring and dependency construction in `src/agent.py`. Drive all behavior through the existing `EpochOrchestrator.on_barge_in()` and `on_address_intent()` hooks so the voice layer detects and reports but never owns cancellation.

**Tech Stack:** Python 3.10+, pytest, LiveKit Agents, LiveKit Deepgram plugin, LiveKit Silero plugin, existing requests/sounddevice Rime client.

**Spec:** `docs/superpowers/specs/2026-09-08-voice-pipeline-design.md`

## Global Constraints

- Microphone input remains enabled while assistant audio is active.
- Barge-in fires from user-speech-start, never from final-transcript completion.
- Voice-pipeline code must not cancel batches or mutate orders directly.
- Credentials come only from environment variables and are never logged.
- Core tests run without network, provider credentials, or audio hardware.
- Address extraction is deterministic and limited to the delivery-address demo.
- No repository changes are pushed.

---

## File Structure

- Create `src/stt_client.py`: immutable event models, deterministic address extraction, per-speech-segment deduplication, and overlap detection.
- Create `tests/test_stt_client.py`: offline unit tests for extraction, partial transcripts, corrections, metadata, and barge-in state.
- Create `src/agent.py`: adapter helpers, dependency construction, LiveKit session/event wiring, and CLI entry point.
- Create `tests/test_agent.py`: SDK-independent adapter and real-orchestrator contract tests.
- Create `voice_demo.py`: credential-free scripted end-to-end overlap/correction scenario.
- Create `tests/test_voice_demo.py`: executable offline demonstration check.
- Modify `src/config.py`: Deepgram model/language settings.
- Modify `src/__init__.py`: export provider-neutral voice pipeline interfaces.
- Modify `.env.example`: document LiveKit/Deepgram placeholders.
- Modify `requirements.txt`: pin compatible LiveKit agent/plugin dependency floors.
- Modify `README.md`: resolve its committed merge markers and document both offline and live voice runs.

---

### Task 1: Transcript Models and Address Extraction

**Files:**
- Create: `src/stt_client.py`
- Create: `tests/test_stt_client.py`

**Interfaces:**
- Consumes: raw transcript text, finality flag, optional monotonic timestamp.
- Produces: `TranscriptEvent(text: str, is_final: bool, timestamp: float)`, `AddressIntent(address: str, transcript: str, is_final: bool, timestamp: float)`, and `extract_address(event: TranscriptEvent) -> AddressIntent | None`.

- [ ] **Step 1: Write failing extraction tests**

```python
from src.stt_client import TranscriptEvent, extract_address

def test_extracts_address_from_interim_transcript():
    event = TranscriptEvent("change my address to 42 Wallaby Way, Sydney", False, 12.5)
    intent = extract_address(event)
    assert intent.address == "42 Wallaby Way, Sydney"
    assert intent.transcript == event.text
    assert intent.is_final is False
    assert intent.timestamp == 12.5

def test_latest_explicit_correction_wins():
    event = TranscriptEvent(
        "change my address to 42 Wallaby Way, actually make it 221B Baker Street, London",
        True,
        13.0,
    )
    assert extract_address(event).address == "221B Baker Street, London"

def test_unrelated_address_word_is_rejected():
    event = TranscriptEvent("I need to address that problem tomorrow", True, 14.0)
    assert extract_address(event) is None
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_stt_client.py -v`

Expected: collection fails because `src.stt_client` does not exist.

- [ ] **Step 3: Implement immutable models and deterministic extraction**

```python
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

def extract_address(event: TranscriptEvent) -> AddressIntent | None:
    text = " ".join(event.text.split()).strip()
    if not text:
        return None
    # Evaluate correction cues from right to left so the newest explicit
    # correction wins; accept only a non-empty, plausible street payload.
```

Implement case-insensitive patterns for `change/update/set ... address to`,
`actually ... make it`, and `make it`, strip trailing sentence punctuation,
and require the payload to contain at least one digit plus one letter.

- [ ] **Step 4: Verify GREEN and edge cases**

Run: `pytest tests/test_stt_client.py -v`

Expected: all extraction tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/stt_client.py tests/test_stt_client.py
git commit -m "feat: extract address intents from transcripts"
```

---

### Task 2: Continuous-Listening and Barge-In State Machine

**Files:**
- Modify: `src/stt_client.py`
- Modify: `tests/test_stt_client.py`
- Modify: `src/__init__.py`

**Interfaces:**
- Consumes: `VoicePipeline(on_barge_in, on_address_intent, event_sink=None)`, `set_assistant_speaking(bool)`, `on_user_speech_started(timestamp=None)`, `on_user_speech_ended(timestamp=None)`, and `on_transcript(TranscriptEvent)`.
- Produces: immediate callback calls plus structured diagnostic dictionaries containing `event` and `timestamp`.

- [ ] **Step 1: Write failing overlap and deduplication tests**

```python
def test_overlap_emits_one_immediate_barge_in_per_segment():
    calls = []
    events = []
    pipeline = VoicePipeline(lambda: calls.append("barge-in"), lambda address: None, events.append)
    pipeline.set_assistant_speaking(True)
    pipeline.on_user_speech_started(timestamp=20.0)
    pipeline.on_user_speech_started(timestamp=20.1)
    assert calls == ["barge-in"]
    assert events[-1]["timestamp"] == 20.0
    pipeline.on_user_speech_ended()
    pipeline.on_user_speech_started(timestamp=21.0)
    assert calls == ["barge-in", "barge-in"]

def test_interim_correction_is_not_duplicated_by_final_transcript():
    addresses = []
    pipeline = VoicePipeline(lambda: None, addresses.append)
    pipeline.on_user_speech_started()
    pipeline.on_transcript(TranscriptEvent("make it 221B Baker Street", False, 1.0))
    pipeline.on_transcript(TranscriptEvent("make it 221B Baker Street", True, 1.2))
    assert addresses == ["221B Baker Street"]
```

Also assert no barge-in when assistant speech is inactive and assert a later
different correction in the same segment is emitted.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_stt_client.py -v`

Expected: failures because `VoicePipeline` is missing.

- [ ] **Step 3: Implement the minimal state machine**

```python
class VoicePipeline:
    def __init__(self, on_barge_in, on_address_intent, event_sink=None):
        self._assistant_speaking = False
        self._user_speaking = False
        self._barge_in_emitted = False
        self._last_address = None

    def on_user_speech_started(self, timestamp=None):
        # Mark the segment and synchronously invoke on_barge_in once when
        # assistant speech overlaps. Record the event timestamp.
```

Use a lock around state changes, but invoke callbacks outside the lock. Reset
barge-in and address deduplication only at speech end.

- [ ] **Step 4: Verify GREEN and regression suite**

Run: `pytest tests/test_stt_client.py tests/test_orchestrator.py -v`

Expected: all tests pass.

- [ ] **Step 5: Export public interfaces and commit**

```python
from .stt_client import AddressIntent, TranscriptEvent, VoicePipeline, extract_address
```

Run: `pytest tests/test_stt_client.py -v`

```bash
git add src/stt_client.py src/__init__.py tests/test_stt_client.py
git commit -m "feat: detect voice barge-in and route intents"
```

---

### Task 3: Orchestrator Handoff Contract

**Files:**
- Create: `tests/test_agent.py`
- Create: `src/agent.py`

**Interfaces:**
- Consumes: any orchestrator implementing `on_barge_in() -> None` and `on_address_intent(address: str) -> UpdateHandle`.
- Produces: `build_voice_pipeline(orchestrator, event_sink=None) -> VoicePipeline`; the timestamp-bearing voice callback is adapted to the orchestrator's zero-argument method.

- [ ] **Step 1: Write failing callback-contract test**

```python
def test_pipeline_adapter_calls_existing_orchestrator_contract():
    orchestrator = RecordingOrchestrator()
    pipeline = build_voice_pipeline(orchestrator)
    pipeline.set_assistant_speaking(True)
    pipeline.on_user_speech_started(timestamp=30.0)
    pipeline.on_transcript(TranscriptEvent("actually make it 10 Downing Street", True, 30.2))
    assert orchestrator.calls == [("barge_in",), ("address", "10 Downing Street")]
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_agent.py::test_pipeline_adapter_calls_existing_orchestrator_contract -v`

Expected: failure because `src.agent` is missing.

- [ ] **Step 3: Implement the provider-independent adapter helper**

```python
def build_voice_pipeline(orchestrator, event_sink=None):
    return VoicePipeline(
        on_barge_in=orchestrator.on_barge_in,
        on_address_intent=orchestrator.on_address_intent,
        event_sink=event_sink,
    )
```

Keep all LiveKit imports inside `entrypoint()` or `_load_livekit()` so importing
this helper succeeds without optional SDK packages.

- [ ] **Step 4: Add a real orchestrator integration test**

Use the real `BatchRegistry`, `OrderStore(delay_seconds=0)`, and
`EpochOrchestrator` with a recording speaker. Send an initial address, mark
assistant playback active, start overlapping speech, then send the corrected
address. Assert only the corrected address remains in the store and only its
confirmation is recorded.

- [ ] **Step 5: Verify GREEN and commit**

Run: `pytest tests/test_agent.py tests/test_orchestrator.py -v`

Expected: all tests pass.

```bash
git add src/agent.py tests/test_agent.py
git commit -m "feat: connect voice events to epoch orchestrator"
```

---

### Task 4: LiveKit, Deepgram, and Silero Runtime Wiring

**Files:**
- Modify: `src/agent.py`
- Modify: `tests/test_agent.py`
- Modify: `src/config.py`
- Modify: `.env.example`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: LiveKit `JobContext`, user-state events, `user_input_transcribed` events, and agent-state events.
- Produces: `async entrypoint(ctx)`, `run()`, and adapter helpers that map SDK event objects into `VoicePipeline` calls.

- [ ] **Step 1: Write failing SDK-event mapping tests**

```python
def test_transcript_adapter_preserves_partial_and_timestamp():
    pipeline = RecordingPipeline()
    handle_transcript_event(
        pipeline,
        SimpleNamespace(transcript="make it 1600 Pennsylvania Avenue", is_final=False),
        clock=lambda: 44.0,
    )
    assert pipeline.transcripts == [
        TranscriptEvent("make it 1600 Pennsylvania Avenue", False, 44.0)
    ]

def test_user_and_agent_state_mapping_keeps_overlap_observable():
    pipeline = RecordingPipeline()
    handle_agent_state(pipeline, "speaking")
    handle_user_state(pipeline, "speaking", clock=lambda: 45.0)
    assert pipeline.actions == [("assistant", True), ("user-start", 45.0)]
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_agent.py -v`

Expected: failures because the event helpers are missing.

- [ ] **Step 3: Implement event adapters and runtime construction**

Use `AgentSession` with Deepgram Nova-3 STT and `silero.VAD.load()`. Subscribe
to transcript, user-state, and agent-state events. Do not call
`session.input.set_audio_enabled(False)` at any point. Build the existing
registry/store/speaker/orchestrator in `entrypoint`, start the session after
`ctx.connect()`, and keep `allow_interruptions=True` for agent speech.

Fail startup with a clear `RuntimeError` if LiveKit packages are unavailable or
required LiveKit/Rime environment variables are blank.

- [ ] **Step 4: Add configuration and dependencies**

```python
DEEPGRAM_MODEL = os.environ.get("DEEPGRAM_MODEL", "nova-3")
DEEPGRAM_LANGUAGE = os.environ.get("DEEPGRAM_LANGUAGE", "en")
```

Add placeholder LiveKit/Deepgram variables to `.env.example`. Add compatible
LiveKit Agents, Deepgram, and Silero plugin packages to `requirements.txt`
without removing existing dependencies.

- [ ] **Step 5: Verify GREEN, syntax, and import isolation**

Run: `pytest tests/test_agent.py -v`

Run: `python -m compileall -q src tests`

Run: `python -c "from src.agent import build_voice_pipeline; print('ok')"`

Expected: tests pass, compile exits 0, and import prints `ok` without provider credentials.

- [ ] **Step 6: Commit**

```bash
git add src/agent.py src/config.py tests/test_agent.py .env.example requirements.txt
git commit -m "feat: wire continuous LiveKit voice input"
```

---

### Task 5: Credential-Free End-to-End Demo and Documentation

**Files:**
- Create: `voice_demo.py`
- Create: `tests/test_voice_demo.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: the real provider-neutral pipeline, registry, store, orchestrator, and a local recording speaker.
- Produces: `run_demo() -> dict` containing the final order, orchestrator results, spoken confirmations, and structured pipeline events.

- [ ] **Step 1: Write the failing end-to-end demo test**

```python
def test_voice_demo_interrupts_stale_turn_and_commits_correction():
    result = run_demo()
    assert result["final_order"]["address"] == "221B Baker Street, London"
    assert result["spoken"] == [
        "Sure, I've updated your address to 221B Baker Street, London."
    ]
    assert any(event["event"] == "barge-in-detected" for event in result["events"])
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_voice_demo.py -v`

Expected: failure because `voice_demo` does not exist.

- [ ] **Step 3: Implement the scripted overlap scenario**

Start the first delayed update through a transcript, wait until its store work
begins, mark assistant speech active, emit overlapping user speech start, emit
the corrected transcript, and release both deterministic store operations.
Return serializable evidence and raise if either worker fails to finish.

- [ ] **Step 4: Repair and update README**

Resolve all committed merge-conflict markers into one coherent project README.
Document Windows and POSIX setup, `pytest -v`, `python voice_demo.py`, and
`python -m src.agent dev`. Explain required environment variables and state
that the offline demo does not need LiveKit, Deepgram, Rime, or audio hardware.

- [ ] **Step 5: Verify end to end**

Run: `pytest tests/test_voice_demo.py -v`

Run: `python voice_demo.py`

Expected: corrected address is the only committed/spoken result and output
contains a barge-in event.

- [ ] **Step 6: Run full verification**

Run: `pytest -v`

Run: `python -m compileall -q src tests voice_demo.py demo.py`

Run: `git diff --check HEAD~5..HEAD`

Expected: zero test failures, zero compile errors, and no whitespace errors.

- [ ] **Step 7: Commit without pushing**

```bash
git add voice_demo.py tests/test_voice_demo.py README.md
git commit -m "docs: add end-to-end voice pipeline demo"
git status --short --branch
```

Expected: clean working tree on the local branch and no push performed.
