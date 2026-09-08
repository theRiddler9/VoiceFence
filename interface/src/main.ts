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

const $ = <T extends HTMLElement>(id: string): T => {
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
const voiceOrb = $("voice-orb");
const turnLabel = $("turn-label");
const turnCaption = $("turn-caption");
const requestPreview = $("request-preview");
const orderAddress = $("order-address");
const orderEta = $("order-eta");
const orderStatus = $("order-status");
const staleCount = $("stale-count");
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

function displayStatus(value: string): string {
  return value.replaceAll("-", " ");
}

function describeTurn(state: InterfaceState): [string, string] {
  if (state.status === "lookup-pending") {
    return ["Checking the address", "The backend is working under the current epoch."];
  }
  if (state.status === "speaking") {
    return ["Rime is speaking", "Interrupt now to prove the current turn stays in control."];
  }
  if (state.status === "interrupted") {
    return ["Turn interrupted", "The previous batch is fenced. Start the corrected request."];
  }
  if (state.status === "stale-dropped") {
    return ["Stale work dropped", "The old result completed late, but never reached the order."];
  }
  if (state.status === "completed") {
    return ["Turn completed", "Only the current address was confirmed."];
  }
  return ["Ready for a request", "The corrected turn will be the only one that reaches the order."];
}

function addEventRow(item: EventRecord): HTMLLIElement {
  const row = document.createElement("li");
  const time = document.createElement("time");
  const name = document.createElement("strong");
  const detail = document.createElement("span");
  const details = [item.reason, item.source, item.purpose].filter(Boolean).join(" / ");

  time.textContent = new Date(item.timestamp).toLocaleTimeString();
  name.textContent = item.event;
  detail.textContent = `epoch ${item.epoch} · ${item.batchId}${details ? ` · ${details}` : ""}`;
  row.append(time, name, detail);
  return row;
}

function render(state: InterfaceState): void {
  const [label, caption] = describeTurn(state);

  connection.textContent = "Connected";
  connection.parentElement?.classList.add("is-connected");
  epoch.textContent = String(state.currentEpoch);
  status.textContent = displayStatus(state.status);
  status.dataset.state = state.status;
  rime.textContent = state.rimeConfigured ? "Rime key detected" : "Rime key missing";
  rime.className = state.rimeConfigured ? "badge badge-ready" : "badge badge-warning";
  batch.textContent = state.activeBatchId ?? "no active batch";
  phase.textContent = displayStatus(state.phase);
  voiceOrb.dataset.state = state.phase;
  turnLabel.textContent = label;
  turnCaption.textContent = caption;
  orderAddress.textContent = state.order.address;
  orderEta.textContent = `${state.order.etaMinutes} minutes`;
  orderStatus.textContent = state.order.status;
  staleCount.textContent = String(state.events.filter((item) => item.event === "stale-dropped").length);
  interruptButton.disabled = !state.activeBatchId;
  requestPreview.textContent = addressInput.value
    ? `Change my delivery address to ${addressInput.value}.`
    : "Change my delivery address…";

  events.replaceChildren(
    ...state.events
      .slice(-6)
      .reverse()
      .map(addEventRow),
  );
}

async function refresh(): Promise<void> {
  try {
    render(await request<InterfaceState>("/api/state"));
  } catch (error) {
    connection.textContent = "Backend offline";
    connection.parentElement?.classList.remove("is-connected");
    console.error(error);
  }
}

addressForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const address = addressInput.value.trim();
  if (!address) return;
  await request("/api/turn", { method: "POST", body: JSON.stringify({ address }) });
  await refresh();
});

interruptButton.addEventListener("click", async () => {
  await request("/api/interrupt", { method: "POST" });
  await refresh();
});

addressInput.addEventListener("input", () => {
  requestPreview.textContent = addressInput.value
    ? `Change my delivery address to ${addressInput.value}.`
    : "Change my delivery address…";
});

void refresh();
window.setInterval(() => void refresh(), 500);
