export type InterfaceEvent = {
  event: string;
  timestamp: number;
  epoch: number;
  batchId: string;
  reason?: string;
  source?: string;
  purpose?: string;
};

export type InterfaceState = {
  source: "demo";
  livekitConfigured: boolean;
  livekitConnected: boolean;
  currentEpoch: number;
  activeBatchId: string | null;
  status: "idle" | "lookup-pending" | "speaking" | "interrupted" | "completed" | "stale-dropped";
  phase: "idle" | "lookup" | "speaking";
  rimeConfigured: boolean;
  order: { address: string; etaMinutes: number; status: string };
  events: InterfaceEvent[];
};

type Operation = {
  epoch: number;
  batchId: string;
  address: string;
  phase: "lookup" | "speaking";
};

export class InterfaceDemoSession {
  private epoch = 0;
  private batchCounter = 0;
  private epochReservedForNextTurn = false;
  private active: Operation | null = null;
  private status: InterfaceState["status"] = "idle";
  private events: InterfaceEvent[] = [];
  private order = {
    address: "No address confirmed",
    etaMinutes: 0,
    status: "waiting",
  };

  constructor(
    private readonly lookupDelayMs = 3000,
    private readonly rimeConfigured = Boolean(process.env.RIME_API_KEY),
  ) {}

  getState(): InterfaceState {
    return {
      source: "demo",
      livekitConfigured: Boolean(process.env.LIVEKIT_URL && process.env.LIVEKIT_API_KEY && process.env.LIVEKIT_API_SECRET),
      livekitConnected: false,
      currentEpoch: this.epoch,
      activeBatchId: this.active?.batchId ?? null,
      status: this.status,
      phase: this.active?.phase ?? "idle",
      rimeConfigured: this.rimeConfigured,
      order: { ...this.order },
      events: this.events.slice(-60),
    };
  }

  startAddressUpdate(address: string): InterfaceState {
    const trimmed = address.trim();
    if (!trimmed) throw new Error("address is required");

    if (this.active) this.interrupt("superseded");
    if (this.epochReservedForNextTurn) {
      this.epochReservedForNextTurn = false;
    } else {
      this.epoch += 1;
    }

    const operation: Operation = {
      epoch: this.epoch,
      batchId: `batch-${++this.batchCounter}`,
      address: trimmed,
      phase: "lookup",
    };
    this.active = operation;
    this.status = "lookup-pending";
    this.emit("epoch-started", operation);
    this.emit("tool-started", operation, { purpose: "address-lookup" });

    setTimeout(() => this.finishLookup(operation), this.lookupDelayMs);
    return this.getState();
  }

  interrupt(reason = "barge_in"): InterfaceState {
    const previous = this.active;
    this.epoch += 1;
    this.epochReservedForNextTurn = true;
    this.active = null;
    this.status = "interrupted";

    if (previous) {
      this.emit("barge-in", previous, { reason });
      this.emit("epoch-invalidated", previous, { reason });
    }
    return this.getState();
  }

  reset(): InterfaceState {
    this.epoch = 0;
    this.batchCounter = 0;
    this.epochReservedForNextTurn = false;
    this.active = null;
    this.status = "idle";
    this.events = [];
    this.order = {
      address: "No address confirmed",
      etaMinutes: 0,
      status: "waiting",
    };
    return this.getState();
  }

  private finishLookup(operation: Operation): void {
    if (!this.isCurrent(operation)) {
      this.status = "stale-dropped";
      this.emit("stale-dropped", operation, { source: "tool-result" });
      return;
    }

    this.order = {
      address: operation.address,
      etaMinutes: 30,
      status: "updated",
    };
    operation.phase = "speaking";
    this.status = "speaking";
    this.emit("tool-completed", operation);
    this.emit("tts-started", operation, { purpose: "interface-preview" });

    setTimeout(() => {
      if (!this.isCurrent(operation)) {
        this.emit("stale-dropped", operation, { source: "tts-preview" });
        return;
      }
      this.status = "completed";
      this.active = null;
      this.emit("tts-completed", operation, { purpose: "interface-preview" });
    }, 350);
  }

  private isCurrent(operation: Operation): boolean {
    return (
      this.active?.batchId === operation.batchId &&
      this.epoch === operation.epoch
    );
  }

  private emit(
    event: string,
    operation: Pick<Operation, "epoch" | "batchId">,
    fields: Omit<InterfaceEvent, "event" | "timestamp" | "epoch" | "batchId"> = {},
  ): void {
    this.events.push({
      event,
      timestamp: Date.now(),
      epoch: operation.epoch,
      batchId: operation.batchId,
      ...fields,
    });
  }
}
