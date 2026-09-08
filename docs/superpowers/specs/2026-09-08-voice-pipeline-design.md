# Voice Pipeline Design

## Purpose

Implement the Voice Pipeline Lead's assigned portion of VoiceFence as a local,
single-process pipeline that continuously listens to the caller, produces
streaming transcripts, detects barge-in while the assistant is speaking,
extracts corrected delivery addresses, and hands events to the existing
`EpochOrchestrator` without owning cancellation policy.

The pipeline must be demonstrable end to end with LiveKit, Deepgram STT, Silero
VAD, the existing orchestrator, the mock order store, and the existing Rime
speaker. Its core behavior must also be testable offline without API keys,
audio hardware, or network access.

## Scope

### In scope

- Join a LiveKit room and continuously consume microphone audio.
- Use Silero VAD and LiveKit turn handling to detect user speech.
- Use Deepgram Nova-3 streaming STT and retain useful interim transcripts.
- Emit barge-in immediately when user speech begins during assistant playback.
- Extract a delivery-address correction from partial or final transcripts.
- Forward barge-in and address intent through the existing orchestrator hooks.
- Configure credentials and provider choices through environment variables.
- Provide deterministic unit and integration-contract tests.
- Provide a local entry point and documented smoke-test procedure.

### Out of scope

- Cancellation or epoch policy, which remains owned by `EpochOrchestrator`.
- A database, web frontend, telephony/SIP, deployment, multilingual tuning, or
  production noise-calibration work.
- Replacing the team's Rime speaker or order-store implementation.
- General conversational intent recognition beyond the address-update demo.

## Architecture

The implementation has two boundaries:

1. `src/stt_client.py` contains provider-neutral transcript models, address
   extraction, transcript accumulation, and the voice-pipeline state machine.
   It has no LiveKit imports and is fully testable offline.
2. `src/agent.py` is the LiveKit adapter and composition root. It configures
   Deepgram STT, Silero VAD, continuous room input, Rime output, the order
   store, and `EpochOrchestrator`, then translates LiveKit events into the
   provider-neutral pipeline.

This separation prevents SDK event-shape changes from leaking into business
logic and keeps the handoff contract independently verifiable.

## Components and Contracts

### Transcript event

`TranscriptEvent` is an immutable value object containing:

- `text`: normalized transcript text available so far.
- `is_final`: whether the STT provider considers the phrase final.
- `timestamp`: monotonic event time used for latency evidence.

Blank transcripts are ignored. Interim text is retained because an
interruption may be incomplete when the signal is first emitted.

### Address intent

`AddressIntent` contains:

- `address`: the extracted address with command/filler wording removed.
- `transcript`: the source transcript.
- `is_final`: whether extraction came from final STT output.
- `timestamp`: the originating transcript timestamp.

Extraction is deterministic and deliberately narrow. It recognizes the demo's
address-update cues, including `change/update/make/set ... address to`,
`actually ...`, and direct corrections such as `make it ...`. It requires a
plausible address payload and rejects unrelated uses of the word `address`.
When one transcript contains an old and corrected address, the last explicit
correction wins.

The extractor does not call an LLM. This avoids adding latency, nondeterminism,
and another credential to the demo's critical path. The interface leaves room
for a future extractor implementation without changing the LiveKit adapter.

### Voice pipeline state

`VoicePipeline` receives three injected callbacks:

- `on_barge_in()` for the existing orchestrator interrupt hook.
- `on_address_intent(address)` for the existing address-update hook.
- An optional event sink for structured diagnostics.

It tracks whether assistant audio is active and whether the current user speech
segment has already emitted a barge-in. On user-speech start:

- If assistant audio is active and no interrupt was emitted for this speech
  segment, emit one immediately.
- Never wait for a final transcript before emitting the interrupt.
- Suppress repeated VAD-start events until user-speech end resets the segment.

On interim and final transcripts, it runs extraction. It emits a new address
only when the normalized extracted value differs from the most recently emitted
value for the current speech segment. A later correction in the same segment is
allowed and replaces the earlier candidate. Speech end clears segment-local
deduplication state.

### LiveKit adapter

`src/agent.py` imports LiveKit dependencies lazily enough that offline unit
tests can import the provider-neutral module without the SDK installed. The
entry point:

1. Loads environment configuration.
2. Connects to the LiveKit room.
3. Constructs `BatchRegistry`, `OrderStore`, `RimeSpeaker`, and
   `EpochOrchestrator`.
4. Creates an `AgentSession` with Deepgram Nova-3 STT, Silero VAD, and
   continuous room audio enabled.
5. Subscribes to user speech/transcription and assistant speech-state events.
6. Routes those events into `VoicePipeline`.

The adapter uses SDK-native turn and speech events rather than polling audio
levels. It does not use push-to-talk and never disables microphone input while
the assistant is speaking.

## Data Flow

```text
LiveKit microphone
  -> Silero VAD user-speech-start
     -> VoicePipeline overlap check
        -> EpochOrchestrator.on_barge_in() immediately when overlapping
  -> Deepgram interim/final transcript
     -> TranscriptEvent
        -> deterministic address extraction
           -> EpochOrchestrator.on_address_intent(address)
              -> fenced OrderStore update
                 -> fenced RimeSpeaker confirmation
```

Assistant playback state flows back from the LiveKit/session integration into
`VoicePipeline`, allowing overlap detection while microphone capture remains
active.

## Configuration

The following values come only from environment variables:

- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `DEEPGRAM_API_KEY` when direct Deepgram credentials are required
- Existing Rime and mock-store variables
- `DEEPGRAM_MODEL`, defaulting to `nova-3`
- `DEEPGRAM_LANGUAGE`, defaulting to `en`

`.env.example` documents placeholders only. No credential values are logged,
stored, or committed.

## Error Handling and Observability

- Invalid or blank transcript payloads are ignored and logged as structured
  diagnostic events.
- Address-extraction misses are normal and do not produce errors.
- Callback failures are logged with event context and allowed to surface at the
  composition boundary; the pipeline must not pretend the orchestrator
  accepted an event that failed.
- LiveKit connection and provider initialization failures terminate startup
  with actionable messages naming the missing configuration or dependency.
- Events include monotonic timestamps so the evidence layer can measure VAD
  speech-start to orchestrator interrupt-call latency.
- Logs never include secrets. Transcript text is acceptable for this local demo
  but is clearly treated as diagnostic data.

## Testing Strategy

Implementation follows test-driven development.

### Offline unit tests

- Speaking-over-assistant emits barge-in immediately.
- Speech without assistant playback does not emit barge-in.
- Repeated VAD starts in one segment emit only one barge-in.
- A new speech segment can emit another barge-in.
- Interim transcripts can yield usable address intents.
- Unrelated sentences do not yield address intents.
- A correction in one transcript selects the latest address.
- An updated interim correction emits once and is not duplicated by the final
  transcript.
- Timestamps and transcript metadata survive the handoff.

### Contract tests

- A recording orchestrator receives the exact `on_barge_in()` and
  `on_address_intent(address)` calls expected from a scripted overlap.
- A corrected address traverses the real `EpochOrchestrator` and real
  `OrderStore` with a fake speaker, proving the stale turn is dropped and only
  the corrected address is committed.
- LiveKit adapter tests use small event objects at the adapter boundary rather
  than network calls or microphone hardware.

### Verification

- Run the complete pytest suite.
- Compile all Python source files.
- Verify imports in the no-credentials test environment.
- Run an offline scripted end-to-end scenario.
- When credentials and microphone access are available, run the documented
  LiveKit smoke test and manually interrupt active assistant speech.

## Files Changed

- Add `src/stt_client.py`.
- Add `src/agent.py`.
- Add focused tests for both modules and the end-to-end handoff.
- Update `src/config.py`, `src/__init__.py`, `requirements.txt`, and
  `.env.example` for the voice-pipeline dependencies and configuration.
- Add a small offline pipeline demo or extend the existing demo only where it
  does not obscure the existing epoch demonstration.
- Update only the README sections necessary to run the voice pipeline; resolve
  the pre-existing conflict-marker damage separately only if required for
  accurate instructions.

## Acceptance Criteria

- The microphone remains logically enabled while assistant audio is active.
- The first overlapping user-speech-start event calls the orchestrator without
  waiting for transcription completion.
- A transcript containing a corrected delivery address sends the corrected
  address, not the abandoned one.
- The voice-pipeline layer never directly cancels batches or mutates orders.
- Core behavior is testable without network, credentials, or audio hardware.
- The complete existing and new automated test suite passes.
- Local configuration contains no committed secrets.
- No repository changes are pushed.
