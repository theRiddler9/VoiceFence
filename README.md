# VoiceFence

VoiceFence is a voice-native delivery assistant that stays correct when a
user interrupts an in-progress request.

The product focuses on a realistic voice failure: a slow address lookup is
still running when the user changes the request. VoiceFence stops outdated
speech and fences the old lookup result so it cannot overwrite the order or
be spoken as the current answer.

## Why Voice Matters

Voice creates the failure mode. A person can interrupt while the assistant is
speaking or while a backend tool is still running. A text-only workflow would
usually serialize these actions and hide the race.

The demo represents a hands-busy delivery workflow:

1. The user asks to change a delivery address.
2. The backend performs a deliberately delayed lookup.
3. The user interrupts with a corrected address.
4. The first epoch is invalidated.
5. The late result from the first request is dropped.
6. Only the corrected address is stored and confirmed.

## Voice Engineering Problem

VoiceFence solves interruption and recovery with epoch fencing.

Every tool call and speech request receives a monotonically increasing epoch
and a unique batch ID. When the user interrupts, the active batch is cancelled
and the epoch advances. A result is usable only when its epoch and batch are
still current.

This prevents two stale-result failures:

- Outdated Rime audio continues after the user changes the request.
- A delayed backend result overwrites the newer order state.

## Architecture

```text
User microphone
      |
      v
LiveKit voice pipeline
  VAD, turn detection, STT, barge-in
      |
      v
EpochOrchestrator
  epoch state, cancellation, timeout, stale-result fencing
      |                         |
      v                         v
OrderStore                  RimeSpeaker
delayed mock lookup         streaming PCM playback
      |                         |
      +------------+------------+
                   v
             Current response
```

The current Python implementation exposes the integration hooks
`on_barge_in()` and `on_address_intent(address)`. LiveKit and speech-to-text
integration are owned by the voice-pipeline work and are not duplicated in
the web interface.

## Repository Layout

```text
VoiceFence/
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
├── demo.py
├── src/
│   ├── batch_registry.py
│   ├── config.py
│   ├── orchestrator.py
│   ├── order_store.py
│   └── rime_speaker.py
├── tests/
│   ├── test_batch_registry.py
│   ├── test_order_store.py
│   ├── test_orchestrator.py
│   └── test_rime_speaker.py
└── interface/
    ├── index.html
    ├── package.json
    ├── server/
    │   ├── epoch_demo.test.ts
    │   ├── epoch_demo.ts
    │   └── index.ts
    └── src/
        ├── main.ts
        └── tailwind.css
```

## Rime Configuration

Rime is the primary spoken output. The current Python path uses:

| Setting | Value |
|---|---|
| Model ID | `mistv2` |
| Speaker | `astra` |
| Language | `eng` |
| Endpoint | `https://users.rime.ai/v1/rime-tts` |
| Audio format | Headerless 16-bit little-endian PCM |
| Sampling rate | `16000` Hz |
| Speed alpha | `1.0` |
| Transport | HTTP `POST` with streamed PCM response |

The API key is loaded from the `RIME_API_KEY` environment variable. It is
never committed, included in the frontend, or exposed by the interface API.

## Setup

Create a local environment and install the Python dependencies:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set the real Rime key in `.env`:

```powershell
Copy-Item .env.example .env
```

The environment file contains these settings:

```text
RIME_API_KEY
RIME_MODEL_ID
RIME_SPEAKER
RIME_LANGUAGE
RIME_SAMPLING_RATE
RIME_SPEED_ALPHA
MOCK_LOOKUP_DELAY_SECONDS
```

The real `.env` file is ignored by Git. Only `.env.example`, containing no
secret, belongs in the repository.

## Python Demo

Run the deterministic interruption scenario:

```powershell
python demo.py
```

The expected state transition is:

```text
first: stale-dropped
second: completed
final address: 221B Baker Street, London
```

The order-store behavior can be demonstrated without a Rime key. With a
valid key and an available audio device, the confirmation is spoken by Rime.
Without an audio device, the speaker falls back to a silent sink while the
cancellation logic remains testable.

## Tests

Run the Python test suite from the repository root:

```powershell
python -m pytest -q
```

The tests cover batch cancellation, delayed order updates, stale epoch
results, timeout handling, Rime stream cancellation, and orchestration event
hooks.

## Web Interface

The `interface/` directory contains a TypeScript/Vite control and
visualization layer for the Epoch demo. It shows:

- Microphone state and a browser waveform preview.
- Current epoch and active batch.
- Rime configuration status without exposing the API key.
- Current order state.
- An event logger showing lookup, interruption, stale-result drops, and speech
  lifecycle events.

The interface backend is a TypeScript demo adapter. It demonstrates the
state-fencing flow independently from the Python orchestrator; it does not
claim to be the measured Rime audio path.

Install and verify the interface:

```powershell
Set-Location interface
npm.cmd install
npm.cmd test
npm.cmd run build
```

Run it locally:

```powershell
npm.cmd run dev
```

Open the URL printed by the server. The API loads the ignored root `.env` and
exposes only whether the Rime key is configured.

## Acceptance Test

The acceptance scenario is:

1. Start an address update with a fixed backend delay.
2. Interrupt before the lookup finishes.
3. Submit a corrected address.
4. Verify that the stale result is dropped.
5. Verify that only the corrected address reaches the final order state.
6. Verify that the current response is the only confirmation spoken.
7. Verify that timeout produces an explicit failure response rather than a
   guess or an old result.

The repository tests provide deterministic coverage of this behavior. Real
user-visible latency and stale-audio measurements must come from repeatable
acceptance runs and must not be invented in documentation.

## Evidence

`RIME_EVIDENCE.md` must report the shipped path, acceptance procedure,
measured results, and limitations from real runs. It should include:

- Barge-in to silence latency.
- Corrected-turn time to first audio.
- Stale-result and stale-data leak counts.
- Timeout and failure-path results.
- Exact Rime configuration used during the run.

The web interface event logger is useful for explaining the flow, but it is
not a substitute for measured evidence.

## Failure Behavior and Limitations

- A cancelled batch is not allowed to mutate the order or continue playback.
- A late tool result is reported as stale and discarded.
- A tool timeout invalidates the old batch and produces an explicit failure
  message.
- The current interface does not provide LiveKit transport or speech-to-text.
- The browser waveform visualizes local microphone input; it is not a LiveKit
  audio stream.
- The mock order store is in memory and is not a production database.
- Cancellation is polling-based, so the measured cancellation interval must
  be reported honestly.

