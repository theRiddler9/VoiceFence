# VoiceFence interface

This is a browser control and visualization layer for the Epoch demo and live
voice pipeline. When the Python LiveKit worker writes the configured JSONL
event log, the interface displays the live orchestration state. Without a live
worker, it falls back to the deterministic TypeScript demo session.

The interface does not expose provider credentials and does not replace the
LiveKit voice runtime. It must not be used as the source for
`RIME_EVIDENCE.md`; measured acceptance runs remain the evidence source.

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

For live mode, start the Python worker with `VOICEFENCE_EVENT_LOG` pointing to
the same JSONL path used by the interface server. The default path is
`.voicefence/live_events.jsonl` relative to the repository root. The interface
automatically switches from demo mode to live mode when that file contains
events.
