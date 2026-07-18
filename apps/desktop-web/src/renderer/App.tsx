import { useEffect, useMemo, useState } from "react";
import type { JSX } from "react";
import { Approval, DesktopSettings, LocalApiClient, LocalEvent, Task } from "./api";

type ConnectionState = "checking" | "connected" | "disconnected";

type DerivedItem = {
  id: string;
  title: string;
  detail: string;
  kind: "file" | "diff" | "command" | "approval";
  risk?: string;
};

const DEFAULT_SETTINGS: DesktopSettings = {
  apiBaseUrl: "http://127.0.0.1:8000",
  userId: "",
  defaultWorkdir: "",
  defaultModelClass: "standard",
  approvalPolicy: "ask"
};

export function App(): JSX.Element {
  const [settings, setSettings] = useState<DesktopSettings>(DEFAULT_SETTINGS);
  const [draftSettings, setDraftSettings] = useState<DesktopSettings>(DEFAULT_SETTINGS);
  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [connectionMessage, setConnectionMessage] = useState("Checking local API");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string>("");
  const [eventsByTask, setEventsByTask] = useState<Record<string, LocalEvent[]>>({});
  const [inputText, setInputText] = useState("");
  const [messageText, setMessageText] = useState("");
  const [approvalReason, setApprovalReason] = useState("");
  const [activePanel, setActivePanel] = useState<"logs" | "approvals" | "changes" | "settings">("logs");
  const [error, setError] = useState<string>("");

  const api = useMemo(() => new LocalApiClient(settings), [settings]);
  const selectedTask = tasks.find((task) => task.task_id === selectedTaskId) || null;
  const selectedEvents = selectedTaskId ? eventsByTask[selectedTaskId] || [] : [];
  const approvals = selectedEvents
    .filter((event) => event.type === "task.tool.requested")
    .map(eventToApproval)
    .filter(Boolean) as Approval[];
  const derivedItems = selectedEvents.flatMap(eventToDerivedItems);
  const approvalCount = approvals.length;
  const runningCount = tasks.filter((task) => task.status === "running").length;
  const waitingApprovalCount = tasks.filter((task) => task.status === "waiting_approval").length;
  const finishedCount = tasks.filter((task) => task.status === "success" || task.status === "failed").length;
  const latestEventLabel = selectedEvents.at(-1)?.type || "No events yet";

  useEffect(() => {
    void window.assistantDesktop.loadSettings().then((stored) => {
      const next = { ...DEFAULT_SETTINGS, ...stored };
      setSettings(next);
      setDraftSettings(next);
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    setConnection("checking");
    setConnectionMessage("Checking local API");
    api
      .health()
      .then(() => api.config())
      .then(() => api.listTasks())
      .then((items) => {
        if (cancelled) return;
        setConnection("connected");
        setConnectionMessage("Connected to local agent server");
        setTasks(items);
        if (!selectedTaskId && items[0]) {
          setSelectedTaskId(items[0].task_id);
        }
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setConnection("disconnected");
        setConnectionMessage(reason instanceof Error ? reason.message : "Local API unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, [api, selectedTaskId]);

  useEffect(() => {
    if (!selectedTaskId || !settings.userId) return;
    let socket: WebSocket | null = null;
    let stopped = false;
    const afterEventId = selectedEvents[selectedEvents.length - 1]?.event_id;
    api
      .events(selectedTaskId, afterEventId)
      .then((events) => {
        if (stopped) return;
        appendEvents(selectedTaskId, events);
        socket = api.streamEvents(selectedTaskId, events.at(-1)?.event_id || afterEventId, (event) => {
          appendEvents(selectedTaskId, [event]);
          applyTaskEvent(event);
        });
        socket.addEventListener("error", () => {
          setConnectionMessage("Event stream disconnected; snapshot refresh is still available");
        });
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Unable to load task events");
      });
    return () => {
      stopped = true;
      socket?.close();
    };
  }, [api, selectedTaskId, settings.userId]);

  function appendEvents(taskId: string, events: LocalEvent[]): void {
    if (!events.length) return;
    setEventsByTask((current) => {
      const existing = current[taskId] || [];
      const byId = new Map(existing.map((event) => [event.event_id, event]));
      for (const event of events) byId.set(event.event_id, event);
      return {
        ...current,
        [taskId]: [...byId.values()].sort((left, right) => left.sequence - right.sequence)
      };
    });
  }

  function applyTaskEvent(event: LocalEvent): void {
    if (!["task.failed", "task.completed"].includes(event.type)) return;
    void api.task(event.task_id).then((task) => {
      setTasks((current) => current.map((item) => (item.task_id === task.task_id ? task : item)));
    });
  }

  async function createTask(): Promise<void> {
    if (!inputText.trim()) return;
    try {
      const task = await api.createTask(inputText.trim());
      setTasks((current) => [task, ...current.filter((item) => item.task_id !== task.task_id)]);
      setSelectedTaskId(task.task_id);
      setInputText("");
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Task creation failed");
    }
  }

  async function sendMessage(): Promise<void> {
    if (!selectedTask || !messageText.trim()) return;
    try {
      const task = await api.appendMessage(selectedTask.task_id, messageText.trim());
      setTasks((current) => [task, ...current]);
      setSelectedTaskId(task.task_id);
      setMessageText("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Message send failed");
    }
  }

  async function decide(approval: Approval, decision: "approve" | "reject"): Promise<void> {
    try {
      const result = await api.decideApproval(
        approval.task_id,
        approval.approval_id,
        decision,
        approvalReason
      );
      setTasks((current) =>
        current.map((task) => (task.task_id === result.task.task_id ? result.task : task))
      );
      setApprovalReason("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Approval failed");
    }
  }

  async function saveSettings(): Promise<void> {
    try {
      const validated = await api.validateSettings(draftSettings);
      await window.assistantDesktop.saveSettings(validated);
      setSettings(validated);
      setDraftSettings(validated);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Settings validation failed");
    }
  }

  return (
    <main className="app-shell">
      <aside className="task-list">
        <header className="sidebar-header">
          <div className="app-title-block">
            <span className="eyebrow">Local task console</span>
            <h1>Assistant</h1>
            <p className={`connection ${connection}`}>
              <span className="status-dot" aria-hidden="true" />
              {connectionMessage}
            </p>
          </div>
          <button className="secondary-action" onClick={() => setActivePanel("settings")}>
            Settings
          </button>
        </header>

        <section className="task-overview" aria-label="Task overview">
          <div className="metric-card">
            <span>Tasks</span>
            <strong>{tasks.length}</strong>
          </div>
          <div className="metric-card">
            <span>Running</span>
            <strong>{runningCount}</strong>
          </div>
          <div className="metric-card">
            <span>Approvals</span>
            <strong>{waitingApprovalCount}</strong>
          </div>
          <div className="metric-card">
            <span>Done</span>
            <strong>{finishedCount}</strong>
          </div>
        </section>

        <section className="new-task">
          <textarea
            value={inputText}
            onChange={(event) => setInputText(event.target.value)}
            placeholder="Describe a new task for the local agent"
          />
          <button
            className="primary-action"
            disabled={connection !== "connected" || !settings.userId}
            onClick={() => void createTask()}
          >
            Create
          </button>
        </section>

        <nav className="task-nav" aria-label="Tasks">
          {tasks.length ? (
            tasks.map((task) => (
              <button
                key={task.task_id}
                className={task.task_id === selectedTaskId ? "selected task-row" : "task-row"}
                onClick={() => setSelectedTaskId(task.task_id)}
              >
                <span>{task.input_text}</span>
                <small>{task.task_type}</small>
                <strong className={`status ${task.status}`}>{formatStatus(task.status)}</strong>
              </button>
            ))
          ) : (
            <div className="task-empty">
              <strong>No tasks yet</strong>
              <p>Create a task to start a local agent run.</p>
            </div>
          )}
        </nav>
      </aside>

      <section className="thread-panel">
        {selectedTask ? (
          <>
            <header className="task-header">
              <div>
                <span className="eyebrow">Active thread</span>
                <h2>{selectedTask.input_text}</h2>
                <p className="thread-meta">
                  <span className={`status ${selectedTask.status}`}>{formatStatus(selectedTask.status)}</span>
                  <span>{selectedTask.task_type}</span>
                  <span>{selectedEvents.length} Events</span>
                  <span>{latestEventLabel}</span>
                </p>
              </div>
              <button
                className="secondary-action"
                onClick={() =>
                  void api
                    .task(selectedTask.task_id)
                    .then((task) =>
                      setTasks((items) =>
                        items.map((item) => (item.task_id === task.task_id ? task : item))
                      )
                    )
                }
              >
                Refresh
              </button>
            </header>

            <div className="message-list">
              <article className="message user-message">
                <span>User</span>
                <p>{selectedTask.input_text}</p>
              </article>
              {renderAssistantMessages(selectedEvents, selectedTask)}
            </div>

            <footer className="composer">
              <textarea
                value={messageText}
                onChange={(event) => setMessageText(event.target.value)}
                placeholder="Continue this task"
              />
              <button className="primary-action" onClick={() => void sendMessage()} disabled={!messageText.trim()}>
                Send
              </button>
            </footer>
          </>
        ) : (
          <div className="empty-state">
            <strong>No active thread</strong>
            <p>Select an existing task or create a new one from the queue.</p>
          </div>
        )}
      </section>

      <aside className="inspector">
        <header className="inspector-header">
          <div>
            <span className="eyebrow">Inspector</span>
            <h2>{selectedTask ? "Run details" : "Waiting for a task"}</h2>
          </div>
          <div className="panel-summary">
            <span>{approvalCount} Approvals</span>
            <span>{selectedEvents.length} Events</span>
            <span>{derivedItems.length} Changes</span>
          </div>
        </header>

        <div className="tabs">
          {(["logs", "approvals", "changes", "settings"] as const).map((panel) => (
            <button
              key={panel}
              className={activePanel === panel ? "active" : ""}
              onClick={() => setActivePanel(panel)}
            >
              {panel}
            </button>
          ))}
        </div>

        {error ? <p className="error-banner">{error}</p> : null}

        {activePanel === "logs" ? (
          <section className="logs-panel">
            {selectedEvents.filter((event) => event.type === "task.log.appended" || event.type.includes("message"))
              .length ? (
              selectedEvents
                .filter((event) => event.type === "task.log.appended" || event.type.includes("message"))
                .map((event) => (
                  <article className="log-card" key={event.event_id}>
                    <span>{event.type}</span>
                    <pre className="command-output">{formatPayload(event.payload)}</pre>
                  </article>
                ))
            ) : (
              <div className="inspector-empty">
                <strong>No logs yet</strong>
                <p>Messages and run logs appear here as events arrive.</p>
              </div>
            )}
          </section>
        ) : null}

        {activePanel === "approvals" ? (
          <section className="approval-panel">
            <textarea
              value={approvalReason}
              onChange={(event) => setApprovalReason(event.target.value)}
              placeholder="Optional reason"
            />
            {approvals.length ? (
              approvals.map((approval) => (
                <article key={approval.approval_id} className="approval-card">
                  <header>
                    <strong>{approval.tool_name}</strong>
                    <span className="risk-level">
                      {String((approval as Approval & { risk_level?: string }).risk_level || "high")}
                    </span>
                  </header>
                  <p>{approval.request_summary || approval.subject}</p>
                  <div className="approval-actions">
                    <button className="primary-action" onClick={() => void decide(approval, "approve")}>
                      Approve
                    </button>
                    <button className="secondary-action danger-action" onClick={() => void decide(approval, "reject")}>
                      Reject
                    </button>
                  </div>
                </article>
              ))
            ) : (
              <div className="inspector-empty">
                <strong>No pending approvals</strong>
                <p>Tool requests that need your decision will be listed here.</p>
              </div>
            )}
          </section>
        ) : null}

        {activePanel === "changes" ? (
          <section className="diff-panel">
            {derivedItems.length ? (
              derivedItems.map((item) => (
                <article key={item.id} className={`derived ${item.kind}`}>
                  <header>
                    <strong>{item.title}</strong>
                    {item.risk ? <span className="risk-level">{item.risk}</span> : null}
                  </header>
                  <pre>{item.detail}</pre>
                </article>
              ))
            ) : (
              <div className="inspector-empty">
                <strong>No changes</strong>
                <p>Files, diffs, command results, and approval payloads will appear here.</p>
              </div>
            )}
          </section>
        ) : null}

        {activePanel === "settings" ? (
          <section className="settings-panel">
            <label>
              Local API URL
              <input
                value={draftSettings.apiBaseUrl}
                onChange={(event) => setDraftSettings({ ...draftSettings, apiBaseUrl: event.target.value })}
              />
            </label>
            <label>
              User ID
              <input
                value={draftSettings.userId}
                onChange={(event) => setDraftSettings({ ...draftSettings, userId: event.target.value })}
              />
            </label>
            <label>
              Default workdir
              <input
                value={draftSettings.defaultWorkdir}
                onChange={(event) => setDraftSettings({ ...draftSettings, defaultWorkdir: event.target.value })}
              />
            </label>
            <label>
              Model
              <select
                value={draftSettings.defaultModelClass}
                onChange={(event) =>
                  setDraftSettings({
                    ...draftSettings,
                    defaultModelClass: event.target.value as DesktopSettings["defaultModelClass"]
                  })
                }
              >
                <option value="standard">standard</option>
                <option value="light">light</option>
              </select>
            </label>
            <label>
              Approval policy
              <select
                value={draftSettings.approvalPolicy}
                onChange={(event) =>
                  setDraftSettings({
                    ...draftSettings,
                    approvalPolicy: event.target.value as DesktopSettings["approvalPolicy"]
                  })
                }
              >
                <option value="ask">ask</option>
                <option value="require_high_risk">require high risk</option>
                <option value="read_only">read only</option>
              </select>
            </label>
            <div className="settings-actions">
              <button className="primary-action" onClick={() => void saveSettings()}>
                Validate settings
              </button>
            </div>
          </section>
        ) : null}
      </aside>
    </main>
  );
}

function renderAssistantMessages(events: LocalEvent[], task: Task): JSX.Element[] {
  const messages = events
    .filter((event) =>
      ["task.message.delta", "task.message.completed", "task.failed", "task.completed"].includes(event.type)
    )
    .map((event) => (
      <article className="message assistant-message" key={event.event_id}>
        <span>{event.type}</span>
        <p>{String(event.payload.text || event.payload.message || event.payload.error || "")}</p>
      </article>
    ));
  if (task.result_text) {
    messages.push(
      <article className="message assistant-message" key="result">
        <span>result</span>
        <p>{task.result_text}</p>
      </article>
    );
  }
  if (task.error_message) {
    messages.push(
      <article className="message error-message" key="error">
        <span>error</span>
        <p>{task.error_message}</p>
      </article>
    );
  }
  return messages;
}

function eventToApproval(event: LocalEvent): Approval | null {
  const payload = event.payload;
  const approvalId = String(payload.approval_id || "");
  if (!approvalId) return null;
  return {
    approval_id: approvalId,
    task_id: event.task_id,
    tool_name: String(payload.tool_name || payload.subject || "tool"),
    approval_type: "tool",
    subject: String(payload.subject || payload.tool_name || "tool"),
    request_summary: String(payload.summary || payload.request_summary || ""),
    status: "pending",
    decided_by_user_id: null,
    decided_at: null,
    created_at: event.created_at,
    updated_at: event.created_at
  };
}

function eventToDerivedItems(event: LocalEvent): DerivedItem[] {
  const payload = event.payload;
  const items: DerivedItem[] = [];
  if (Array.isArray(payload.files)) {
    for (const [index, file] of payload.files.entries()) {
      items.push({
        id: `${event.event_id}-file-${index}`,
        kind: "file",
        title: String((file as Record<string, unknown>).path || "file"),
        detail: formatPayload(file as Record<string, unknown>)
      });
    }
  }
  if (payload.diff) {
    items.push({
      id: `${event.event_id}-diff`,
      kind: "diff",
      title: String(payload.path || "diff"),
      detail: String(payload.diff)
    });
  }
  if (payload.stdout || payload.stderr || payload.exit_code !== undefined) {
    items.push({
      id: `${event.event_id}-command`,
      kind: "command",
      title: String(payload.command || "command"),
      detail: formatPayload({
        stdout: payload.stdout,
        stderr: payload.stderr,
        exit_code: payload.exit_code,
        timed_out: payload.timed_out
      })
    });
  }
  if (event.type === "task.tool.requested") {
    items.push({
      id: `${event.event_id}-approval`,
      kind: "approval",
      title: String(payload.tool_name || "approval"),
      detail: formatPayload(payload),
      risk: String(payload.risk_level || "high")
    });
  }
  return items;
}

function formatPayload(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function formatStatus(status: Task["status"]): string {
  return status.replace(/_/g, " ");
}
