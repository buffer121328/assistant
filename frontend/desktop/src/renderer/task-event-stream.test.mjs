import test from "node:test";
import assert from "node:assert/strict";

import {
  buildTaskEventStreamUrl,
  subscribeTaskEvents
} from "./task-event-stream.ts";

const event = (id, sequence, type = "task.phase.started") => ({
  event_id: id,
  task_id: "task-1",
  type,
  created_at: "2026-08-17T12:00:00Z",
  sequence,
  payload: {}
});

class FakeSocket {
  constructor(url) {
    this.url = url;
    this.closed = false;
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this.onclose = null;
  }

  close() {
    this.closed = true;
  }

  emitOpen() {
    this.onopen?.({});
  }

  emitMessage(value) {
    this.onmessage?.({ data: JSON.stringify(value) });
  }

  emitClose() {
    this.onclose?.({});
  }
}

const flush = async () => new Promise((resolve) => setImmediate(resolve));

test("buildTaskEventStreamUrl converts a local HTTP API URL and preserves the event cursor", () => {
  assert.equal(
    buildTaskEventStreamUrl("http://127.0.0.1:8000", "task/1", "user 1", "event/1"),
    "ws://127.0.0.1:8000/local/tasks/task%2F1/events/stream?user_id=user+1&after_event_id=event%2F1"
  );
  assert.equal(
    buildTaskEventStreamUrl("https://localhost:8443", "task-1", "user-1"),
    "wss://localhost:8443/local/tasks/task-1/events/stream?user_id=user-1"
  );
});

test("subscribeTaskEvents delivers stream events, catches up after a close, and cleans up", async () => {
  const sockets = [];
  const received = [];
  const eventReads = [];
  let readCount = 0;

  const subscription = subscribeTaskEvents({
    apiBaseUrl: "http://127.0.0.1:8000",
    taskId: "task-1",
    userId: "user-1",
    afterEventId: "already-rendered",
    listEvents: async (afterEventId) => {
      eventReads.push(afterEventId);
      readCount += 1;
      return readCount === 2 ? [event("catch-up", 3)] : [];
    },
    onEvents: (items) => received.push(...items),
    createSocket: (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket;
    },
    setTimer: (callback) => {
      queueMicrotask(callback);
      return 1;
    },
    clearTimer: () => {}
  });

  await flush();
  assert.equal(sockets.length, 1);
  assert.match(sockets[0].url, /after_event_id=already-rendered/);

  sockets[0].emitOpen();
  sockets[0].emitMessage(event("streamed", 2));
  await flush();
  assert.deepEqual(received.map((item) => item.event_id), ["streamed"]);

  sockets[0].emitClose();
  await flush();
  await flush();
  assert.deepEqual(eventReads, ["already-rendered", "streamed"]);
  assert.deepEqual(received.map((item) => item.event_id), ["streamed", "catch-up"]);
  assert.equal(sockets.length, 2);
  assert.match(sockets[1].url, /after_event_id=catch-up/);

  subscription.close();
  assert.equal(sockets[1].closed, true);
});
