import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { InterfaceDemoSession } from "./epoch_demo";

const port = Number(process.env.INTERFACE_PORT ?? 3001);
const delayMs = Number(process.env.MOCK_LOOKUP_DELAY_SECONDS ?? 3) * 1000;
const session = new InterfaceDemoSession(delayMs);

function sendJson(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "http://localhost:5173",
    "Access-Control-Allow-Headers": "Content-Type",
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
  if (request.method === "OPTIONS") {
    response.writeHead(204, {
      "Access-Control-Allow-Origin": "http://localhost:5173",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    });
    response.end();
    return;
  }

  try {
    if (request.method === "GET" && request.url === "/api/state") {
      sendJson(response, 200, session.getState());
      return;
    }

    if (request.method === "POST" && request.url === "/api/turn") {
      const body = JSON.parse(await readBody(request)) as { address?: string };
      sendJson(response, 200, session.startAddressUpdate(body.address ?? ""));
      return;
    }

    if (request.method === "POST" && request.url === "/api/interrupt") {
      sendJson(response, 200, session.interrupt());
      return;
    }

    if (request.method === "POST" && request.url === "/api/reset") {
      sendJson(response, 200, session.reset());
      return;
    }

    sendJson(response, 404, { error: "Not found" });
  } catch (error) {
    sendJson(response, 400, { error: error instanceof Error ? error.message : "Request failed" });
  }
});

server.listen(port, () => {
  console.log(`VoiceFence interface API listening on http://localhost:${port}`);
});
