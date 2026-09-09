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

const server = createServer(async (request, response) => {
  const path = request.url?.split("?")[0];

  if (request.method === "OPTIONS") {
    sendJson(response, 204, null);
    return;
  }

  try {
    if (request.method === "GET" && path === "/api/state") {
      sendJson(response, 200, liveEvents.getState() ?? session.getState());
      return;
    }

    if (request.method === "POST" && path === "/api/turn") {
      const body = JSON.parse(await readBody(request)) as { address?: string };
      sendJson(response, 200, session.startAddressUpdate(body.address ?? ""));
      return;
    }

    if (request.method === "POST" && path === "/api/interrupt") {
      sendJson(response, 200, session.interrupt());
      return;
    }

    if (request.method === "POST" && path === "/api/reset") {
      sendJson(response, 200, session.reset());
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
