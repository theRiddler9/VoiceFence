# Joy — TTS & Tools

The "mouth" (Rime TTS) and the "backend" (a mock order lookup) for the
assistant demo. Both can be killed mid-action the instant the
orchestrator says a batch is outdated.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your real RIME_API_KEY
export $(cat .env | xargs)   # or use python-dotenv / your shell's preferred method
```

`sounddevice` needs PortAudio available on the system (`brew install
portaudio` on macOS, `apt-get install libportaudio2` on Linux). If no
audio device is available, `RimeSpeaker` automatically falls back to a
silent no-op sink and logs a warning — cancellation logic still works,
you just won't hear anything.

## Step 1: confirm the Rime connection works at all

Before touching cancellation, just make sure you can hear it speak:

```python
from src import BatchRegistry, RimeSpeaker

registry = BatchRegistry()
speaker = RimeSpeaker(registry)
result = speaker.speak("Hello from Rime.", batch_id="smoke-test")
print(result)  # SpeakResult(status="completed", ...)
```

## Layout

```
src/
  config.py          # env vars: RIME_API_KEY, model/voice, MOCK_LOOKUP_DELAY_SECONDS, ...
  batch_registry.py  # shared "is this batch id cancelled?" registry
  rime_speaker.py     # streams + plays TTS audio, killable mid-stream
  order_store.py      # one in-memory fake order, adjustable delay, killable mid-lookup
demo.py               # end-to-end scenario mirroring the phone-rep interrupt analogy
tests/                # unit tests for both components, no real network/audio needed
```

## How cancellation works

Every request into either component carries a `batch_id`. The
orchestrator calls `registry.cancel(batch_id)` when that batch is no
longer needed. Both components poll `registry.is_cancelled(batch_id)`
frequently:

- **RimeSpeaker** checks before writing *each* audio chunk to the output
  stream. On cancel, it calls `sink.abort()` (which drops anything
  already buffered for playback, not just stops new writes) and closes
  the HTTP connection to Rime so no more audio is even pulled down.
- **OrderStore** sleeps in small increments (default 50ms) instead of
  one long `sleep(delay)`, checking for cancellation between increments.
  A late/cancelled result is simply discarded — the in-memory order is
  never touched.

This is the same shape as the phone-rep analogy in the spec: stop
talking immediately, and throw away the lookup that was in flight,
rather than either talking over the interruption or silently applying a
stale result.

## Config knobs (env vars, see `.env.example`)

| Var | Purpose |
|---|---|
| `RIME_API_KEY` | required, never hardcode this |
| `RIME_MODEL_ID`, `RIME_SPEAKER`, `RIME_LANGUAGE` | voice selection — pick deliberately, judges check for this |
| `RIME_SAMPLING_RATE`, `RIME_SPEED_ALPHA` | audio quality/pacing |
| `MOCK_LOOKUP_DELAY_SECONDS` | artificial backend delay, the knob Person D sweeps |

`OrderStore(registry, delay_seconds=...)` also accepts the delay
directly per-instance, which is what the tests use to avoid depending on
env vars.

## Running the tests

```bash
pytest tests/ -v
```

13 tests cover: normal completion, cancel-before-start (should skip work
entirely), and cancel-mid-flight (should stop immediately and leave no
stale state) — for both the speaker and the order store. The Rime tests
fake the HTTP layer so they run without a real API key or network
access.

## Running the demo

```bash
export RIME_API_KEY=...   # optional — the order-store half works without it
python demo.py
```

Simulates the orchestrator starting a lookup + speech under one batch
id, cancelling it a second later, and starting a fresh batch — showing
the first batch's results get dropped and the order store is only ever
touched by the batch that actually finished.

## Known risk to confirm on day one

Rime's HTTP PCM streaming endpoint (what this is built on) is a plain
`POST` that streams headerless 16-bit PCM back — killing the *client-side*
consumption of that stream is what this code does. If a WebSocket-based
integration is used instead later (e.g. via Pipecat's `RimeTTSService`),
double check that its interruption/barge-in support is wired up the same
way, since the mechanics differ from a plain HTTP stream.
