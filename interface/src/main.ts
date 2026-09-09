import "./tailwind.css";

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
  source: "demo" | "live";
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
const microphoneToggle = $("microphone-toggle") as HTMLButtonElement;
const microphoneStatus = $("microphone");
const addressHint = $("address-hint");
const visualizer = $("voice-visualizer") as HTMLCanvasElement;
let microphoneStream: MediaStream | null = null;
let audioContext: AudioContext | null = null;
let animationFrame = 0;

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
  if (state.status === "lookup-pending") return "I am checking that request...";
  if (state.status === "speaking") return "The current address was confirmed. Rime is speaking the result.";
  if (state.status === "interrupted") return "I stopped the previous request.";
  if (state.status === "stale-dropped") return "The old result arrived late and was dropped.";
  if (state.status === "completed") return "The current request was confirmed.";
  return "Ready when you are.";
}

function addEventRow(item: EventRecord): HTMLLIElement {
  const row = document.createElement("li");
  const time = document.createElement("time");
  const name = document.createElement("strong");
  const detail = document.createElement("span");
  const extras = [item.reason, item.source, item.purpose].filter(Boolean).join(" / ");

  row.className = "grid grid-cols-[95px_185px_1fr] items-center gap-3 border-t border-sky-100/15 py-3 text-xs max-sm:grid-cols-1 max-sm:gap-1";
  time.className = "text-[#7891ab]";
  name.className = "font-bold text-sky-200";
  detail.className = "text-[#7891ab]";
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
  connection.textContent = state.source === "live" ? "Live backend connected" : "Demo backend";
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
  voiceOrb.classList.toggle("animate-pulse", state.phase === "speaking");
  assistantResponse.textContent = getAssistantResponse(state);
  interruptionNote.hidden = state.status !== "interrupted" && state.status !== "stale-dropped";
  interruptionNote.textContent = recentInterruption
    ? `User interrupted · Epoch ${recentInterruption.epoch} cancelled`
    : "";
  orderAddress.textContent = state.order.address;
  orderEta.textContent = `${state.order.etaMinutes} minutes`;
  orderStatus.textContent = state.order.status;
  interruptButton.disabled = state.source === "live" || !state.activeBatchId;
  addressInput.disabled = state.source === "live";
  requestPreview.textContent = addressInput.value || "Your words will appear here.";
  addressHint.hidden = Boolean(addressInput.value);

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
  requestPreview.textContent = addressInput.value || "Your words will appear here.";
  addressHint.hidden = Boolean(addressInput.value);
});

function drawVisualizer(analyser: AnalyserNode): void {
  const context = visualizer.getContext("2d");
  if (!context) return;
  const values = new Uint8Array(analyser.fftSize);
  const ratio = window.devicePixelRatio || 1;
  const width = visualizer.clientWidth * ratio;
  const height = visualizer.clientHeight * ratio;
  if (visualizer.width !== width || visualizer.height !== height) {
    visualizer.width = width;
    visualizer.height = height;
  }
  analyser.getByteTimeDomainData(values);
  context.clearRect(0, 0, width, height);
  context.strokeStyle = "rgba(125, 211, 252, 0.95)";
  context.lineWidth = 2 * ratio;
  context.beginPath();
  values.forEach((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = ((value / 255) * height * 0.72) + height * 0.14;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
  animationFrame = requestAnimationFrame(() => drawVisualizer(analyser));
}

async function toggleMicrophone(): Promise<void> {
  if (microphoneStream) {
    microphoneStream.getTracks().forEach((track) => track.stop());
    microphoneStream = null;
    if (audioContext) await audioContext.close();
    audioContext = null;
    cancelAnimationFrame(animationFrame);
    microphoneStatus.textContent = "Off";
    microphoneToggle.textContent = "Start microphone";
    return;
  }

  try {
    microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioContext = new AudioContext();
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 512;
    audioContext.createMediaStreamSource(microphoneStream).connect(analyser);
    microphoneStatus.textContent = "Listening";
    microphoneToggle.textContent = "Stop microphone";
    drawVisualizer(analyser);
  } catch (error) {
    microphoneStatus.textContent = "Permission needed";
    console.error(error);
  }
}

microphoneToggle.addEventListener("click", () => void toggleMicrophone());

void refresh();
window.setInterval(() => void refresh(), 500);
