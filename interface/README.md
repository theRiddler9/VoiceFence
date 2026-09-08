# VoiceFence interface

This is a temporary browser control and visualization layer for the Epoch
demo. It shows delayed lookups, interruptions, stale-result drops, order state,
and the Rime configuration status without replacing Riya's future LiveKit
voice pipeline.

The server-side demo adapter is intentionally separate from the Python
orchestrator. It does not claim measured Rime audio behavior and must not be
used as the source for `RIME_EVIDENCE.md`; Abhinav's measured acceptance flow
remains the evidence source.

## Run locally

From this directory:

```powershell
npm install
npm test
npm run build
```

Use two terminals for the live interface:

```powershell
npm run dev:server
```

```powershell
npm run dev:client
```

Open <http://localhost:5173>.

The API uses `MOCK_LOOKUP_DELAY_SECONDS` and detects `RIME_API_KEY` from the
process environment. The interface does not load `.env` automatically.
