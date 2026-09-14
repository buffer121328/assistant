import type { LocalEvent } from "./api";

export type TaskEventSocket = {
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onerror: ((event: Event) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  close: () => void;
};

type TimerId = ReturnType<typeof setTimeout>;

export type TaskEventStreamState = "connecting" | "connected" | "recovering" | "fallback";

export type TaskEventSubscriptionOptions = {
  apiBaseUrl: string;
  taskId: string;
  userId: string;
  afterEventId?: string;
  listEvents: (afterEventId?: string) => Promise<LocalEvent[]>;
  onEvents: (events: LocalEvent[]) => void;
  onStateChange?: (state: TaskEventStreamState) => void;
  createSocket?: (url: string) => TaskEventSocket;
  reconnectDelayMs?: number;
  setTimer?: (callback: () => void, delayMs: number) => TimerId;
  clearTimer?: (timerId: TimerId) => void;
};

export type TaskEventSubscription = {
  close: () => void;
};

const TERMINAL_EVENT_TYPES = new Set([
  "task.completed",
  "task.failed",
  "task.cancelled",
  "task.waiting_approval",
  "run.completed",
  "run.failed",
  "approval.required"
]);

/** Build a localhost-only WebSocket URL for one owner-scoped task event ledger. */
export function buildTaskEventStreamUrl(
  apiBaseUrl: string,
  taskId: string,
  userId: string,
  afterEventId?: string
): string {
  const parsed = new URL(apiBaseUrl);
  const localHosts = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
  if (
    (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
    parsed.username ||
    parsed.password ||
    !localHosts.has(parsed.hostname) ||
    (parsed.pathname !== "" && parsed.pathname !== "/")
  ) {
    throw new Error("invalid_api_url");
  }
  parsed.protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
  parsed.pathname = `/local/tasks/${encodeURIComponent(taskId)}/events/stream`;
  parsed.search = "";
  parsed.searchParams.set("user_id", userId);
  if (afterEventId) parsed.searchParams.set("after_event_id", afterEventId);
  return parsed.toString();
}

/** Subscribe to one task's ordered event ledger with REST catch-up after stream interruptions. */
export function subscribeTaskEvents(options: TaskEventSubscriptionOptions): TaskEventSubscription {
  const createSocket = options.createSocket || ((url: string) => new WebSocket(url));
  const setTimer = options.setTimer || ((callback: () => void, delayMs: number) => setTimeout(callback, delayMs));
  const clearTimer = options.clearTimer || ((timerId: TimerId) => clearTimeout(timerId));
  const reconnectDelayMs = options.reconnectDelayMs ?? 1_000;
  const seenEventIds = new Set<string>();
  let afterEventId = options.afterEventId;
  let socket: TaskEventSocket | null = null;
  let reconnectTimer: TimerId | undefined;
  let stopped = false;
  let terminal = false;

  const emit = (events: LocalEvent[]): void => {
    const next = events
      .filter((event) => event.task_id === options.taskId && !seenEventIds.has(event.event_id))
      .sort((left, right) => left.sequence - right.sequence);
    if (!next.length) return;
    for (const event of next) seenEventIds.add(event.event_id);
    afterEventId = next.at(-1)?.event_id || afterEventId;
    if (next.some((event) => isTerminalEvent(event))) terminal = true;
    options.onEvents(next);
  };

  const recover = async (): Promise<void> => {
    options.onStateChange?.("recovering");
    try {
      emit(await options.listEvents(afterEventId));
    } catch {
      options.onStateChange?.("fallback");
    }
  };

  const scheduleReconnect = (): void => {
    if (stopped || terminal || reconnectTimer !== undefined) return;
    reconnectTimer = setTimer(() => {
      reconnectTimer = undefined;
      void connect();
    }, reconnectDelayMs);
  };

  const connect = async (): Promise<void> => {
    if (stopped || terminal) return;
    await recover();
    if (stopped || terminal) return;
    options.onStateChange?.("connecting");
    try {
      socket = createSocket(
        buildTaskEventStreamUrl(options.apiBaseUrl, options.taskId, options.userId, afterEventId)
      );
    } catch {
      options.onStateChange?.("fallback");
      scheduleReconnect();
      return;
    }
    const activeSocket = socket;
    activeSocket.onopen = () => options.onStateChange?.("connected");
    activeSocket.onmessage = (message) => {
      const event = parseTaskEvent(message.data);
      if (event) emit([event]);
    };
    activeSocket.onerror = () => activeSocket.close();
    activeSocket.onclose = () => {
      if (socket === activeSocket) socket = null;
      if (stopped || terminal) return;
      options.onStateChange?.("fallback");
      scheduleReconnect();
    };
  };

  void connect();
  return {
    close: () => {
      stopped = true;
      if (reconnectTimer !== undefined) clearTimer(reconnectTimer);
      reconnectTimer = undefined;
      socket?.close();
      socket = null;
    }
  };
}

function isTerminalEvent(event: LocalEvent): boolean {
  return TERMINAL_EVENT_TYPES.has(event.normalized_type || event.type);
}

function parseTaskEvent(value: string): LocalEvent | null {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const event = parsed as Partial<LocalEvent>;
    if (
      typeof event.event_id !== "string" ||
      typeof event.task_id !== "string" ||
      typeof event.type !== "string" ||
      typeof event.created_at !== "string" ||
      typeof event.sequence !== "number" ||
      !event.payload ||
      typeof event.payload !== "object" ||
      Array.isArray(event.payload)
    ) {
      return null;
    }
    return event as LocalEvent;
  } catch {
    return null;
  }
}
