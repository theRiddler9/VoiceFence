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

Run the complete interface:

```powershell
npm run dev
```

Open <http://localhost:3000>. The TypeScript server hosts the Vite frontend
and the `/api` routes at the same origin, so there is one process and one
browser link. CORS headers remain enabled for direct API access.

The API automatically loads the ignored root `.env` file. It uses
`MOCK_LOOKUP_DELAY_SECONDS` for the demo delay and only exposes a boolean Rime
configuration status to the browser; the API key never leaves the server.
