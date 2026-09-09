# VoiceFence

VoiceFence is a voice-native delivery assistant built around one safety rule:
an interrupted request must never write or speak a late result. It runs as a
continuous-listening LiveKit agent and includes a credential-free deterministic
demo of the same end-to-end fencing behavior.

## What the demo proves

1. The caller requests an address change to `42 Wallaby Way, Sydney`.
2. The deliberately delayed order update begins.
3. The caller interrupts and corrects it to `221B Baker Street, London`.
4. The old epoch and shared batch are cancelled.
5. The late result is dropped; only the corrected address is stored and spoken.

The offline scenario uses explicit synchronization rather than timing guesses,
so the race and its expected outcome are reproducible.

## Architecture

```text
User speech
  -> LiveKit VAD and Deepgram streaming STT
  -> provider-neutral VoicePipeline
  -> EpochOrchestrator
  -> fenced OrderStore update
  -> session-managed Rime confirmation
```

Every tool call and speech request receives a monotonically increasing epoch
and unique batch ID. Barge-in immediately advances the epoch and cancels the
active batch. The registry lock is the common linearization point for
cancellation and order commits, preventing a timed-out or superseded update
from mutating state.

`VoicePipeline` handles interim and final transcripts, corrections, anonymous
VAD turns, canonical deduplication, and concurrent callback serialization. The
live runtime uses LiveKit `AgentSession`, Deepgram Nova-3 STT, prewarmed Silero
VAD, and Rime TTS. LiveKit owns playback, allowing interruption to stop both
session audio and the associated epoch.

## Setup

### Windows PowerShell

```powershell
git clone <repo-url>
Set-Location VoiceFence
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

### macOS and Linux

```bash
git clone <repo-url>
cd VoiceFence
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Never commit the populated `.env` file.

## Offline demo

The offline demo needs no provider credentials, microphone, audio device, or
network connection:

```bash
python voice_demo.py
```

It prints JSON evidence. The first result must be `stale-dropped`, the corrected
result must be `completed`, and the only saved and spoken address must be
`221B Baker Street, London`.

## Live agent

Configure these environment variables before starting the worker:

| Variable | Purpose |
| --- | --- |
| `LIVEKIT_URL` | LiveKit WebSocket URL |
| `LIVEKIT_API_KEY` | LiveKit API key |
| `LIVEKIT_API_SECRET` | LiveKit API secret |
| `DEEPGRAM_API_KEY` | Deepgram streaming STT credential |
| `RIME_API_KEY` | Rime TTS credential |

Deepgram defaults to model `nova-3` and language `en`. Rime defaults are listed
in `.env.example`, including `mistv2`, speaker `astra`, and 16 kHz output.

```bash
python -m src.agent dev
```

## Tests

The Python suite is deterministic, offline, and requires no API keys or audio
hardware:

```bash
python -m pytest -q
```

It covers batch cancellation, atomic order commits, stale epochs, timeout
races, streaming speech cancellation, transcript extraction and deduplication,
barge-in, and complete correction scenarios.

## Web interface

The `interface/` directory provides the TypeScript/Vite visualization added on
`main`. It displays microphone state, epoch and batch state, current order, Rime
configuration status without exposing the key, and the interruption event log.
Its backend is a demo adapter; the production voice path remains the Python
LiveKit agent.

```powershell
Set-Location interface
npm.cmd install
npm.cmd test
npm.cmd run build
npm.cmd run dev
```

## Repository layout

```text
src/
  agent.py            LiveKit composition root and event adapters
  stt_client.py       Transcript parsing, deduplication, and barge-in logic
  orchestrator.py     Epoch fencing, cancellation, and timeout ownership
  batch_registry.py   Atomic cancelled-batch registry
  order_store.py      Delayed, cancellable in-memory order store
  rime_speaker.py     Standalone Rime PCM speaker
interface/            TypeScript/Vite demo and visualization
voice_demo.py         Credential-free end-to-end voice-pipeline demo
demo.py               Standalone epoch/Rime demonstration
tests/                Unit, integration, and scenario tests
```

## Scope and limitations

VoiceFence focuses on delivery-address correction and interruption safety. The
order store is intentionally in memory. Telephony/SIP, persistence,
authentication, deployment, and multilingual production tuning are outside the
current scope. Browser waveform visualization is local microphone input, not a
LiveKit audio stream; real latency claims require measured acceptance runs.
