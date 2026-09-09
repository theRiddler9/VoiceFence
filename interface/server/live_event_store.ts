import { readFileSync, statSync } from "node:fs";

export type LiveInterfaceEvent = {
  event: string;
  timestamp: number;
  epoch?: number;
  batch_id?: string;
  reason?: string;
  source?: string;
  purpose?: string;
  order?: { address: string; eta_minutes?: number; status?: string };
  address?: string;
  livekit_connected?: boolean;
  transcript?: string;
  is_final?: boolean;
  turn_id?: string;
};

export type LiveInterfaceState = {
  source: "live";
  livekitConfigured: boolean;
  livekitConnected: boolean;
  currentEpoch: number;
  activeBatchId: string | null;
  status: "idle" | "lookup-pending" | "speaking" | "interrupted" | "completed" | "stale-dropped";
  phase: "idle" | "lookup" | "speaking";
  rimeConfigured: boolean;
  order: { address: string; etaMinutes: number; status: string };
  latestTranscript: string | null;
  events: LiveInterfaceEvent[];
};

export class LiveEventStore {
  constructor(
    private readonly path: string,
    private readonly rimeConfigured: boolean,
    private readonly livekitConfigured = false,
  ) {}

  getState(): LiveInterfaceState | null {
    const events = this.readEvents();
    if (events.length === 0) return null;

    let currentEpoch = 0;
    let activeBatchId: string | null = null;
    let status: LiveInterfaceState["status"] = "idle";
    let phase: LiveInterfaceState["phase"] = "idle";
    let livekitConnected = false;
    let latestTranscript: string | null = null;
    let order = {
      address: "No address confirmed",
      etaMinutes: 0,
      status: "waiting",
    };

    for (const event of events) {
      currentEpoch = Math.max(currentEpoch, event.epoch ?? 0);
      if (event.event === "transcript") {
        latestTranscript = event.transcript ?? null;
      } else if (event.event === "runtime-status") {
        livekitConnected = event.livekit_connected === true;
      } else if (event.event === "epoch-started") {
        activeBatchId = event.batch_id ?? null;
        status = "lookup-pending";
        phase = "lookup";
      } else if (event.event === "address-intent") {
        status = "lookup-pending";
        phase = "lookup";
      } else if (event.event === "barge-in-detected" || event.event === "epoch-invalidated") {
        status = "interrupted";
        phase = "idle";
        activeBatchId = null;
      } else if (event.event === "stale-dropped") {
        status = "stale-dropped";
        phase = "idle";
      } else if (event.event === "tool-completed") {
        status = "speaking";
        phase = "speaking";
        if (event.order) {
          order = {
            address: event.order.address,
            etaMinutes: event.order.eta_minutes ?? 0,
            status: event.order.status ?? "updated",
          };
        }
      } else if (event.event === "tts-started") {
        status = "speaking";
        phase = "speaking";
      }
    }

    return {
      source: "live",
      livekitConfigured: this.livekitConfigured,
      livekitConnected,
      currentEpoch,
      activeBatchId,
      status,
      phase,
      rimeConfigured: this.rimeConfigured,
      order,
      latestTranscript,
      events: events.slice(-60),
    };
  }

  private readEvents(): LiveInterfaceEvent[] {
    try {
      statSync(this.path);
      return readFileSync(this.path, "utf8")
        .split(/\r?\n/)
        .filter(Boolean)
        .slice(-200)
        .map((line) => JSON.parse(line) as LiveInterfaceEvent);
    } catch {
      return [];
    }
  }
}