import assert from "node:assert/strict";
import test from "node:test";
import { InterfaceDemoSession } from "./epoch_demo";

test("drops a delayed result after interruption", async () => {
  const session = new InterfaceDemoSession(20, false);

  session.startAddressUpdate("Stale Address");
  session.interrupt();
  session.startAddressUpdate("Correct Address");

  await new Promise((resolve) => setTimeout(resolve, 450));
  const state = session.getState();

  assert.equal(state.order.address, "Correct Address");
  assert.ok(state.events.some((event) => event.event === "stale-dropped"));
  assert.equal(state.status, "completed");
});

test("completes a current turn and emits the TTS lifecycle", async () => {
  const session = new InterfaceDemoSession(10, true);

  session.startAddressUpdate("Current Address");
  await new Promise((resolve) => setTimeout(resolve, 450));
  const state = session.getState();

  assert.equal(state.order.address, "Current Address");
  assert.equal(state.rimeConfigured, true);
  assert.ok(state.events.some((event) => event.event === "tts-started"));
  assert.ok(state.events.some((event) => event.event === "tts-completed"));
});
