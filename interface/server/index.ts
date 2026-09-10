import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { fileURLToPath } from "node:url";
import dotenv from "dotenv";
import { createServer as createViteServer } from "vite";
import { InterfaceDemoSession } from "./epoch_demo";
import { LiveEventStore } from "./live_event_store";

const interfaceRoot = fileURLToPath(new URL("../", import.meta.url));
dotenv.config({ path: fileURLToPath(new URL("../../.env", import.meta.url)), quiet: true });

const port = Number(process.env.INTERFACE_PORT ?? 3000);
const delayMs = Number(process.env.MOCK_LOOKUP_DELAY_SECONDS ?? 3) * 1000;
const session = new InterfaceDemoSession(delayMs);
session.onStateChange = broadcastState;
const liveEvents = new LiveEventStore(
  process.env.VOICEFENCE_EVENT_LOG ?? fileURLToPath(new URL("../../.voicefence/live_events.jsonl", import.meta.url)),
  Boolean(process.env.RIME_API_KEY),
  Boolean(process.env.LIVEKIT_URL && process.env.LIVEKIT_API_KEY && process.env.LIVEKIT_API_SECRET),
);
const vite = await createViteServer({
  root: interfaceRoot,
  appType: "spa",
  server: { middlewareMode: true },
});

// Track active SSE connections for cleanup.
const sseClients = new Set<ServerResponse>();

function sendJson(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  });
  response.end(JSON.stringify(body));
}

function readBody(request: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => resolve(body));
    request.on("error", reject);
  });
}

function sendSseEvent(response: ServerResponse, data: unknown): void {
  try {
    response.write(`data: ${JSON.stringify(data)}\n\n`);
  } catch {
    // Client disconnected.
  }
}

const server = createServer(async (request, response) => {
  const path = request.url?.split("?")[0];

  if (request.method === "OPTIONS") {
    sendJson(response, 204, null);
    return;
  }

  try {
    // SSE endpoint — real-time event stream.
    if (request.method === "GET" && path === "/api/events") {
      response.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
      });

      // Send current state immediately.
      const initialState = liveEvents.getState() ?? session.getState();
      sendSseEvent(response, initialState);

      sseClients.add(response);

      // Watch for new live events.
      const unwatch = liveEvents.watchEvents((state) => {
        sendSseEvent(response, state);
      });

      // Keep-alive heartbeat every 15s.
      const heartbeat = setInterval(() => {
        try {
          response.write(":heartbeat\n\n");
        } catch {
          clearInterval(heartbeat);
        }
      }, 15000);

      request.on("close", () => {
        sseClients.delete(response);
        clearInterval(heartbeat);
        unwatch();
      });
      return;
    }

    if (request.method === "GET" && path === "/api/state") {
      sendJson(response, 200, liveEvents.getState() ?? session.getState());
      return;
    }

    if (request.method === "POST" && path === "/api/turn") {
      const body = JSON.parse(await readBody(request)) as { address?: string };
      const state = session.startAddressUpdate(body.address ?? "");
      sendJson(response, 200, state);
      // Push update to SSE clients.
      broadcastState();
      return;
    }

    if (request.method === "POST" && path === "/api/interrupt") {
      const state = session.interrupt();
      sendJson(response, 200, state);
      broadcastState();
      return;
    }

    if (request.method === "POST" && path === "/api/reset") {
      const state = session.reset();
      liveEvents.clearEvents();
      sendJson(response, 200, state);
      broadcastState();
      return;
    }

    // Live mode endpoints — write events to JSONL for testing without voice.
    if (request.method === "POST" && path === "/api/live/turn") {
      const body = JSON.parse(await readBody(request)) as { address?: string };
      const address = body.address?.trim();
      if (!address) {
        sendJson(response, 400, { error: "address is required" });
        return;
      }
      const epoch = (liveEvents.getState()?.currentEpoch ?? 0) + 1;
      const batchId = `batch-live-${Date.now()}`;
      liveEvents.appendEvent({ event: "epoch-started", epoch, batch_id: batchId });
      liveEvents.appendEvent({ event: "address-intent", epoch, batch_id: batchId, address });
      liveEvents.appendEvent({ event: "tool-started", epoch, batch_id: batchId, purpose: "address-lookup" });

      // Simulate delayed lookup completion.
      setTimeout(() => {
        liveEvents.appendEvent({
          event: "tool-completed",
          epoch,
          batch_id: batchId,
          order: { address, eta_minutes: 30, status: "updated" },
        });
        liveEvents.appendEvent({ event: "tts-started", epoch, batch_id: batchId });
        setTimeout(() => {
          liveEvents.appendEvent({ event: "tts-completed", epoch, batch_id: batchId });
        }, 500);
      }, delayMs);

      sendJson(response, 200, { ok: true, epoch, batchId });
      return;
    }

    if (request.method === "POST" && path === "/api/live/interrupt") {
      const currentState = liveEvents.getState();
      const epoch = (currentState?.currentEpoch ?? 0);
      const batchId = currentState?.activeBatchId ?? "unknown";
      liveEvents.appendEvent({ event: "barge-in-detected", epoch, batch_id: batchId, reason: "barge_in" });
      liveEvents.appendEvent({ event: "epoch-invalidated", epoch, batch_id: batchId, reason: "barge_in" });
      sendJson(response, 200, { ok: true });
      return;
    }

    if (request.method === "POST" && path === "/api/live/clear") {
      liveEvents.clearEvents();
      sendJson(response, 200, { ok: true });
      return;
    }

    vite.middlewares(request, response, () => {
      response.statusCode = 404;
      response.end("Not found");
    });
  } catch (error) {
    sendJson(response, 400, { error: error instanceof Error ? error.message : "Request failed" });
  }
});

function broadcastState(): void {
  const state = liveEvents.getState() ?? session.getState();
  for (const client of sseClients) {
    sendSseEvent(client, state);
  }
}

function listen(requestedPort: number): void {
  server.once("error", (error: NodeJS.ErrnoException) => {
    if (error.code !== "EADDRINUSE" || requestedPort === 0) throw error;
    console.warn(`Port ${requestedPort} is busy; selecting an available local port.`);
    listen(0);
  });
  server.listen(requestedPort, "127.0.0.1");
}

server.on("listening", () => {
  const address = server.address();
  if (address && typeof address !== "string") {
    console.log(`VoiceFence is running at http://localhost:${address.port}`);
  }
});

listen(port);
