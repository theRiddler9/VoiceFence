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
const audioState = $("audio-state");
const voiceOrb = $("voice-orb");
const assistantResponse = $("assistant-response");
const interruptionNote = $("interruption-note");
const requestPreview = $("request-preview");
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

function displayStatus(value: string): string {
  return value.replaceAll("-", " ");
}

function getAudioState(state: InterfaceState): string {
  if (state.status === "interrupted") return "Interrupted";
  if (state.status === "completed") return "Completed";
  if (state.phase === "lookup") return "Processing";
  if (state.phase === "speaking") return "Speaking";
  return "Listening";
}

function getAssistantResponse(state: InterfaceState): string {
  if (state.status === "lookup-pending") return "I’m checking that address...";
  if (state.status === "speaking") return "The current address was confirmed. Rime is speaking the result.";
  if (state.status === "interrupted") return "I stopped the previous request.";
  if (state.status === "stale-dropped") return "The old result arrived late and was dropped.";
  if (state.status === "completed") return "The current address was confirmed.";
  return "Ready to check your address.";
}

function addEventRow(item: EventRecord): HTMLLIElement {
  const row = document.createElement("li");
  const time = document.createElement("time");
  const name = document.createElement("strong");
  const detail = document.createElement("span");
  const extras = [item.reason, item.source, item.purpose].filter(Boolean).join(" / ");

  time.textContent = new Date(item.timestamp).toLocaleTimeString();
  name.textContent = item.event;
  detail.textContent = `${item.batchId || "no batch"} · epoch ${item.epoch}${extras ? ` · ${extras}` : ""}`;
  row.append(time, name, detail);
  return row;
}

function render(state: InterfaceState): void {
  const currentAudioState = getAudioState(state);
  const recentInterruption = [...state.events].reverse().find((item) => item.event === "barge-in");

  connection.textContent = "API connected";
  epoch.textContent = String(state.currentEpoch);
  batch.textContent = state.activeBatchId ?? "No active batch";
  rime.textContent = state.rimeConfigured ? "Configured" : "Key missing";
  rime.classList.toggle("is-ready", state.rimeConfigured);
  status.textContent = displayStatus(state.status);
  status.dataset.state = state.status;
  phase.textContent = currentAudioState;
  audioState.textContent = currentAudioState;
  audioState.dataset.state = state.status;
  voiceOrb.dataset.state = state.phase;
  assistantResponse.textContent = getAssistantResponse(state);
  interruptionNote.hidden = state.status !== "interrupted" && state.status !== "stale-dropped";
  interruptionNote.textContent = recentInterruption
    ? `User interrupted · Epoch ${recentInterruption.epoch} cancelled`
    : "";
  orderAddress.textContent = state.order.address;
  orderEta.textContent = `${state.order.etaMinutes} minutes`;
  orderStatus.textContent = state.order.status;
  interruptButton.disabled = !state.activeBatchId;
  requestPreview.textContent = addressInput.value
    ? `Change my delivery address to ${addressInput.value}.`
    : "Enter an address to update the delivery order.";

  events.replaceChildren(...state.events.slice(-10).reverse().map(addEventRow));
}

async function refresh(): Promise<void> {
  try {
    render(await request<InterfaceState>("/api/state"));
  } catch (error) {
    connection.textContent = "API offline";
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
    : "Enter an address to update the delivery order.";
});

void refresh();
window.setInterval(() => void refresh(), 500);
