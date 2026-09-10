# Rime Evidence

## Hard Voice Claim
**Safe handling of superseding intent (barge-in) during long-running tool calls.**
When a user interacts with a voice agent connected to a slow backend API, they often change their mind before the initial request finishes. The system must gracefully cancel or fence the stale request and ensure that the Text-to-Speech (TTS) engine does not speak the wrong, outdated confirmation.

## Acceptance Test
If a user requests an address change (Intent A), and then interrupts to provide a corrected address (Intent B) *before* Intent A's backend lookup completes, the system must:
1. Advance the orchestration epoch and invalidate Intent A's context.
2. Mark Intent A's late-arriving tool result as `stale-dropped`.
3. Process Intent B successfully.
4. Only generate and play Rime TTS for Intent B.

## Procedure
We implemented an offline fixture to deterministically test this race condition without requiring a live microphone or network connection.

Run the provided python fixture:
```bash
python voice_demo.py
```

## Result
The system successfully prevents the stale TTS from playing. The output of `voice_demo.py` confirms that:
- The first batch (`batch-1`) is correctly marked as `stale-dropped`.
- The corrected batch (`batch-2`) is marked as `completed`.
- The `spoken` array proves that **only** the confirmation for the corrected address ("221B Baker Street, London") was passed to the Rime TTS engine.

## Limitations
- **Regex Intent Extraction**: To isolate the concurrency and epoch-fencing logic, this demo uses a simplified regex-based intent extractor rather than a full LLM. It requires specific phrasing (e.g., "Change my address to...") to trigger updates.
- **Mocked Backend**: The Order Store uses a simulated thread sleep to emulate slow API latency.
