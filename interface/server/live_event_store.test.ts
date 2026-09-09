import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import assert from "node:assert/strict";
import test from "node:test";
import { LiveEventStore } from "./live_event_store";

test("derives live interface state from Python pipeline events", () => {
  const directory = mkdtempSync(join(tmpdir(), "voicefence-live-"));
  const path = join(directory, "events.jsonl");
  writeFileSync(path, [
    JSON.stringify({ event: "epoch-started", timestamp: 1, epoch: 1, batch_id: "batch-1" }),
    JSON.stringify({ event: "tool-completed", timestamp: 2, epoch: 1, batch_id: "batch-1", order: { address: "42 Wallaby Way, Sydney", eta_minutes: 30, status: "updated" } }),
    JSON.stringify({ event: "tts-started", timestamp: 3, epoch: 1, batch_id: "batch-1" }),
  ].join("\n"));

  const state = new LiveEventStore(path, true).getState();

  assert.ok(state);
  assert.equal(state.source, "live");
  assert.equal(state.currentEpoch, 1);
  assert.equal(state.activeBatchId, "batch-1");
  assert.equal(state.status, "speaking");
  assert.equal(state.order.address, "42 Wallaby Way, Sydney");
  assert.equal(state.rimeConfigured, true);
});