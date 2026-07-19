import { useEffect, useMemo, useState } from "react";
import type { JSX } from "react";
import {
  Approval,
  ConversationTokenStats,
  DesktopSettings,
  LocalApiClient,
  LocalEvent,
  LocalTaskType,
  RemoteControlBridgeSession,
  Task,
  TaskStatus
} from "./api";

type ConnectionState = "checking" | "connected" | "disconnected";

type TaskStatusFilter = "all" | TaskStatus;
type BridgeDeliveryFilter = "all" | "pending" | "succeeded" | "retry" | "failed" | "unknown";

type DerivedItem = {
  id: string;
  title: string;
  detail: string;
  kind: "file" | "diff" | "command" | "approval";
  risk?: string;
};


const TASK_TYPE_OPTIONS: { value: LocalTaskType; label: string; detail: string }[] = [
  { value: "plan", label: "Plan", detail: "计划拆解和执行路线" },
  { value: "learn", label: "Learn", detail: "搜索、学习和知识理解" },
  { value: "daily", label: "Daily", detail: "提醒、状态和日常助理" },
  { value: "office", label: "Office", detail: "办公、文件和工具动作" }
];

const TASK_STATUS_FILTERS: { value: TaskStatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "running", label: "Running" },
  { value: "waiting_approval", label: "Approval" },
  { value: "success", label: "Success" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" }
];

const BRIDGE_DELIVERY_FILTERS: { value: BridgeDeliveryFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "succeeded", label: "Delivered" },
  { value: "retry", label: "Retry" },
  { value: "failed", label: "Failed" },
  { value: "unknown", label: "Unknown" }
];

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
  const [selectedTaskType, setSelectedTaskType] = useState<LocalTaskType>("plan");
  const [taskSearchText, setTaskSearchText] = useState("");
  const [taskStatusFilter, setTaskStatusFilter] = useState<TaskStatusFilter>("all");
  const [messageText, setMessageText] = useState("");
  const [approvalReason, setApprovalReason] = useState("");
  const [bridgeSessions, setBridgeSessions] = useState<RemoteControlBridgeSession[]>([]);
  const [bridgeSearchText, setBridgeSearchText] = useState("");
  const [bridgeDeliveryFilter, setBridgeDeliveryFilter] = useState<BridgeDeliveryFilter>("all");
  const [selectedBridgeMessageId, setSelectedBridgeMessageId] = useState<string>("");
  const [selectedBridgeSession, setSelectedBridgeSession] = useState<RemoteControlBridgeSession | null>(null);
  const [tokenStats, setTokenStats] = useState<ConversationTokenStats | null>(null);
  const [activePanel, setActivePanel] = useState<"timeline" | "logs" | "approvals" | "bridge" | "changes" | "settings">("timeline");
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
  const normalizedTaskSearch = taskSearchText.trim().toLowerCase();
  const filteredTasks = tasks.filter((task) => {
    const matchesStatus = taskStatusFilter === "all" || task.status === taskStatusFilter;
    if (!matchesStatus) return false;
    if (!normalizedTaskSearch) return true;
    const searchable = [
      task.input_text,
      task.task_type,
      task.status,
      formatStatus(task.status),
      task.workflow_key || "",
      task.model_class || ""
    ]
      .join(" ")
      .toLowerCase();
    return searchable.includes(normalizedTaskSearch);
  });
  const normalizedBridgeSearch = bridgeSearchText.trim().toLowerCase();
  const filteredBridgeSessions = bridgeSessions.filter((session) => {
    const status = knownBridgeDeliveryStatus(session.delivery_status);
    const matchesStatus = bridgeDeliveryFilter === "all" || status === bridgeDeliveryFilter;
    if (!matchesStatus) return false;
    if (!normalizedBridgeSearch) return true;
    const searchable = [
      session.message_id,
      session.message_text || "",
      session.adapter || "",
      session.sender_id || "",
      session.conversation_id || "",
      session.conversation_type || "",
      session.intent_outcome || "",
      session.reason,
      session.task_id || "",
      session.task_status || "",
      session.delivery_status || "unknown"
    ]
      .join(" ")
      .toLowerCase();
    return searchable.includes(normalizedBridgeSearch);
  });
  const bridgePendingCount = bridgeSessions.filter((session) => session.delivery_status === "pending").length;
  const bridgeSucceededCount = bridgeSessions.filter((session) => session.delivery_status === "succeeded").length;
  const bridgeRetryCount = bridgeSessions.filter((session) => session.delivery_status === "retry").length;

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
    let cancelled = false;
    api
      .bridgeSessions(20)
      .then((items) => {
        if (cancelled) return;
        setBridgeSessions(items);
        if (!selectedBridgeMessageId && items[0]) {
          setSelectedBridgeMessageId(items[0].message_id);
        }
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "Unable to load bridge sessions");
      });
    return () => {
      cancelled = true;
    };
  }, [api, selectedBridgeMessageId]);

  useEffect(() => {
    if (!selectedBridgeMessageId) {
      setSelectedBridgeSession(null);
      return;
    }
    let cancelled = false;
    api
      .bridgeSession(selectedBridgeMessageId)
      .then((session) => {
        if (!cancelled) {
          setSelectedBridgeSession(session);
        }
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "Unable to load bridge session");
      });
    return () => {
      cancelled = true;
    };
  }, [api, selectedBridgeMessageId]);

  useEffect(() => {
    if (!selectedTask?.conversation_id || !settings.userId) {
      setTokenStats(null);
      return;
    }
    let cancelled = false;
    api
      .conversationTokenStats(selectedTask.conversation_id)
      .then((stats) => {
        if (!cancelled) setTokenStats(stats);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setTokenStats(null);
        setError(reason instanceof Error ? reason.message : "Unable to load session token stats");
      });
    return () => {
      cancelled = true;
    };
  }, [api, selectedTask?.conversation_id, selectedTaskId, settings.userId]);

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
      const task = await api.createTask(inputText.trim(), selectedTaskType);
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
          <div className="new-task-controls">
            <label className="task-type-select">
              Task type
              <select
                value={selectedTaskType}
                onChange={(event) => setSelectedTaskType(event.target.value as LocalTaskType)}
              >
                {TASK_TYPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="primary-action"
              disabled={connection !== "connected" || !settings.userId}
              onClick={() => void createTask()}
            >
              Create
            </button>
          </div>
          <p className="task-type-hint">
            {TASK_TYPE_OPTIONS.find((option) => option.value === selectedTaskType)?.detail}
          </p>
        </section>

        <section className="task-filters" aria-label="Task filters">
          <label className="task-search">
            Search tasks
            <input
              value={taskSearchText}
              onChange={(event) => setTaskSearchText(event.target.value)}
              placeholder="Search text, type, status"
            />
          </label>
          <label className="task-status-filter">
            Status
            <select
              value={taskStatusFilter}
              onChange={(event) => setTaskStatusFilter(event.target.value as TaskStatusFilter)}
            >
              {TASK_STATUS_FILTERS.map((filter) => (
                <option key={filter.value} value={filter.value}>
                  {filter.label}
                </option>
              ))}
            </select>
          </label>
        </section>

        <nav className="task-nav" aria-label="Tasks">
          {tasks.length ? (
            filteredTasks.length ? (
              filteredTasks.map((task) => (
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
              <div className="task-filter-empty">
                <strong>No matching tasks</strong>
                <p>Clear search or switch status to show more loaded tasks.</p>
              </div>
            )
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
                  {tokenStats ? (
                    <span className={`token-state ${tokenStats.status}`}>
                      Session tokens {tokenStats.total_estimated_tokens}/{tokenStats.token_limit}
                    </span>
                  ) : null}
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
            <h2>
              {activePanel === "bridge"
                ? "Remote bridge"
                : selectedTask
                  ? "Run details"
                  : "Waiting for a task"}
            </h2>
          </div>
          <div className="panel-summary">
            {activePanel === "bridge" ? (
              <>
                <span>{bridgeSessions.length} Sessions</span>
                <span>{bridgePendingCount} Pending</span>
                <span>{bridgeSucceededCount} Delivered</span>
                <span>{bridgeRetryCount} Retry</span>
              </>
            ) : (
              <>
                <span>{approvalCount} Approvals</span>
                <span>{selectedEvents.length} Events</span>
                <span>{derivedItems.length} Changes</span>
              </>
            )}
          </div>
        </header>

        <div className="tabs">
          {(["timeline", "logs", "approvals", "bridge", "changes", "settings"] as const).map((panel) => (
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


        {activePanel === "timeline" ? renderTimelinePanel(selectedEvents, tokenStats, selectedTask) : null}

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

        {activePanel === "bridge" ? (
          <section className="bridge-panel">
            <section className="bridge-filters" aria-label="Bridge filters">
              <label className="bridge-search">
                Search bridge
                <input
                  value={bridgeSearchText}
                  onChange={(event) => setBridgeSearchText(event.target.value)}
                  placeholder="Search message, sender, conversation"
                />
              </label>
              <label className="bridge-delivery-filter">
                Delivery
                <select
                  value={bridgeDeliveryFilter}
                  onChange={(event) => setBridgeDeliveryFilter(event.target.value as BridgeDeliveryFilter)}
                >
                  {BRIDGE_DELIVERY_FILTERS.map((filter) => (
                    <option key={filter.value} value={filter.value}>
                      {filter.label}
                    </option>
                  ))}
                </select>
              </label>
            </section>

            <div className="bridge-list">
              {bridgeSessions.length ? (
                filteredBridgeSessions.length ? (
                  filteredBridgeSessions.slice(0, 8).map((session) => (
                    <button
                      key={session.bridge_id}
                      className={
                        session.message_id === selectedBridgeMessageId ? "bridge-item selected" : "bridge-item"
                      }
                      onClick={() => setSelectedBridgeMessageId(session.message_id)}
                    >
                      <span className="bridge-item-title">
                        <strong>{session.message_id}</strong>
                        <span className={`status ${bridgeStatusClass(session.delivery_status)}`}>
                          {formatBridgeDeliveryStatus(session.delivery_status)}
                        </span>
                      </span>
                      <small>{bridgeSessionSubtitle(session)}</small>
                      <p>{session.message_text || "No message body"}</p>
                    </button>
                  ))
                ) : (
                  <div className="bridge-filter-empty">
                    <strong>No matching bridge sessions</strong>
                    <p>Clear search or switch delivery status to show more loaded sessions.</p>
                  </div>
                )
              ) : (
                <div className="inspector-empty">
                  <strong>No bridge sessions</strong>
                  <p>Remote-control messages will appear here once LangBot traffic reaches the backend.</p>
                </div>
              )}
            </div>

            {selectedBridgeSession ? (
              <article className="bridge-detail">
                <header>
                  <div>
                    <strong>{selectedBridgeSession.message_id}</strong>
                    <p className="bridge-meta-line">
                      <span>{selectedBridgeSession.intent_outcome || selectedBridgeSession.reason}</span>
                      <span>{selectedBridgeSession.conversation_id || "no conversation"}</span>
                    </p>
                  </div>
                  <button
                    className="secondary-action"
                    onClick={() =>
                      void api
                        .bridgeSession(selectedBridgeSession.message_id)
                        .then((session) => {
                          setSelectedBridgeSession(session);
                        })
                        .catch((reason: unknown) => {
                          setError(reason instanceof Error ? reason.message : "Unable to refresh bridge session");
                        })
                    }
                  >
                    Refresh
                  </button>
                </header>
                <p className="bridge-message">{selectedBridgeSession.message_text || "No message body"}</p>
                <div className="bridge-meta-grid">
                  <span>Adapter: {selectedBridgeSession.adapter || "-"}</span>
                  <span>Sender: {selectedBridgeSession.sender_id || "-"}</span>
                  <span>Conversation: {selectedBridgeSession.conversation_type || "-"}</span>
                  <span>Task: {selectedBridgeSession.task_status || selectedBridgeSession.task_id || "-"}</span>
                  <span>Deliveries: {selectedBridgeSession.delivery_attempt_count}</span>
                  <span>Status: {selectedBridgeSession.delivery_status || "-"}</span>
                </div>
                {selectedBridgeSession.response_target ? (
                  <pre className="command-output">{formatPayload(selectedBridgeSession.response_target)}</pre>
                ) : null}
                {selectedBridgeSession.delivery_error_summary ? (
                  <p className="bridge-error">{selectedBridgeSession.delivery_error_summary}</p>
                ) : null}
                <div className="bridge-actions">
                  {selectedBridgeSession.task_id && selectedBridgeSession.delivery_status !== "succeeded" ? (
                    <button
                      className="secondary-action"
                      onClick={() =>
                        void api
                          .replayBridgeSession(selectedBridgeSession.message_id)
                          .then((result) => {
                            setSelectedBridgeSession(result.session);
                            setBridgeSessions((current) =>
                              current.map((item) =>
                                item.message_id === result.session.message_id ? result.session : item
                              )
                            );
                            setError(result.dispatch_status === "succeeded" ? "" : result.message);
                          })
                          .catch((reason: unknown) => {
                            setError(reason instanceof Error ? reason.message : "Unable to replay bridge session");
                          })
                      }
                    >
                      Replay delivery
                    </button>
                  ) : null}
                  {selectedBridgeSession.task_id ? (
                    <button
                      className="primary-action"
                      onClick={() => {
                        setSelectedTaskId(selectedBridgeSession.task_id || "");
                        setActivePanel("logs");
                      }}
                    >
                      Open task
                    </button>
                  ) : null}
                </div>
              </article>
            ) : (
              <div className="inspector-empty">
                <strong>No bridge session selected</strong>
                <p>Select a remote-control message to inspect its task and delivery state.</p>
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


function renderTimelinePanel(
  events: LocalEvent[],
  tokenStats: ConversationTokenStats | null,
  task: Task | null
): JSX.Element {
  return (
    <section className="timeline-panel">
      {renderTokenUsageCard(tokenStats, task)}
      <div className="timeline-events" aria-label="Agent event timeline">
        {events.length ? (
          events.map((event) => (
            <article className="timeline-event" key={event.event_id}>
              <header>
                <span className="event-sequence">#{event.sequence}</span>
                <strong>{eventActionLabel(event)}</strong>
                <span>{formatEventTime(event.created_at)}</span>
              </header>
              <p className="timeline-type">{event.type}</p>
              <pre className="command-output">{formatPayload(event.payload)}</pre>
            </article>
          ))
        ) : (
          <div className="timeline-empty">
            <strong>No timeline events yet</strong>
            <p>Agent state changes and actions appear here after the backend publishes task events.</p>
          </div>
        )}
      </div>
    </section>
  );
}

function renderTokenUsageCard(stats: ConversationTokenStats | null, task: Task | null): JSX.Element {
  if (!task?.conversation_id) {
    return (
      <article className="token-card">
        <header>
          <strong>Token usage</strong>
          <span>no conversation</span>
        </header>
        <p>This task is not linked to a conversation yet.</p>
      </article>
    );
  }
  if (!stats) {
    return (
      <article className="token-card">
        <header>
          <strong>Token usage</strong>
          <span>loading</span>
        </header>
        <p>Token stats will appear when the local API returns conversation usage.</p>
      </article>
    );
  }
  const percent = Math.min(100, Math.max(0, Math.round(stats.usage_ratio * 100)));
  return (
    <article className={`token-card ${stats.status}`}>
      <header>
        <strong>Token usage</strong>
        <span>{stats.status}</span>
      </header>
      <div className="token-meter" aria-label="Token usage meter">
        <span style={{ width: `${percent}%` }} />
      </div>
      <div className="token-grid">
        <span>Total {stats.total_estimated_tokens}/{stats.token_limit}</span>
        <span>Usage {percent}%</span>
        <span>Messages {stats.message_count}</span>
        <span>User {stats.user_message_count}</span>
        <span>Assistant {stats.assistant_message_count}</span>
      </div>
    </article>
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


function eventActionLabel(event: LocalEvent): string {
  const payload = event.payload;
  switch (event.type) {
    case "task.started":
      return "Agent started";
    case "task.completed":
      return "Agent completed";
    case "task.failed":
      return "Agent failed";
    case "task.message.delta":
      return "Assistant streaming";
    case "task.message.completed":
      return "Assistant message";
    case "task.log.appended":
      return String(payload.message || payload.text || "Log appended");
    case "task.tool.requested":
      return `Tool approval requested: ${String(payload.tool_name || payload.subject || "tool")}`;
    default:
      if (payload.tool_name) return `Tool action: ${String(payload.tool_name)}`;
      if (payload.command) return `Command: ${String(payload.command)}`;
      if (payload.path) return `File: ${String(payload.path)}`;
      return event.type.replace(/\./g, " ");
  }
}

function formatEventTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function formatPayload(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function formatStatus(status: Task["status"]): string {
  return status.replace(/_/g, " ");
}

function knownBridgeDeliveryStatus(status: string | null): BridgeDeliveryFilter {
  switch (status) {
    case "pending":
    case "succeeded":
    case "retry":
    case "failed":
      return status;
    default:
      return "unknown";
  }
}

function formatBridgeDeliveryStatus(status: string | null): string {
  if (!status) return "unknown";
  return status.replace(/_/g, " ");
}

function bridgeStatusClass(status: string | null): string {
  switch (status) {
    case "succeeded":
      return "success";
    case "pending":
      return "running";
    case "retry":
      return "waiting_approval";
    case "failed":
      return "failed";
    default:
      return "";
  }
}

function bridgeSessionSubtitle(session: {
  adapter: string | null;
  conversation_id: string | null;
  sender_id: string | null;
  intent_outcome: string | null;
  reason: string;
}): string {
  return [
    session.adapter || "unknown adapter",
    session.conversation_id || "no conversation",
    session.sender_id || "no sender",
    session.intent_outcome || session.reason
  ]
    .filter(Boolean)
    .join(" · ");
}
