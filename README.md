# VoiceFence

VoiceFence is a voice-address-update demo built around one safety rule: an
interrupted request must never write or speak a late result. It demonstrates
that rule locally without credentials, and can run as a continuous-listening
LiveKit agent when the provider configuration is available.

## What the demo proves

The scripted scenario is deliberately small:

1. The caller asks to change an address to `42 Wallaby Way, Sydney`.
2. The order update starts but is held in progress.
3. While the assistant is speaking, the caller interrupts and corrects the
   address to `221B Baker Street, London`.
4. The first epoch is cancelled. When its delayed store operation is released,
   it is dropped; only the corrected address is saved and confirmed.

The offline demo waits for explicit store-start and store-release events rather
than relying on a sleep to make an overlap likely.

## Architecture

```text
User speech
  -> LiveKit speech-state / transcript events
  -> provider-neutral VoicePipeline
  -> EpochOrchestrator
  -> fenced OrderStore update and session-managed Rime confirmation
```

`VoicePipeline` is independent of LiveKit and provider SDKs. It detects
barge-in on real user-speech-start while assistant speech is active, then calls
the orchestrator immediately; it does not wait for a final transcript. The
orchestrator owns the monotonically increasing epoch and cancels the active
batch. Both tool work and speech are tagged with that batch, so late work is
fenced before it can update the order or produce a confirmation.

For the live path, `src.agent` builds a LiveKit `AgentSession` with Deepgram
Nova-3 STT, Silero VAD, and Rime TTS. Confirmations are played by the
session-managed LiveKit speaker, so barge-in interrupts the actual session
playout as well as advancing the epoch. The Silero model is prewarmed once per
worker process before a room is assigned.

Transcript processing accepts interim and final text. It deduplicates
item-id-backed turns and handles anonymous finals that arrive after a VAD
boundary, while still allowing a genuine later correction to be processed.

## Quick start: offline demo

The offline demo does not need the optional LiveKit, Deepgram, or Rime runtime
packages or credentials. It does not open a microphone, audio device, or
network connection.

After installing the project dependencies, run:

```bash
python voice_demo.py
```

It prints JSON evidence containing the final order, serializable orchestrator
results, recorded confirmations, and structured pipeline events. The final
order and only confirmation should use `221B Baker Street, London`, and the
events include `barge-in-detected`.

## Setup

### Windows PowerShell

```powershell
git clone <repo-url>
Set-Location VoiceFence
python -m venv .venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell prevents activation, use the environment's interpreter directly:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe voice_demo.py
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

The `.env` file is a template; this project reads the live-agent settings from
the process environment. Set the required values in the shell that starts the
agent.

## Running tests

With the virtual environment active:

```bash
pytest -v
```

The tests use local fakes and deterministic synchronization. They do not make
provider requests, require API keys, or require audio hardware.

## Running the LiveKit agent

The live worker requires all of these environment variables:

| Variable | Purpose |
| --- | --- |
| `LIVEKIT_URL` | LiveKit server WebSocket URL. |
| `LIVEKIT_API_KEY` | LiveKit API key. |
| `LIVEKIT_API_SECRET` | LiveKit API secret. |
| `DEEPGRAM_API_KEY` | Deepgram streaming STT credential. |
| `RIME_API_KEY` | Rime TTS credential used by the LiveKit session. |

`DEEPGRAM_MODEL` defaults to `nova-3` and `DEEPGRAM_LANGUAGE` defaults to
`en`. `RIME_MODEL_ID`, `RIME_SPEAKER`, `RIME_LANGUAGE`,
`RIME_SAMPLING_RATE`, and `RIME_SPEED_ALPHA` select the Rime voice and have
safe defaults. `MOCK_LOOKUP_DELAY_SECONDS` controls the in-memory backend
delay used for experiments. Never commit real credentials.

In PowerShell, for example:

```powershell
$env:LIVEKIT_URL = "wss://your-livekit-host"
$env:LIVEKIT_API_KEY = "your_livekit_api_key"
$env:LIVEKIT_API_SECRET = "your_livekit_api_secret"
$env:DEEPGRAM_API_KEY = "your_deepgram_api_key"
$env:RIME_API_KEY = "your_rime_api_key"
python -m src.agent dev
```

On macOS or Linux:

```bash
export LIVEKIT_URL='wss://your-livekit-host'
export LIVEKIT_API_KEY='your_livekit_api_key'
export LIVEKIT_API_SECRET='your_livekit_api_secret'
export DEEPGRAM_API_KEY='your_deepgram_api_key'
export RIME_API_KEY='your_rime_api_key'
python -m src.agent dev
```

The agent validates these values before it connects. Speak an address update,
then begin speaking again while the confirmation is active to exercise the
barge-in path.

## Repository layout

```text
src/
  agent.py            LiveKit composition root and event adapters
  stt_client.py       Provider-neutral transcript, deduplication, and barge-in logic
  orchestrator.py     Epoch fencing and cancellation ownership
  batch_registry.py   Thread-safe cancelled-batch registry
  order_store.py      In-memory delayed, cancellable address store
  rime_speaker.py     Standalone Rime PCM speaker used outside the live session
voice_demo.py         Credential-free deterministic end-to-end demo
demo.py               Standalone epoch/Rime smoke demonstration
tests/                Offline unit and integration-contract tests
```

## Scope and limits

VoiceFence intentionally focuses on one delivery-address correction flow. It
does not include a database, telephony/SIP bridge, general conversational
intent recognition, multilingual tuning, deployment, or authentication. The
store is an in-memory mock so the safety behavior can be inspected and tested
without external infrastructure.
