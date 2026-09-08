// The bundler loads this stylesheet; TypeScript has no declaration for CSS imports.
// @ts-expect-error CSS is handled at build time.
import "./style.css";

type EventRecord = {
  event: string;
  timestamp: number;
  epoch: number;
  batchId: string;
  reason?: string;
  source?: string;
  purpose?: string;
};

type InterfaceState = {
  currentEpoch: number;
  activeBatchId: string | null;
  status: string;
  phase: string;
  rimeConfigured: boolean;
  order: { address: string; etaMinutes: number; status: string };
  events: EventRecord[];
};

const $ = <T extends HTMLElement>(id: string) => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing element #${id}`);
  return element as T;
};

const connection = $("connection");
const epoch = $("epoch");
const status = $("status");
const rime = $("rime");
const batch = $("batch");
const phase = $("phase");
const orderAddress = $("order-address");
const orderEta = $("order-eta");
const orderStatus = $("order-status");
const events = $("events");
const addressInput = $("address") as HTMLInputElement;
const addressForm = $("address-form") as HTMLFormElement;
const interruptButton = $("interrupt") as HTMLButtonElement;

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

function render(state: InterfaceState): void {
  connection.textContent = "Connected";
  connection.className = "pill success";
  epoch.textContent = String(state.currentEpoch);
  status.textContent = state.status.replaceAll("-", " ");
  rime.textContent = state.rimeConfigured ? "Key detected" : "Key missing";
  rime.className = state.rimeConfigured ? "ready" : "warning";
  batch.textContent = state.activeBatchId ?? "no active batch";
  phase.textContent = state.phase;
  orderAddress.textContent = state.order.address;
  orderEta.textContent = `${state.order.etaMinutes} minutes`;
  orderStatus.textContent = state.order.status;

  events.replaceChildren(
    ...state.events
      .slice()
      .reverse()
      .map((item) => {
        const row = document.createElement("li");
        const time = new Date(item.timestamp).toLocaleTimeString();
        const details = [item.reason, item.source, item.purpose]
          .filter(Boolean)
          .join(" / ");
        row.innerHTML = `<time>${time}</time><strong>${item.event}</strong><span>epoch ${item.epoch} · ${item.batchId}${details ? ` · ${details}` : ""}</span>`;
        return row;
      }),
  );
}

async function refresh(): Promise<void> {
  try {
    render(await request<InterfaceState>("/api/state"));
  } catch (error) {
    connection.textContent = "Backend offline";
    connection.className = "pill danger";
    console.error(error);
  }
}

addressForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const address = addressInput.value.trim();
  if (!address) return;
  await request("/api/turn", {
    method: "POST",
    body: JSON.stringify({ address }),
  });
  await refresh();
});

interruptButton.addEventListener("click", async () => {
  await request("/api/interrupt", { method: "POST" });
  await refresh();
});

void refresh();
window.setInterval(() => void refresh(), 500);
