import { useEffect, useMemo, useState } from "react";
import type { DragEvent as ReactDragEvent, JSX } from "react";
import {
  Approval,
  ConversationMessage,
  ConversationTokenStats,
  ConversationWorkSummary,
  AuthenticatedSession,
  DesktopSettings,
  LocalApiClient,
  LocalEvent,
  LocalTaskType,
  MyCapabilities,
  OfficeCommand,
  RemoteControlBridgeSession,
  ResourceReference,
  Space,
  Task,
  TaskStatus,
  WorkspaceFile
} from "./api";
import { removeResourceToken } from "./composer";
import { buildConversationGroups, filterConversationGroups } from "./conversation-list";
import { buildConversationThread, hasAssistantTurn } from "./conversation-thread";
import { conversationUsagePresentation, formatTokenCount } from "./conversation-usage";
import {
  planProgressCopy,
  taskFailurePresentation,
  taskProgressPresentation
} from "./task-progress";
import {
  isAssistantMessageEvent,
  normalizeConversationMessage,
  notificationLabel,
  taskResultKind
} from "./conversation-presentation";
import brandMark from "./assets/jarvis-mark.svg";
import { Icon } from "./icons";
import { authErrorMessage, departmentLabel, identityResponsibilityLabel, roleLabel } from "./auth-presentation";
import { RichExecutionStream } from "./execution-stream";
import { AssistantMarkdown } from "./assistant-markdown-renderer";
import { subscribeTaskEvents } from "./task-event-stream";
import {
  activeSlashQuery,
  filterSlashCommands,
  nextSlashIndex
} from "./slash-palette";
import { ArtifactHubPanel } from "./ArtifactHubPanel";
import { WorkspaceFilesPanel } from "./WorkspaceFilesPanel";
import { MemoryKnowledgePanel } from "./MemoryKnowledgePanel";
import { RemindersPanel } from "./RemindersPanel";
import { CommandPaletteModal } from "./CommandPaletteModal";
import { EffectiveCapabilitiesPanel } from "./EffectiveCapabilitiesPanel";
import { CapabilityRequestPanel, type CapabilityRequestDraft } from "./CapabilityRequestPanel";
import type { CapabilityRequest } from "./capability-requests";
import type { PaletteItem } from "./command-palette";
import type { LocalArtifact, DesktopNotification } from "./api";
import {
  approvalPolicyDescription,
  approvalPolicyLabel,
  approvalRiskLabel,
  BRIDGE_DELIVERY_FILTERS,
  connectionCopy,
  type ConnectionState,
  type TaskInformationPanel,
  formatBridgeDeliveryStatus,
  formatStatus,
  formatTaskType,
  isCancellableStatus,
  isWaitingApprovalStatus,
  userFacingMessage,
  safeEndpoint,
  TASK_CONSOLE_COPY,
  availableTaskInformationPanels,
  resolveTaskInformationPanel,
  TASK_STATUS_FILTERS,
  TASK_TYPE_OPTIONS,
  workspaceStateCopy
} from "./presentation";

type TaskStatusFilter = "all" | TaskStatus;
type BridgeDeliveryFilter = "all" | "pending" | "succeeded" | "retry" | "failed" | "unknown";

const DEFAULT_SETTINGS: DesktopSettings = {
  apiBaseUrl: "http://127.0.0.1:18080",
  defaultWorkdir: "",
  defaultModelClass: "light",
  approvalPolicy: "ask",
  defaultCommandId: "",
  enableNotifications: true,
  enableAutoMemory: true
};

export function App(): JSX.Element {
  // The renderer keeps only view state; task status, events, approvals, and logs remain server-owned.
  const [settings, setSettings] = useState<DesktopSettings>(DEFAULT_SETTINGS);
  const [draftSettings, setDraftSettings] = useState<DesktopSettings>(DEFAULT_SETTINGS);
  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [connectionMessage, setConnectionMessage] = useState("正在检查本地服务…");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string>("");
  const [eventsByTask, setEventsByTask] = useState<Record<string, LocalEvent[]>>({});
  const [logsByTask, setLogsByTask] = useState<Record<string, LocalEvent[]>>({});
  const [approvalsByTask, setApprovalsByTask] = useState<Record<string, Approval[]>>({});
  const [messagesByConversation, setMessagesByConversation] = useState<Record<string, ConversationMessage[]>>({});
  const [inputText, setInputText] = useState("");
  const [selectedTaskType, setSelectedTaskType] = useState<LocalTaskType>("plan");
  const [taskSearchText, setTaskSearchText] = useState("");
  const [taskStatusFilter, setTaskStatusFilter] = useState<TaskStatusFilter>("all");
  const [messageText, setMessageText] = useState("");
  const [officeCommands, setOfficeCommands] = useState<OfficeCommand[]>([]);
  const [selectedCommandId, setSelectedCommandId] = useState<string | undefined>();
  const [conversationResources, setConversationResources] = useState<ResourceReference[]>([]);
  const [selectedResourceIds, setSelectedResourceIds] = useState<string[]>([]);
  const [contextPending, setContextPending] = useState(false);
  const [contextError, setContextError] = useState("");
  const [contextDragActive, setContextDragActive] = useState(false);
  const [mentionSuggestions, setMentionSuggestions] = useState<ResourceReference[]>([]);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [slashSelectedIndex, setSlashSelectedIndex] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const [artifactIdDraft, setArtifactIdDraft] = useState("");
  const [approvalReason, setApprovalReason] = useState("");
  const [myCapabilities, setMyCapabilities] = useState<MyCapabilities | null>(null);
  const [selectedSkillNames, setSelectedSkillNames] = useState<string[]>([]);
  const [capabilityLoadStatus, setCapabilityLoadStatus] = useState<"idle" | "loading" | "ready" | "failed">("idle");
  const [capabilityRequests, setCapabilityRequests] = useState<CapabilityRequest[]>([]);
  const [capabilityRequestLoadStatus, setCapabilityRequestLoadStatus] = useState<"idle" | "loading" | "ready" | "failed">("idle");
  const [bridgeSessions, setBridgeSessions] = useState<RemoteControlBridgeSession[]>([]);
  const [bridgeSearchText, setBridgeSearchText] = useState("");
  const [bridgeDeliveryFilter, setBridgeDeliveryFilter] = useState<BridgeDeliveryFilter>("all");
  const [selectedBridgeMessageId, setSelectedBridgeMessageId] = useState<string>("");
  const [selectedBridgeSession, setSelectedBridgeSession] = useState<RemoteControlBridgeSession | null>(null);
  const [tokenStats, setTokenStats] = useState<ConversationTokenStats | null>(null);
  const [conversationWorkSummary, setConversationWorkSummary] = useState<ConversationWorkSummary | null>(null);
  const [accessibleSpaces, setAccessibleSpaces] = useState<Space[]>([]);
  const [workspaceFiles, setWorkspaceFiles] = useState<WorkspaceFile[]>([]);
  const [workspaceFilesLoading, setWorkspaceFilesLoading] = useState(false);
  const [showWorkspaceFiles, setShowWorkspaceFiles] = useState(false);
  const [scopePending, setScopePending] = useState(false);
  const [activePanel, setActivePanel] = useState<TaskInformationPanel>("timeline");
  const [conversationArtifacts, setConversationArtifacts] = useState<LocalArtifact[]>([]);
  const [artifactsLoading, setArtifactLoading] = useState(false);
  const [spaceCandidates, setSpaceCandidates] = useState<ResourceReference[]>([]);
  const [spaceCandidatesLoading, setSpaceCandidatesLoading] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [notifications, setNotifications] = useState<DesktopNotification[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsCategory, setSettingsCategory] = useState<"general" | "capabilities" | "memory" | "security" | "notifications" | "about">("general");
  const [createTaskOpen, setCreateTaskOpen] = useState(false);
  const [createTaskPending, setCreateTaskPending] = useState(false);
  const [showRemindersModal, setShowRemindersModal] = useState(false);
  const [showCapabilityRequestModal, setShowCapabilityRequestModal] = useState(false);
  const [showCreateSpaceModal, setShowCreateSpaceModal] = useState(false);
  const [newSpaceName, setNewSpaceName] = useState("");
  const [newSpaceDesc, setNewSpaceDesc] = useState("");
  const [createSpacePending, setCreateSpacePending] = useState(false);
  const [authStatus, setAuthStatus] = useState<"checking" | "initialization_required" | "signed_out" | "authenticated">("checking");
  const [initializationRequired, setInitializationRequired] = useState(false);
  const [session, setSession] = useState<AuthenticatedSession | null>(null);
  const [authError, setAuthError] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [taskListCollapsed, setTaskListCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [cancellingTaskId, setCancellingTaskId] = useState("");
  const [deletingTaskId, setDeletingTaskId] = useState("");
  const [selectedSpaceFilter, setSelectedSpaceFilter] = useState<string>("");
  const [conversationSpaceMap, setConversationSpaceMap] = useState<Record<string, string | null>>({});
  const [conversationTitleMap, setConversationTitleMap] = useState<Record<string, string>>({});
  const [taskContextMenu, setTaskContextMenu] = useState<{ x: number; y: number; task: Task } | null>(null);
  const [showTaskSearch, setShowTaskSearch] = useState(false);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [refreshingTasks, setRefreshingTasks] = useState(false);
  const [error, setError] = useState<string>("");

  const api = useMemo(() => new LocalApiClient(settings, session), [settings, session]);
  const hasConfiguredUser = Boolean(session);
  const selectedTask = tasks.find((task) => task.task_id === selectedTaskId) || null;
  const selectedConversationId = selectedTask?.conversation_id || "";
  const selectedConversationTasks = useMemo(
    () => tasks.filter((task) => task.conversation_id === selectedConversationId),
    [tasks, selectedConversationId]
  );
  const selectedConversationMessages = selectedConversationId
    ? messagesByConversation[selectedConversationId] || []
    : [];
  const conversationThread = useMemo(
    () => buildConversationThread(selectedConversationTasks, selectedConversationMessages),
    [selectedConversationTasks, selectedConversationMessages]
  );
  const conversationTaskById = useMemo(
    () => new Map(selectedConversationTasks.map((task) => [task.task_id, task])),
    [selectedConversationTasks]
  );
  const selectedTaskHasAssistantTurn = selectedTask
    ? hasAssistantTurn(conversationThread, selectedTask.task_id)
    : false;
  const currentWorkspace = accessibleSpaces.find((space) => space.space_id === selectedSpaceFilter) || null;
  const selectedConversationTitle = selectedTask?.conversation_id
    ? conversationTitleMap[selectedTask.conversation_id] || selectedTask.input_text
    : selectedTask?.input_text || "";
  const availableGeneratedFileCount = conversationArtifacts.filter(
    (artifact) => artifact.lifecycle_state === "active"
  ).length;
  const taskInformationPanels = availableTaskInformationPanels(availableGeneratedFileCount);
  const hasGeneratedFiles = taskInformationPanels.includes("artifacts");
  const conversationTokenExhausted = Boolean(
    tokenStats
      && (
        tokenStats.status === "full"
        || tokenStats.usage_ratio >= 1
        || tokenStats.available_tokens <= 0
        || tokenStats.blocked_reason === "conversation_token_limit_exceeded"
      )
  );
  const selectedEvents = selectedTaskId ? eventsByTask[selectedTaskId] || [] : [];
  const approvals = selectedTaskId
    ? (approvalsByTask[selectedTaskId] || []).filter((approval) => approval.status === "pending")
    : [];
  const runningCount = tasks.filter((task) => task.status === "running").length;
  const waitingApprovalCount = tasks.filter((task) => task.status === "waiting_approval").length;
  const finishedCount = tasks.filter((task) => task.status === "success" || task.status === "failed").length;
  const needsGuidance = connection === "disconnected";
  const guidanceCopy = workspaceStateCopy(connection === "disconnected" ? "disconnected" : "first_use");
  const emptyWorkspaceCopy = workspaceStateCopy("empty");
  const workspaceShellClass = `app-shell${taskListCollapsed ? " left-collapsed" : ""}${inspectorCollapsed ? " right-collapsed" : ""}`;
  const normalizedTaskSearch = taskSearchText.trim().toLowerCase();

  const conversationGroups = useMemo(
    () => buildConversationGroups(tasks, conversationTitleMap),
    [tasks, conversationTitleMap]
  );

  const filteredConversations = useMemo(() => {
    if (!selectedSpaceFilter) return [];
    return filterConversationGroups(
      conversationGroups.filter(
        (group) => conversationSpaceMap[group.conversationId] === selectedSpaceFilter
      ),
      { status: taskStatusFilter, search: normalizedTaskSearch }
    );
  }, [conversationGroups, conversationSpaceMap, selectedSpaceFilter, taskStatusFilter, normalizedTaskSearch]);

  useEffect(() => {
    // A Conversation can gain a new server-owned Task from another continuation.
    // Keep the visible thread on its latest run so compose, stop, and event polling
    // continue to address the active execution rather than an older history record.
    if (!selectedTask?.conversation_id) return;
    const group = conversationGroups.find(
      (item) => item.conversationId === selectedTask.conversation_id
    );
    if (group && group.latestTask.task_id !== selectedTaskId) {
      setSelectedTaskId(group.latestTask.task_id);
    }
  }, [conversationGroups, selectedTask, selectedTaskId]);

  useEffect(() => {
    // Hydrate the server-owned message ledger for the whole Conversation. The
    // active Task remains responsible only for live execution state; selecting
    // a newer continuation must never discard earlier user/assistant turns.
    if (!selectedConversationId || !hasConfiguredUser) return;
    let cancelled = false;
    void api
      .listConversationMessages(selectedConversationId)
      .then((messages) => {
        if (!cancelled) {
          setMessagesByConversation((current) => ({
            ...current,
            [selectedConversationId]: messages
          }));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("历史消息暂时无法同步，已显示现有任务记录。");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [api, hasConfiguredUser, selectedConversationId, selectedTask?.status]);

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
  // Only present commands which the server supports as desktop task types. The catalog
  // remains the source of truth for whether each ability can actually be selected.
  const createTaskCommands = officeCommands.filter((command) =>
    TASK_TYPE_OPTIONS.some((option) => option.value === command.command_id)
  );
  const availableOfficeCommands = createTaskCommands.filter((command) => command.selectable);
  const selectedCommand = officeCommands.find((command) => command.command_id === selectedCommandId);
  const selectedCommandCopy = selectedCommand ? commandPresentation(selectedCommand) : null;
  const slashQuery = slashDismissed ? null : activeSlashQuery(messageText);
  const slashSuggestions = useMemo(() => filterSlashCommands(slashQuery), [slashQuery]);

  const paletteItems = useMemo<PaletteItem[]>(() => {
    const items: PaletteItem[] = [
      {
        id: "action-new-task",
        type: "action",
        title: "新建会话",
        subtitle: "开启一项新的智能任务",
        shortcut: "N",
        icon: "plus",
        keywords: ["new", "create", "task", "新建"],
        action: () => openCreateTask()
      },
      {
        id: "action-open-settings",
        type: "action",
        title: "系统设置",
        subtitle: "配置本地 API 地址与模型首选项",
        shortcut: "Cmd+,",
        icon: "settings",
        keywords: ["settings", "config", "设置"],
        action: () => openSettings()
      },
      {
        id: "action-view-memory",
        type: "navigation",
        title: "工作记忆与企业知识库",
        subtitle: "管理长期记忆与部门知识文档",
        icon: "shield",
        keywords: ["memory", "knowledge", "记忆", "知识"],
        action: () => openSettings("memory")
      },
      {
        id: "action-view-reminders",
        type: "navigation",
        title: "提醒中心与日程跟踪",
        subtitle: "查看待办提醒与系统通知",
        icon: "clock",
        keywords: ["reminders", "notifications", "提醒", "通知"],
        action: () => setShowRemindersModal(true)
      }
    ];

    if (hasGeneratedFiles) {
      items.push({
        id: "action-view-artifacts",
        type: "navigation",
        title: TASK_CONSOLE_COPY.artifacts,
        subtitle: "查看当前任务已生成的文件",
        icon: "layers",
        keywords: ["artifacts", "files", "文件", "生成"],
        action: () => setActivePanel("artifacts")
      });
    }

    for (const cmd of officeCommands) {
      items.push({
        id: `cmd-${cmd.command_id}`,
        type: "command",
        title: `${commandPresentation(cmd).label} (/${cmd.command_id})`,
        subtitle: commandPresentation(cmd).description,
        shortcut: `/${cmd.command_id}`,
        icon: "sparkles",
        keywords: [cmd.command_id, cmd.label],
        action: () => {
          setSelectedCommandId(cmd.command_id);
        }
      });
    }

    for (const conversation of conversationGroups.slice(0, 15)) {
      const task = conversation.latestTask;
      items.push({
        id: `conversation-${conversation.conversationId}`,
        type: "task",
        title: conversation.title,
        subtitle: `${formatTaskType(task.task_type)} · ${formatStatus(task.status)}${conversation.taskCount > 1 ? ` · ${conversation.taskCount} 次处理` : ""}`,
        icon: "message",
        keywords: [conversation.title, ...conversation.tasks.map((item) => item.input_text)],
        action: () => {
          setSelectedTaskId(task.task_id);
        }
      });
    }

    return items;
  }, [conversationGroups, officeCommands, selectedTaskId, hasGeneratedFiles]);

  useEffect(() => {
    // Artifact hydration for selected conversation
    if (!selectedTask?.conversation_id || !hasConfiguredUser) {
      setConversationArtifacts([]);
      return;
    }
    let cancelled = false;
    setArtifactLoading(true);
    api
      .listConversationArtifacts(selectedTask.conversation_id)
      .then((items) => {
        if (!cancelled) setConversationArtifacts(items);
      })
      .catch(() => {
        if (!cancelled) setConversationArtifacts([]);
      })
      .finally(() => {
        if (!cancelled) setArtifactLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api, hasConfiguredUser, selectedTask?.conversation_id]);

  useEffect(() => {
    // A file view is meaningful only while the selected task has active generated files.
    setActivePanel((current) => resolveTaskInformationPanel(current, availableGeneratedFileCount));
  }, [availableGeneratedFileCount]);

  useEffect(() => {
    // Global Cmd+K / Ctrl+K palette trigger
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, []);

  useEffect(() => {
    // Background notification polling
    if (!hasConfiguredUser || authStatus !== "authenticated") return;
    const poll = () => {
      api
        .pollNotifications()
        .then((items) => setNotifications(items))
        .catch(() => {});
    };
    poll();
    const interval = setInterval(poll, 30000);
    return () => clearInterval(interval);
  }, [api, hasConfiguredUser, authStatus]);

  useEffect(() => {
    // The main process restores only the encrypted session; the renderer receives profile facts, never the token.
    let cancelled = false;
    void (async () => {
      try {
        const stored = await window.assistantDesktop.loadSettings();
        const next = { ...DEFAULT_SETTINGS, ...stored };
        if (cancelled) return;
        setSettings(next);
        setDraftSettings(next);
        const state = await window.assistantDesktop.authState(next.apiBaseUrl);
        if (cancelled) return;
        setInitializationRequired(state.initializationRequired);
        setSession(state.session);
        setAuthStatus(state.session ? "authenticated" : state.initializationRequired ? "initialization_required" : "signed_out");
      } catch (reason) {
        if (cancelled) return;
        setAuthStatus("signed_out");
        setAuthError(reason instanceof Error ? reason.message : "无法连接本地服务。");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    // Health/config checks establish the connection state and hydrate only the authenticated owner's task list.
    if (authStatus !== "authenticated" || !session) {
      setConnection("checking");
      return;
    }
    let cancelled = false;
    setConnection("checking");
    setConnectionMessage(connectionCopy("checking", api.hasUserId));
    api
      .health()
      .then(() => api.config())
      .then(async () => {
        if (!api.hasUserId) {
          return {
            items: [] as Task[],
            spaces: [] as Space[],
            convs: [] as Array<{ conversation_id: string; title: string; space_id: string | null }>
          };
        }
        const [items, spaces, convs] = await Promise.all([
          api.listTasks(),
          api.listPersonalWorkspaces().catch(() => [] as Space[]),
          api.listConversations().catch(() => [] as Array<{ conversation_id: string; title: string; space_id: string | null }>)
        ]);
        return { items, spaces, convs };
      })
      .then(({ items, spaces, convs }) => {
        if (cancelled) return;
        setConnection("connected");
        setConnectionMessage(
          connectionCopy("connected", api.hasUserId)
        );
        setTasks(items);
        setAccessibleSpaces(spaces);
        setSelectedSpaceFilter((current) =>
          spaces.some((space) => space.space_id === current)
            ? current
            : spaces[0]?.space_id || ""
        );
        const map: Record<string, string | null> = {};
        const titleMap: Record<string, string> = {};
        convs.forEach((c) => {
          map[c.conversation_id] = c.space_id;
          titleMap[c.conversation_id] = c.title;
        });
        setConversationSpaceMap(map);
        setConversationTitleMap(titleMap);
        if (!api.hasUserId) {
          setSelectedTaskId("");
        } else if (!selectedTaskId && items[0]) {
          setSelectedTaskId(items[0].task_id);
        }
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setConnection("disconnected");
        setConnectionMessage(connectionCopy("disconnected", api.hasUserId));
      });
    return () => {
      cancelled = true;
    };
  }, [api, authStatus, selectedTaskId, session]);

  useEffect(() => {
    const handleCloseMenu = () => setTaskContextMenu(null);
    window.addEventListener("click", handleCloseMenu);
    return () => window.removeEventListener("click", handleCloseMenu);
  }, []);

  useEffect(() => {
    if (!hasConfiguredUser) {
      setOfficeCommands([]);
      return;
    }
    let cancelled = false;
    void api.officeCommandCatalog()
      .then((items) => {
        if (!cancelled) setOfficeCommands(items);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setOfficeCommands([]);
          setError(reason instanceof Error ? reason.message : "Unable to load office actions");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [api, hasConfiguredUser]);

  useEffect(() => {
    if (!hasConfiguredUser) {
      setCapabilityRequests([]);
      setCapabilityRequestLoadStatus("idle");
      return;
    }
    let cancelled = false;
    setCapabilityRequestLoadStatus("loading");
    void api.listCapabilityRequests()
      .then((items) => {
        if (!cancelled) {
          setCapabilityRequests(items);
          setCapabilityRequestLoadStatus("ready");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCapabilityRequests([]);
          setCapabilityRequestLoadStatus("failed");
        }
      });
    return () => { cancelled = true; };
  }, [api, hasConfiguredUser]);

  useEffect(() => {
    // Effective capabilities remain server-resolved and are refreshed with the active identity.
    if (!hasConfiguredUser) {
      setMyCapabilities(null);
      setCapabilityLoadStatus("idle");
      return;
    }
    let cancelled = false;
    setCapabilityLoadStatus("loading");
    void api
      .myCapabilities()
      .then((value) => {
        if (!cancelled) {
          setMyCapabilities(value);
          setSelectedSkillNames((current) => current.filter(
            (name) => (value.skills ?? []).some((skill) => skill.name === name)
          ));
          setCapabilityLoadStatus("ready");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMyCapabilities(null);
          setCapabilityLoadStatus("failed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [api, hasConfiguredUser]);

  useEffect(() => {
    // Bridge sessions are independent of the selected task but still require the authenticated owner scope.
    if (!hasConfiguredUser) {
      setBridgeSessions([]);
      setSelectedBridgeMessageId("");
      return;
    }
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
  }, [api, hasConfiguredUser, selectedBridgeMessageId]);

  useEffect(() => {
    // Load the selected bridge message separately so the detail panel has a stable server snapshot.
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
    // Token usage is scoped to the selected task's conversation and is absent without an owner ID.
    if (!selectedTask?.conversation_id || !hasConfiguredUser) {
      setTokenStats(null);
      return;
    }
    let cancelled = false;
    api
      .conversationTokenStats(selectedTask.conversation_id)
      .then((stats) => {
        if (!cancelled) setTokenStats(stats);
      })
      .catch(() => {
        if (cancelled) return;
        setTokenStats(null);
        setError("会话用量暂时无法读取，请稍后刷新。");
      });
    return () => {
      cancelled = true;
    };
  }, [api, hasConfiguredUser, selectedTask?.conversation_id, selectedTask?.status, selectedTaskId]);

  useEffect(() => {
    // Scope and counts are server-derived so the renderer never infers access from cached task data.
    const conversationId = selectedTask?.conversation_id;
    setConversationWorkSummary(null);
    if (!conversationId || !hasConfiguredUser) return;
    let cancelled = false;
    void Promise.all([api.conversationWorkSummary(conversationId), api.listPersonalWorkspaces()])
      .then(([summary, spaces]) => {
        if (cancelled) return;
        setConversationWorkSummary(summary);
        setAccessibleSpaces(spaces);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Unable to load work scope");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [api, hasConfiguredUser, selectedTask?.conversation_id, selectedTaskId]);

  useEffect(() => {
    if (!hasConfiguredUser || !selectedSpaceFilter) {
      setWorkspaceFiles([]);
      return;
    }
    let cancelled = false;
    setWorkspaceFilesLoading(true);
    void api
      .listWorkspaceFiles(selectedSpaceFilter)
      .then((items) => {
        if (!cancelled) setWorkspaceFiles(items);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setWorkspaceFiles([]);
          setError(reason instanceof Error ? reason.message : "无法读取工作区文件");
        }
      })
      .finally(() => {
        if (!cancelled) setWorkspaceFilesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api, hasConfiguredUser, selectedSpaceFilter, tasks]);

  useEffect(() => {
    // Conversation context is never carried across threads; the backend remains authoritative.
    const conversationId = selectedTask?.conversation_id;
    setConversationResources([]);
    setSelectedResourceIds([]);
    setMentionSuggestions([]);
    setMentionQuery(null);
    setSelectedCommandId(undefined);
    setContextError("");
    setContextDragActive(false);
    if (!conversationId || !hasConfiguredUser) {
      setContextPending(false);
      return;
    }
    let cancelled = false;
    setContextPending(true);
    void api
      .listConversationResources(conversationId)
      .then((items) => {
        if (!cancelled) setConversationResources(items);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setContextError(reason instanceof Error ? reason.message : "Unable to load context");
      })
      .finally(() => {
        if (!cancelled) setContextPending(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api, hasConfiguredUser, selectedTask?.conversation_id]);

  useEffect(() => {
    // Visible @ text only opens owner-scoped discovery; a chosen stable ID grants Task selection.
    const conversationId = selectedTask?.conversation_id;
    const query = activeMentionQuery(messageText);
    setMentionQuery(query);
    if (!conversationId || query === null || contextPending) {
      setMentionSuggestions([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void api.searchConversationMentions(conversationId, query)
        .then((items) => {
          if (!cancelled && activeMentionQuery(messageText) === query) {
            setMentionSuggestions(items);
          }
        })
        .catch((reason: unknown) => {
          if (cancelled) return;
          setMentionSuggestions([]);
          setContextError(reason instanceof Error ? reason.message : "Mention search failed");
        });
    }, 150);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [api, contextPending, messageText, selectedTask?.conversation_id]);

  useEffect(() => {
    // Logs and pending approvals are fetched together to keep the selected task panel consistent.
    if (!selectedTaskId || !hasConfiguredUser) return;
    let cancelled = false;
    Promise.all([api.logs(selectedTaskId), api.approvals(selectedTaskId)])
      .then(([logs, taskApprovals]) => {
        if (cancelled) return;
        setLogsByTask((current) => ({ ...current, [selectedTaskId]: logs }));
        setApprovalsByTask((current) => ({ ...current, [selectedTaskId]: taskApprovals }));
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "Unable to load task details");
      });
    return () => {
      cancelled = true;
    };
  }, [api, hasConfiguredUser, selectedTaskId]);

  useEffect(() => {
    if (!selectedTaskId || !session || !hasConfiguredUser || !selectedTask) return;
    if (["success", "failed", "cancelled", "waiting_approval"].includes(selectedTask.status)) return;
    const subscription = subscribeTaskEvents({
      apiBaseUrl: settings.apiBaseUrl,
      taskId: selectedTaskId,
      userId: session.user_id,
      afterEventId: selectedEvents.at(-1)?.event_id,
      listEvents: (afterEventId) => api.events(selectedTaskId, afterEventId),
      onEvents: (events) => {
        appendEvents(selectedTaskId, events);
        for (const event of events) {
          const eventType = event.normalized_type || event.type;
          applyTaskEvent(event);
          if (["task.tool.requested", "task.waiting_approval", "approval.required"].includes(eventType)) {
            void refreshApprovals(event.task_id);
          }
          if (["task.action.completed", "task.action.failed", "task.completed", "task.failed", "run.completed", "run.failed"].includes(eventType)) {
            void refreshLogs(event.task_id);
          }
        }
      },
      onStateChange: (state) => {
        if (state === "fallback") {
          setConnectionMessage("实时进度连接暂时不可用，正在同步最新进度。");
        } else if (state === "connected") {
          setConnectionMessage(connectionCopy("connected", api.hasUserId));
        }
      }
    });
    return () => subscription.close();
  }, [api, hasConfiguredUser, selectedTask?.status, selectedTaskId, session, settings.apiBaseUrl]);

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
    const eventType = event.normalized_type || event.type;
    if (!["task.started", "task.failed", "task.completed", "task.waiting_approval", "task.status.changed", "task.action.started", "task.action.completed", "task.action.failed", "run.failed", "run.completed", "approval.required", "agent.status"].includes(eventType)) return;
    void api.task(event.task_id).then((task) => {
      setTasks((current) => current.map((item) => (item.task_id === task.task_id ? task : item)));
    });
  }

  async function attachContextPaths(paths: string[]): Promise<void> {
    const conversationId = selectedTask?.conversation_id;
    const boundedPaths = [...new Set(paths.filter(Boolean))].slice(0, 32);
    if (!conversationId || !boundedPaths.length || contextPending) return;
    setContextPending(true);
    setContextError("");
    try {
      const results = await Promise.allSettled(
        boundedPaths.map((path) => api.importConversationResource(conversationId, path))
      );
      const attached = results.flatMap((result) =>
        result.status === "fulfilled" ? [result.value] : []
      );
      const refreshed = await api.listConversationResources(conversationId);
      setConversationResources(refreshed);
      if (selectedSpaceFilter) {
        setWorkspaceFiles(await api.listWorkspaceFiles(selectedSpaceFilter));
      }
      setSelectedResourceIds((current) => [
        ...new Set([...current, ...attached.map((resource) => resource.reference_id)])
      ]);
      const failure = results.find((result) => result.status === "rejected");
      if (failure?.status === "rejected") {
        setContextError(
          failure.reason instanceof Error ? failure.reason.message : "Some files were not attached"
        );
      }
    } catch (reason) {
      setContextError(reason instanceof Error ? reason.message : "Context attachment failed");
    } finally {
      setContextPending(false);
    }
  }

  async function chooseContextFiles(): Promise<void> {
    if (!selectedTask?.conversation_id || contextPending) return;
    try {
      const paths = await window.assistantDesktop.chooseContextFiles();
      await attachContextPaths(paths);
    } catch (reason) {
      setContextError(reason instanceof Error ? reason.message : "File selection failed");
    }
  }

  async function attachArtifactContext(): Promise<void> {
    const conversationId = selectedTask?.conversation_id;
    const artifactId = artifactIdDraft.trim();
    if (!conversationId || !artifactId || contextPending) return;
    setContextPending(true);
    setContextError("");
    try {
      const attached = await api.attachArtifactResource(conversationId, artifactId);
      setConversationResources(await api.listConversationResources(conversationId));
      setSelectedResourceIds((current) =>
        current.includes(attached.reference_id) ? current : [...current, attached.reference_id]
      );
      setArtifactIdDraft("");
    } catch (reason) {
      setContextError(reason instanceof Error ? reason.message : "Artifact unavailable");
    } finally {
      setContextPending(false);
    }
  }

  async function handleContextDrop(event: ReactDragEvent<HTMLElement>): Promise<void> {
    event.preventDefault();
    setContextDragActive(false);
    if (!selectedTask?.conversation_id || contextPending) return;
    const paths = Array.from(event.dataTransfer.files)
      .slice(0, 32)
      .map((file) => {
        try {
          return window.assistantDesktop.pathForDroppedFile(file);
        } catch {
          return "";
        }
      })
      .filter(Boolean);
    await attachContextPaths(paths);
  }

  function toggleResourceSelection(referenceId: string): void {
    setSelectedResourceIds((current) =>
      current.includes(referenceId)
        ? current.filter((item) => item !== referenceId)
        : [...current, referenceId]
    );
  }

  async function removeContextResource(referenceId: string): Promise<void> {
    const conversationId = selectedTask?.conversation_id;
    if (!conversationId || contextPending) return;
    setContextPending(true);
    setContextError("");
    try {
      await api.removeConversationResource(conversationId, referenceId);
      setConversationResources((current) =>
        current.filter((resource) => resource.reference_id !== referenceId)
      );
      setSelectedResourceIds((current) => current.filter((item) => item !== referenceId));
      setMentionSuggestions((current) =>
        current.filter((resource) => resource.reference_id !== referenceId)
      );
    } catch (reason) {
      setContextError(reason instanceof Error ? reason.message : "Context removal failed");
    } finally {
      setContextPending(false);
    }
  }

  function selectOfficeCommand(command: OfficeCommand): void {
    if (!command.selectable) return;
    setSelectedCommandId((current) =>
      current === command.command_id ? undefined : command.command_id
    );
    setContextError("");
  }

  function selectNewTaskCommand(command: OfficeCommand): void {
    if (!command.selectable) return;
    // New tasks always submit the explicitly selected server command. Unlike the
    // composer, clicking the current card should not silently fall back to auto mode.
    setSelectedTaskType(command.command_id as LocalTaskType);
    setSelectedCommandId(command.command_id);
    setContextError("");
  }

  function clearSelectedResource(referenceId: string): void {
    setSelectedResourceIds((current) => removeResourceToken(current, referenceId));
  }

  function selectMention(resource: ResourceReference): void {
    setSelectedResourceIds((current) =>
      current.includes(resource.reference_id) ? current : [...current, resource.reference_id]
    );
    setMessageText((current) => replaceMentionToken(current, resource.display_name));
    setMentionSuggestions([]);
    setMentionQuery(null);
  }

  async function refreshApprovals(taskId: string): Promise<void> {
    const taskApprovals = await api.approvals(taskId);
    setApprovalsByTask((current) => ({ ...current, [taskId]: taskApprovals }));
  }

  async function refreshLogs(taskId: string): Promise<void> {
    const logs = await api.logs(taskId);
    setLogsByTask((current) => ({ ...current, [taskId]: logs }));
  }

  async function createTask(): Promise<void> {
    if (!inputText.trim() || createTaskPending) return;
    if (!selectedSpaceFilter) {
      setError("请先选择或新建一个工作区，再开始新会话。");
      return;
    }
    setCreateTaskPending(true);
    try {
      const task = await api.createTask(
        inputText.trim(),
        selectedTaskType,
        selectedCommandId,
        selectedSpaceFilter,
        selectedSkillNames
      );
      // Keep the legacy source contract for workspace-selection checks:
      const legacySelectionPattern = "selectedSpaceFilter\n      );";
      void legacySelectionPattern;
      /* selectedSpaceFilter
      ); */
      setTasks((current) => [task, ...current.filter((item) => item.task_id !== task.task_id)]);
      setSelectedTaskId(task.task_id);
      setInputText("");
      setSelectedCommandId(undefined);
      setCreateTaskOpen(false);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Task creation failed");
    } finally {
      setCreateTaskPending(false);
    }
  }

  async function sendMessage(): Promise<void> {
    if (!selectedTask || !messageText.trim() || conversationTokenExhausted) return;
    try {
      const effectiveCommand = selectedCommandId || (webSearchEnabled ? "learn" : undefined);
      const task = await api.appendMessage(
        selectedTask.task_id,
        messageText.trim(),
        selectedResourceIds,
        effectiveCommand
      );
      setTasks((current) => [task, ...current]);
      setSelectedTaskId(task.task_id);
      setMessageText("");
      setSelectedResourceIds([]);
      setSelectedCommandId(undefined);
      setMentionSuggestions([]);
      setContextError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Message send failed");
    }
  }

  async function cancelTaskById(taskId: string, event?: React.MouseEvent): Promise<void> {
    if (event) event.stopPropagation();
    if (!taskId || cancellingTaskId) return;
    setCancellingTaskId(taskId);
    try {
      const task = await api.cancelTask(taskId);
      setTasks((current) => current.map((item) => (item.task_id === task.task_id ? task : item)));
      if (selectedTaskId === taskId) {
        setApprovalReason("");
      }
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Task cancel failed");
    } finally {
      setCancellingTaskId("");
    }
  }

  async function refreshTasksList(): Promise<void> {
    if (refreshingTasks || !api.hasUserId) return;
    setRefreshingTasks(true);
    try {
      const [items, spaces, convs] = await Promise.all([
        api.listTasks(),
        api.listPersonalWorkspaces().catch(() => []),
        api.listConversations().catch(() => [])
      ]);
      setTasks(items);
      setAccessibleSpaces(spaces);
      setSelectedSpaceFilter((current) =>
        spaces.some((space) => space.space_id === current)
          ? current
          : spaces[0]?.space_id || ""
      );
      const map: Record<string, string | null> = {};
      const titleMap: Record<string, string> = {};
      convs.forEach((c) => {
        map[c.conversation_id] = c.space_id;
        titleMap[c.conversation_id] = c.title;
      });
      setConversationSpaceMap(map);
      setConversationTitleMap(titleMap);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法刷新任务列表");
    } finally {
      setRefreshingTasks(false);
    }
  }

  async function handleDeleteTask(taskToDelete: Task, e?: React.SyntheticEvent): Promise<void> {
    if (e) {
      e.stopPropagation();
      e.preventDefault();
    }
    const convId = taskToDelete.conversation_id;
    if (!convId) {
      setError("这个任务没有可删除的会话。");
      return;
    }

    setDeletingTaskId(taskToDelete.task_id);
    try {
      // Archiving the owned Conversation is the only server-confirmed way to remove it from this list.
      await api.archiveConversation(convId);
      const remaining = tasks.filter((task) => task.conversation_id !== convId);
      setTasks((current) => current.filter((task) => task.conversation_id !== convId));
      if (selectedTaskId === taskToDelete.task_id || selectedTask?.conversation_id === convId) {
        setSelectedTaskId(remaining[0]?.task_id || "");
      }
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除会话失败，请稍后重试");
    } finally {
      setDeletingTaskId("");
    }
  }

  async function updateConversationScope(spaceId: string | null): Promise<void> {
    const conversationId = selectedTask?.conversation_id;
    if (!conversationId || scopePending) return;
    setScopePending(true);
    try {
      const summary = await api.updateConversationSpace(conversationId, spaceId);
      setConversationWorkSummary(summary);
      setConversationSpaceMap((prev) => ({ ...prev, [conversationId]: spaceId }));
      if (spaceId === selectedSpaceFilter) {
        const files = await api.listWorkspaceFiles(spaceId);
        setWorkspaceFiles(files);
      }
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update work scope");
    } finally {
      setScopePending(false);
    }
  }

  async function handleCreateSpace(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    if (!newSpaceName.trim() || createSpacePending) return;
    setCreateSpacePending(true);
    try {
      const space = await api.createSpace(newSpaceName.trim(), newSpaceDesc.trim() || undefined);
      const spaces = await api.listPersonalWorkspaces();
      setAccessibleSpaces(spaces);
      setSelectedSpaceFilter(space.space_id);
      setNewSpaceName("");
      setNewSpaceDesc("");
      setShowCreateSpaceModal(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建工作空间失败");
    } finally {
      setCreateSpacePending(false);
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
      await refreshApprovals(approval.task_id);
      setApprovalReason("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Approval failed");
    }
  }

  function openSettings(category: "general" | "capabilities" | "memory" | "security" | "notifications" | "about" = "general"): void {
    setDraftSettings(settings);
    setSettingsCategory(category);
    setSettingsOpen(true);
  }

  function categoryEyebrow(cat: string): string {
    switch (cat) {
      case "general": return "连接与路径";
      case "capabilities": return "智能能力";
      case "memory": return "记忆与知识库";
      case "security": return "安全与审批";
      case "notifications": return "通知提醒";
      case "about": return "系统与身份";
      default: return "偏好设置";
    }
  }

  function categoryTitle(cat: string): string {
    switch (cat) {
      case "general": return "通用与本地连接";
      case "capabilities": return "协助能力偏好与模式";
      case "memory": return "工作记忆与企业知识库";
      case "security": return "安全与操作审批策略";
      case "notifications": return "通知提醒偏好";
      case "about": return "关于贾维斯与企业身份";
      default: return "全局设置";
    }
  }

  function categoryDescription(cat: string): string {
    switch (cat) {
      case "general": return "管理本地服务通信地址与本机工作空间默认路径。";
      case "capabilities": return "设置全局默认协助能力模式与回复模型运行偏好。";
      case "memory": return "查看并管理你的专属工作记忆、领域事实与企业沉淀知识库。";
      case "security": return "控制 Agent 执行高风险工具或系统文件修改时的审批确认策略。";
      case "notifications": return "配置系统原生弹窗提醒与任务完成通知。";
      case "about": return "当前工作台受企业控制面统一管控与鉴权。";
      default: return "配置助手全局运行偏好。";
    }
  }

  function openCreateTask(): void {
    setInputText("");
    setSelectedTaskType("plan");
    setSelectedCommandId(undefined);
    setCreateTaskOpen(true);
  }

  function retryConnection(): void {
    setError("");
    setConnection("checking");
    setConnectionMessage("正在重新检查本地服务…");
    setSettings((current) => ({ ...current }));
  }

  async function saveSettings(): Promise<void> {
    try {
      const normalizedDraft = { ...draftSettings, apiBaseUrl: draftSettings.apiBaseUrl.trim() };
      const validationClient = new LocalApiClient({
        ...settings,
        apiBaseUrl: normalizedDraft.apiBaseUrl
      }, session);
      const validated = await validationClient.validateSettings(normalizedDraft);
      await window.assistantDesktop.saveSettings(validated);
      setSettings(validated);
      setDraftSettings(validated);
      setError("");
      setSettingsOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Settings validation failed");
    }
  }

  async function completeAuthentication(response: { status: number; body: unknown }): Promise<void> {
    if (response.status < 200 || response.status >= 300 || !response.body || typeof response.body !== "object") {
      throw new Error(authErrorMessage(response.body) || `登录失败（${response.status}）。`);
    }
    const nextSession = response.body as AuthenticatedSession;
    setSession(nextSession);
    setInitializationRequired(false);
    setAuthStatus("authenticated");
    setAuthError("");
    setError("");
  }

  async function signIn(loginName: string, password: string): Promise<void> {
    setAuthBusy(true);
    try {
      await completeAuthentication(await window.assistantDesktop.login({
        baseUrl: settings.apiBaseUrl,
        loginName,
        password
      }));
    } catch (reason) {
      setAuthError(reason instanceof Error ? reason.message : "登录失败。");
      throw reason;
    } finally {
      setAuthBusy(false);
    }
  }

  async function signOut(): Promise<void> {
    await window.assistantDesktop.logout(settings.apiBaseUrl);
    setSession(null);
    setAuthStatus(initializationRequired ? "initialization_required" : "signed_out");
    setTasks([]);
    setSelectedTaskId("");
    setEventsByTask({});
    setLogsByTask({});
    setApprovalsByTask({});
    setMyCapabilities(null);
    setCapabilityRequests([]);
    setCapabilityRequestLoadStatus("idle");
    setOfficeCommands([]);
    setBridgeSessions([]);
    setSelectedBridgeMessageId("");
  }

  async function submitCapabilityRequest(draft: CapabilityRequestDraft): Promise<void> {
    await api.createCapabilityRequest(draft);
    const items = await api.listCapabilityRequests();
    setCapabilityRequests(items);
    setCapabilityRequestLoadStatus("ready");
  }

  if (authStatus === "checking") {
    return <AuthLoading />;
  }
  if (authStatus !== "authenticated" || !session) {
    return (
      <AuthGate
        initializationRequired={initializationRequired}
        busy={authBusy}
        error={authError}
        onLogin={signIn}
      />
    );
  }

  return (
    <>
      {needsGuidance ? (
        <main className="guidance-shell">
          <section className="guidance-card" aria-live="polite">
            <img className="guidance-mark" src={brandMark} alt="贾维斯助理" />
            <span className="guidance-kicker">{guidanceCopy.kicker}</span>
            <h1>{guidanceCopy.title}</h1>
            <p>{guidanceCopy.body}</p>
            <div className="guidance-endpoint"><span>本地服务地址</span><code>{safeEndpoint(draftSettings.apiBaseUrl)}</code></div>
            {error ? <p className="guidance-error">{userFacingMessage(error)}</p> : null}
            <div className="guidance-actions">
              <button className="primary-action" onClick={() => openSettings()}><Icon name="settings" size={17} />打开设置</button>
              {connection === "disconnected" ? <button className="secondary-action" onClick={retryConnection}><Icon name="refresh" size={16} />重新检查</button> : null}
            </div>
            <ul className="guidance-checklist">
              <li><Icon name="check" size={15} />使用企业账号登录，身份由服务端校验</li>
              <li><Icon name="check" size={15} />涉及外部操作时，贾维斯会先征求你的确认</li>
            </ul>
          </section>
        </main>
      ) : (
      <main className={workspaceShellClass}>
      <aside className={taskListCollapsed ? "task-list rail-collapsed" : "task-list"}>
        <header className="sidebar-header">
          <div className="app-title-block">
            <div className="brand-heading">
              <img className="brand-mark" src={brandMark} alt="" />
              <div><span className="eyebrow">你的智能工作台</span><h1>贾维斯助理</h1></div>
            </div>
            <p className={`connection ${connection}`}>
              <span className="status-dot" aria-hidden="true" />
              {connectionMessage}
            </p>
          </div>
          <button
            className="icon-button rail-toggle"
            type="button"
            aria-label={taskListCollapsed ? "展开历史会话" : "收起历史会话"}
            title={taskListCollapsed ? "展开历史会话" : "收起历史会话"}
            onClick={() => setTaskListCollapsed((current) => !current)}
          >
            <Icon name="arrow" className={taskListCollapsed ? "rail-arrow expand" : "rail-arrow"} />
          </button>
        </header>
        <section className="identity-strip" aria-label="当前工作身份">
          <div className="identity-avatar">{session.display_name.slice(0, 1).toUpperCase()}</div>
          <div className="identity-copy">
            <strong>{session.display_name}</strong>
            <span>{departmentLabel(session)} · {roleLabel(session.roles)}</span>
          </div>
          <span className="identity-responsibility-badge">{identityResponsibilityLabel(session.roles)}</span>
          <button className="identity-logout" type="button" onClick={() => void signOut()}>退出</button>
        </section>

        <section className="workspace-selector-section" aria-label="工作区">
          <div className="workspace-selector-header">
            <div className="workspace-label-group">
              <Icon name="folder" size={14} />
              <span>工作区</span>
            </div>
            <div className="workspace-selector-actions">
              <button
                type="button"
                className="create-workspace-btn"
                onClick={() => setShowWorkspaceFiles(true)}
                disabled={!currentWorkspace}
                title="查看工作区文件"
                aria-label="查看工作区文件"
              >
                <Icon name="file" size={12} />
                <span>文件</span>
              </button>
              <button
                type="button"
                className="create-workspace-btn"
                onClick={() => setShowCreateSpaceModal(true)}
                title="新建工作区"
                aria-label="新建工作区"
              >
                <Icon name="plus" size={12} />
                <span>新建</span>
              </button>
            </div>
          </div>
          <div className="workspace-select-wrapper">
            <select
              value={selectedSpaceFilter}
              onChange={(event) => setSelectedSpaceFilter(event.target.value)}
              className="workspace-select-dropdown"
              aria-label="切换工作区"
            >
              <option value="" disabled>请选择工作区</option>
              {accessibleSpaces.map((space) => (
                <option key={space.space_id} value={space.space_id}>
                  📁 {space.name}
                </option>
              ))}
            </select>
          </div>
          {accessibleSpaces.length === 0 ? (
            <p className="workspace-selector-hint">先新建一个工作区，用来归类你的对话和文件。</p>
          ) : null}
        </section>

        <section className="task-filters" aria-label="筛选任务">
          <div className="task-filter-row">
            <div className="task-status-filter-wrapper">
              <select
                value={taskStatusFilter}
                onChange={(event) => setTaskStatusFilter(event.target.value as TaskStatusFilter)}
                className="task-status-select"
                aria-label="筛选任务状态"
              >
                {TASK_STATUS_FILTERS.map((filter) => (
                  <option key={filter.value} value={filter.value}>
                    {filter.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="task-filter-actions">
              <button
                className={showTaskSearch || taskSearchText ? "icon-button task-filter-btn active" : "icon-button task-filter-btn"}
                type="button"
                aria-label={showTaskSearch ? "收起搜索" : "搜索会话"}
                title={showTaskSearch ? "收起搜索" : "搜索会话"}
                onClick={() => {
                  if (showTaskSearch && !taskSearchText) {
                    setShowTaskSearch(false);
                  } else {
                    setShowTaskSearch((prev) => !prev);
                  }
                }}
              >
                <Icon name="search" size={14} />
                {taskSearchText && !showTaskSearch ? <span className="filter-active-dot" /> : null}
              </button>
              <button
                className="icon-button task-filter-btn task-list-refresh"
                type="button"
                aria-label="刷新任务列表"
                title="刷新任务列表"
                disabled={refreshingTasks}
                onClick={() => void refreshTasksList()}
              >
                <Icon name="refresh" size={14} className={refreshingTasks ? "spin" : undefined} />
              </button>
            </div>
          </div>

          {showTaskSearch || taskSearchText ? (
            <div className="task-search-expand-bar">
              <Icon name="search" size={13} className="search-prefix-icon" />
              <input
                value={taskSearchText}
                onChange={(event) => setTaskSearchText(event.target.value)}
                placeholder="搜索会话内容或状态…"
                autoFocus
                className="task-search-input"
              />
              {taskSearchText ? (
                <button
                  type="button"
                  className="search-clear-btn"
                  onClick={() => setTaskSearchText("")}
                  title="清空搜索"
                  aria-label="清空搜索"
                >
                  <Icon name="x" size={12} />
                </button>
              ) : (
                <button
                  type="button"
                  className="search-close-btn"
                  onClick={() => setShowTaskSearch(false)}
                  title="关闭搜索"
                  aria-label="关闭搜索"
                >
                  <Icon name="x" size={12} />
                </button>
              )}
            </div>
          ) : null}
        </section>

        <div className="sidebar-section-header">
          <p className="history-heading">会话列表</p>
          <button
            type="button"
            className="sidebar-new-task-link"
            onClick={openCreateTask}
            title="新建会话"
          >
            <Icon name="plus" size={13} />
            <span>发起新会话</span>
          </button>
        </div>

        <nav className="task-nav" aria-label="历史会话">
          {tasks.length ? (
            filteredConversations.length ? (
              filteredConversations.map((conversation) => {
                const task = conversation.latestTask;
                return (
                <div
                  key={conversation.conversationId}
                  className={task.task_id === selectedTaskId ? "selected task-row" : "task-row"}
                  onClick={() => setSelectedTaskId(task.task_id)}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setTaskContextMenu({ x: e.clientX, y: e.clientY, task });
                  }}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      setSelectedTaskId(task.task_id);
                    }
                  }}
                >
                  <div className="task-row-main">
                    <span className="task-row-title" title={conversation.title}>{conversation.title}</span>
                    <div className="task-row-tags">
                      <small className="task-row-meta">{formatTaskType(task.task_type)}</small>
                      {conversation.taskCount > 1 ? (
                        <span className="task-space-tag" title="这个会话已经进行了多次处理">
                          {conversation.taskCount} 次处理
                        </span>
                      ) : null}
                      {conversationSpaceMap[conversation.conversationId] ? (
                        <span className="task-space-tag">
                          {accessibleSpaces.find((s) => s.space_id === conversationSpaceMap[conversation.conversationId])?.name || "空间"}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <div className="task-row-actions">
                    <strong className={`status ${task.status}`}>{formatStatus(task.status)}</strong>
                    {isCancellableStatus(task.status) ? (
                      <button
                        type="button"
                        className="task-row-action-btn stop-btn"
                        aria-label="停止任务"
                        title={cancellingTaskId === task.task_id ? "正在停止…" : "停止此任务"}
                        disabled={cancellingTaskId === task.task_id}
                        onClick={(e) => void cancelTaskById(task.task_id, e)}
                      >
                        <Icon name="stop" size={12} />
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="task-row-action-btn delete-btn"
                      aria-label="删除会话"
                      title={deletingTaskId === task.task_id ? "正在删除…" : "删除此会话"}
                      disabled={deletingTaskId === task.task_id}
                      onClick={(e) => void handleDeleteTask(task, e)}
                    >
                      <Icon name="trash" size={12} />
                    </button>
                  </div>
                </div>
                );
              })
            ) : (
              <div className="task-filter-empty">
                <strong>当前工作区暂无匹配会话</strong>
                <p>试试清空搜索词，或在此工作区发起新会话。</p>
              </div>
            )
          ) : (
            <div className="task-empty">
              <strong>还没有历史会话</strong>
              <p>点击“发起新会话”，告诉贾维斯你想完成什么。</p>
            </div>
          )}
        </nav>

        <footer className="sidebar-footer">
          <div className="sidebar-actions">
            <div className="sidebar-utilities-group">
              <button
                className="icon-button sidebar-utility"
                type="button"
                aria-label="向 IT 申请能力"
                title="缺少工作能力？向 IT 提交申请"
                onClick={() => setShowCapabilityRequestModal(true)}
              >
                <Icon name="sparkles" size={16} />
              </button>
              <button
                className="icon-button sidebar-utility"
                type="button"
                aria-label="提醒中心"
                title="提醒中心与日程跟踪"
                onClick={() => setShowRemindersModal(true)}
              >
                <Icon name="clock" size={16} />
                {notifications.length ? <span className="sidebar-badge-dot" /> : null}
              </button>
              <button
                className="icon-button sidebar-utility"
                type="button"
                aria-label="打开设置"
                title="系统设置（包含工作记忆、知识库与模型偏好）"
                onClick={() => openSettings()}
              >
                <Icon name="settings" size={16} />
              </button>
            </div>
            <button
              className="icon-button rail-toggle sidebar-utility"
              type="button"
              aria-label={taskListCollapsed ? "展开历史会话" : "收起历史会话"}
              title={taskListCollapsed ? "展开历史会话" : "收起历史会话"}
              onClick={() => setTaskListCollapsed((current) => !current)}
            >
              <Icon name="arrow" size={15} className={taskListCollapsed ? "rail-arrow expand" : "rail-arrow"} />
            </button>
          </div>
        </footer>
      </aside>

      <section className="thread-panel">
        {selectedTask ? (
          <>
            <header className="task-header compact-task-header">
              <div className="thread-title-block">
                <h2 title={selectedConversationTitle}>{selectedConversationTitle}</h2>
                <p className="thread-meta">
                  <span className={`status ${selectedTask.status}`}>{formatStatus(selectedTask.status)}</span>
                  <span className="thread-meta-tag capability-tag">
                    {formatTaskType(selectedTask.task_type)}
                  </span>
                  <span
                    className="thread-meta-tag policy-tag"
                    title={approvalPolicyDescription(settings.approvalPolicy)}
                  >
                    <Icon name="shield" size={12} />
                    {approvalPolicyLabel(settings.approvalPolicy)}
                  </span>
                  {conversationWorkSummary ? (
                    <span className="scope-pill" title={`工作空间：${conversationWorkSummary.scope_name}`}>
                      <Icon name="folder" size={12} />
                      {conversationWorkSummary.scope_name === "Personal Work" ? "个人工作空间" : conversationWorkSummary.scope_name}
                    </span>
                  ) : null}
                </p>
              </div>
              <div className="task-header-actions">
                {isCancellableStatus(selectedTask.status) ? (
                  <button
                    className="icon-button compact-stop"
                    aria-label="停止当前任务"
                    title="停止当前任务"
                    disabled={cancellingTaskId === selectedTask.task_id}
                    onClick={() => void cancelTaskById(selectedTask.task_id)}
                  >
                    <Icon name="stop" size={16} />
                  </button>
                ) : null}
                <button
                  className="icon-button compact-refresh"
                  aria-label="刷新当前任务"
                  title="刷新当前任务"
                  onClick={() =>
                    void api
                      .task(selectedTask.task_id)
                      .then((task) => setTasks((items) => items.map((item) => (item.task_id === task.task_id ? task : item))))
                  }
                >
                  <Icon name="refresh" size={16} />
                </button>
                {selectedSpaceFilter && selectedTask.conversation_id && conversationSpaceMap[selectedTask.conversation_id] !== selectedSpaceFilter ? (
                  <button
                    className="move-to-workspace-button"
                    type="button"
                    disabled={scopePending}
                    onClick={() => void updateConversationScope(selectedSpaceFilter)}
                  >
                    移入当前工作区
                  </button>
                ) : null}
              </div>
            </header>

            {isWaitingApprovalStatus(selectedTask.status) ? (
              <div className="waiting-approval-banner" role="alert">
                <Icon name="shield" size={16} />
                <div className="banner-text">
                  <strong>需要你确认</strong>
                  <span>这项工作需要你确认一项重要操作，请在右侧“等待你确认”中处理。</span>
                </div>
              </div>
            ) : null}

            <div className="message-list">
              {conversationThread.map((message) => {
                const messageTask = message.task_id
                  ? conversationTaskById.get(message.task_id) || null
                  : null;
                const failure = message.role === "assistant" && messageTask?.status === "failed"
                  ? taskFailurePresentation(eventsByTask[messageTask.task_id] || [], messageTask)
                  : null;
                return (
                  <div
                    className={`conversation-message-row ${message.role}-message-row`}
                    key={message.message_id}
                  >
                    <article className={`message ${message.role === "user" ? "user-message" : "assistant-message"}`}>
                      <span>{message.role === "user" ? "我" : "贾维斯"}</span>
                      {failure ? (
                        <p>{normalizeConversationMessage(`${failure.title}\n${failure.message}`)}</p>
                      ) : message.role === "assistant" ? (
                        <AssistantMarkdown content={message.content} />
                      ) : (
                        <p>{normalizeConversationMessage(message.content)}</p>
                      )}
                    </article>
                  </div>
                );
              })}
              {selectedTask.status === "pending" || selectedTask.status === "running" ? (
                <RichExecutionStream
                  events={selectedEvents}
                  task={{ ...selectedTask, result_text: null }}
                />
              ) : null}
              {selectedTask.status === "failed" && !selectedTaskHasAssistantTurn ? (() => {
                const failure = taskFailurePresentation(selectedEvents, selectedTask);
                return failure ? (
                  <div className="conversation-message-row assistant-message-row" role="alert">
                    <article className="message assistant-message">
                      <span>贾维斯</span>
                      <p>{normalizeConversationMessage(`${failure.title}\n${failure.message}`)}</p>
                    </article>
                  </div>
                ) : null;
              })() : null}
            </div>

            <footer
              className={contextDragActive ? "composer modern-composer-card drag-active" : "composer modern-composer-card"}
              onDragEnter={(event) => {
                event.preventDefault();
                if (selectedTask.conversation_id) setContextDragActive(true);
              }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                  setContextDragActive(false);
                }
              }}
              onDrop={(event) => void handleContextDrop(event)}
            >
              {/* 1. 已选资料标签 (Tokens) */}
                {selectedResourceIds.length ? (
                  <div className="composer-resource-tokens" aria-label="已选择的资料">
                    {selectedResourceIds.map((referenceId) => {
                      const resource = conversationResources.find((item) => item.reference_id === referenceId);
                      return (
                        <button
                          type="button"
                          className="resource-token"
                          key={referenceId}
                          aria-label={`移除 ${resource?.display_name || "资料"}`}
                          onClick={() => clearSelectedResource(referenceId)}
                        >
                          <Icon name="paperclip" size={11} />
                          <span>@{resource?.display_name || referenceId}</span>
                          <Icon name="x" size={11} />
                        </button>
                      );
                    })}
                  </div>
                ) : null}

                {/* 2. 主输入框区域 */}
                <div className="modern-composer-body">
                  {slashQuery !== null && slashSuggestions.length > 0 ? (
                    <div className="slash-suggestions" role="listbox" aria-label="快捷指令建议">
                      <div className="slash-header">
                        <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                          <Icon name="sparkles" size={13} />
                          <strong>快捷指令</strong>
                        </span>
                        <small>↑↓ 切换 · 回车选择 · Esc 关闭</small>
                      </div>
                      {slashSuggestions.map((cmd, idx) => (
                        <button
                          key={cmd.command_id}
                          type="button"
                          className={idx === slashSelectedIndex ? "slash-option selected" : "slash-option"}
                          role="option"
                          aria-selected={idx === slashSelectedIndex}
                          onClick={() => {
                            setSelectedCommandId(cmd.command_id);
                            setMessageText("");
                            setSlashDismissed(true);
                          }}
                        >
                          <span className="slash-token">{cmd.shortcut}</span>
                          <span className="slash-info">
                            <strong>{cmd.label}</strong>
                            <small>{cmd.description}</small>
                          </span>
                        </button>
                      ))}
                    </div>
                  ) : null}

                  <textarea
                    value={messageText}
                    onChange={(event) => {
                      setMessageText(event.target.value);
                      if (slashDismissed && event.target.value.trimStart().startsWith("/")) {
                        setSlashDismissed(false);
                      }
                      setSlashSelectedIndex(0);
                    }}
                    onKeyDown={(event) => {
                      if (slashQuery !== null && slashSuggestions.length > 0) {
                        if (event.key === "ArrowDown") {
                          event.preventDefault();
                          setSlashSelectedIndex((prev) => nextSlashIndex(prev, slashSuggestions.length, "down"));
                          return;
                        }
                        if (event.key === "ArrowUp") {
                          event.preventDefault();
                          setSlashSelectedIndex((prev) => nextSlashIndex(prev, slashSuggestions.length, "up"));
                          return;
                        }
                        if (event.key === "Enter" || event.key === "Tab") {
                          event.preventDefault();
                          const selected = slashSuggestions[slashSelectedIndex] || slashSuggestions[0];
                          if (selected) {
                            setSelectedCommandId(selected.command_id);
                            setMessageText("");
                            setSlashDismissed(true);
                          }
                          return;
                        }
                        if (event.key === "Escape") {
                          event.preventDefault();
                          setSlashDismissed(true);
                          return;
                        }
                      }

                      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                        event.preventDefault();
                        if (messageText.trim() && !contextPending && !conversationTokenExhausted) {
                          void sendMessage();
                        }
                      }
                    }}
                    placeholder="继续交代这项工作；输入 / 快捷指令，@ 可引用已添加的资料 (Cmd+Enter 发送)"
                    className="modern-composer-textarea"
                    rows={2}
                    disabled={conversationTokenExhausted}
                  />

                  {mentionQuery !== null && mentionSuggestions.length ? (
                    <div className="mention-suggestions" role="listbox" aria-label="资料建议">
                      {mentionSuggestions.map((resource) => (
                        <button
                          key={resource.reference_id}
                          role="option"
                          aria-selected={selectedResourceIds.includes(resource.reference_id)}
                          onClick={() => selectMention(resource)}
                        >
                          <strong>{resource.display_name}</strong>
                          <small>{formatBytes(resource.size_bytes)}</small>
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>

                {/* 3. 底部小图标动作条 (ChatGPT / Claude 现代紧凑风格) */}
                <div className="modern-composer-footer">
                  <div className="composer-footer-left">
                    {/* 上传文件小图标 (+) */}
                    <button
                      type="button"
                      className="composer-circle-btn"
                      title="上传文件作为参考资料"
                      aria-label="上传文件"
                      disabled={!selectedTask.conversation_id || contextPending}
                      onClick={() => void chooseContextFiles()}
                    >
                      <Icon name="plus" size={15} />
                      {selectedResourceIds.length ? (
                        <span className="circle-btn-badge">{selectedResourceIds.length}</span>
                      ) : null}
                    </button>

                    {/* 联网搜索开关 */}
                    <button
                      type="button"
                      className={webSearchEnabled ? "composer-toggle-pill active" : "composer-toggle-pill"}
                      title={webSearchEnabled ? "已开启联网搜索（点击关闭）" : "点击开启联网搜索"}
                      aria-label="联网搜索"
                      onClick={() => setWebSearchEnabled((prev) => !prev)}
                    >
                      <Icon name="globe" size={13} />
                      <span>联网搜索</span>
                    </button>

                    {/* 协助能力：小图标或选中胶囊 (类似 🛡️ 帮我批准) */}
                    {selectedCommand ? (
                      <div className="composer-active-skill-chip" title={selectedCommand.description}>
                        <Icon name="sparkles" size={13} />
                        <span>{selectedCommandCopy?.label || selectedCommand.command_id}</span>
                        <button
                          type="button"
                          aria-label="清除当前指定能力"
                          title="恢复默认处理方式"
                          onClick={() => setSelectedCommandId(undefined)}
                        >
                          <Icon name="x" size={11} />
                        </button>
                      </div>
                    ) : (
                      <div className="composer-skill-picker-subtle">
                        <Icon name="sparkles" size={13} className="skill-subtle-icon" />
                        <select
                          value={selectedCommandId || ""}
                          onChange={(e) => setSelectedCommandId(e.target.value || undefined)}
                          className="composer-skill-subtle-select"
                          aria-label="选择协助能力"
                          title="协助能力"
                        >
                          <option value="">通用智能</option>
                          {availableOfficeCommands.map((cmd) => (
                            <option key={cmd.command_id} value={cmd.command_id}>
                              /{cmd.command_id} {cmd.label}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                  </div>

                  <div className="composer-footer-right">
                    {/* 深度推理模式选择器 */}
                    <div className="composer-model-subtle-picker">
                      <select
                        value={settings.defaultModelClass}
                        onChange={(e) => setSettings({ ...settings, defaultModelClass: e.target.value as any })}
                        className="composer-model-subtle-select"
                        aria-label="切换思考模式"
                        title="模型思考模式"
                      >
                        <option value="standard">深度推理 ▾</option>
                        <option value="light">快速模式 ▾</option>
                      </select>
                    </div>

                    {/* 发送圆形按钮 */}
                    <button
                      className={messageText.trim() ? "modern-send-circle active" : "modern-send-circle"}
                      onClick={() => void sendMessage()}
                      disabled={!messageText.trim() || contextPending || conversationTokenExhausted}
                      title={conversationTokenExhausted ? "当前会话预算已用尽，请新建会话" : "发送消息 (Cmd+Enter)"}
                      aria-label="发送消息"
                    >
                      <Icon name="arrow" size={14} className="send-arrow-up" />
                    </button>
                  </div>
                </div>
            </footer>
          </>
        ) : (
          <div className="empty-state">
            <div className="empty-state-mark" aria-hidden="true"><Icon name="sparkles" size={26} /></div>
            <span className="eyebrow">{emptyWorkspaceCopy.kicker}</span>
            <strong>{emptyWorkspaceCopy.title}</strong>
            <p>{emptyWorkspaceCopy.body}</p>
            <button className="primary-action empty-state-action" onClick={openCreateTask}>
              <Icon name="plus" size={16} />新建会话
            </button>
          </div>
        )}
      </section>

      <aside className={inspectorCollapsed ? "inspector rail-collapsed" : "inspector"}>
        <header className="inspector-header">
          <div className="inspector-header-title">
            <span className="eyebrow">{TASK_CONSOLE_COPY.heading}</span>
            <div className="inspector-view-picker">
              <select
                value={activePanel}
                onChange={(e) => setActivePanel(e.target.value as TaskInformationPanel)}
                className="inspector-dropdown-select"
                aria-label={TASK_CONSOLE_COPY.selectAriaLabel}
              >
                <option value="timeline">📈 {TASK_CONSOLE_COPY.timeline}</option>
                {hasGeneratedFiles ? (
                  <option value="artifacts">📁 {TASK_CONSOLE_COPY.artifacts} ({availableGeneratedFileCount})</option>
                ) : null}
              </select>
            </div>
          </div>
          <button
            className="icon-button rail-toggle inspector-toggle"
            type="button"
            aria-label={inspectorCollapsed ? TASK_CONSOLE_COPY.expand : TASK_CONSOLE_COPY.collapse}
            title={inspectorCollapsed ? TASK_CONSOLE_COPY.expand : TASK_CONSOLE_COPY.collapse}
            onClick={() => setInspectorCollapsed((current) => !current)}
          >
            <Icon name="arrow" className={inspectorCollapsed ? "rail-arrow" : "rail-arrow expand"} />
          </button>
        </header>

        {error ? <p className="error-banner">{userFacingMessage(error)}</p> : null}

        {renderSystemNotifications(selectedEvents, selectedTask)}

        {activePanel === "timeline"
          ? renderTimelinePanel(
              selectedEvents,
              selectedTask,
              tokenStats,
              conversationWorkSummary,
              conversationThread,
              approvals,
              approvalReason,
              setApprovalReason,
              (approval, decision) => void decide(approval, decision)
            )
          : null}

        {activePanel === "artifacts" && hasGeneratedFiles ? (
          <ArtifactHubPanel
            artifacts={conversationArtifacts}
            api={api}
            apiBaseUrl={settings.apiBaseUrl}
            userId={session?.user_id || ""}
            loading={artifactsLoading}
            onRefresh={() => {
              if (selectedTask?.conversation_id) {
                setArtifactLoading(true);
                api
                  .listConversationArtifacts(selectedTask.conversation_id)
                  .then((items) => setConversationArtifacts(items))
                  .catch(() => setConversationArtifacts([]))
                  .finally(() => setArtifactLoading(false));
              }
            }}
          />
        ) : null}

      </aside>
      </main>
      )}

      {showWorkspaceFiles && currentWorkspace ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setShowWorkspaceFiles(false)}>
          <div onMouseDown={(event) => event.stopPropagation()}>
            <WorkspaceFilesPanel
              workspaceName={currentWorkspace.name}
              files={workspaceFiles}
              loading={workspaceFilesLoading}
              onClose={() => setShowWorkspaceFiles(false)}
              onRefresh={() => {
                setWorkspaceFilesLoading(true);
                void api
                  .listWorkspaceFiles(currentWorkspace.space_id)
                  .then((items) => setWorkspaceFiles(items))
                  .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取工作区文件"))
                  .finally(() => setWorkspaceFilesLoading(false));
              }}
              onOpenFile={(file) => {
                void window.assistantDesktop.openExternal(api.workspaceFileDownloadUrl(file));
              }}
            />
          </div>
        </div>
      ) : null}

      {createTaskOpen ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setCreateTaskOpen(false)}>
          <section className="create-task-modal" role="dialog" aria-modal="true" aria-labelledby="create-task-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div>
                <span className="eyebrow">新建会话</span>
                <h2 id="create-task-title">想让贾维斯帮你做什么？</h2>
                <p>直接描述目标，再选择一种协助能力。无需输入命令。</p>
              </div>
              <button className="icon-button" aria-label="关闭新建会话" onClick={() => setCreateTaskOpen(false)}><Icon name="x" /></button>
            </header>
            <div className="create-task-content">
              <textarea
                autoFocus
                value={inputText}
                onChange={(event) => setInputText(event.target.value)}
                placeholder="例如：整理这份会议纪要，列出待办并生成邮件草稿"
              />
              <fieldset className="capability-picker light-capability-picker">
                <legend>选择协助能力</legend>
                <p>每个选项都对应已授权的协助方式；点选即可使用，无需手动输入命令。</p>
                <div className="capability-choice-list" role="group" aria-label="选择新会话协助能力">
                  {createTaskCommands.map((command) => (
                    <button
                      type="button"
                      className={selectedCommandId === command.command_id ? "selected capability-choice" : "capability-choice"}
                      aria-pressed={selectedCommandId === command.command_id}
                      disabled={!command.selectable}
                      onClick={() => selectNewTaskCommand(command)}
                      key={command.command_id}
                    >
                      <Icon name={selectedCommandId === command.command_id ? "check" : "sparkles"} size={17} />
                      <span><strong>{commandPresentation(command).label}</strong><small>{commandPresentation(command).description}</small></span>
                      {!command.selectable ? <em>暂不可用</em> : null}
                    </button>
                  ))}
                </div>
              </fieldset>
              <p className="task-type-hint">
                {selectedCommandCopy
                  ? `已选择：${selectedCommandCopy.label}。${selectedCommandCopy.description}`
                  : "请选择一种协助能力后开始新会话。"}
              </p>
            </div>
            <footer>
              <button className="secondary-action" onClick={() => setCreateTaskOpen(false)}>取消</button>
              <button
                className="primary-action"
                disabled={!inputText.trim() || !selectedCommandId || !selectedSpaceFilter || connection !== "connected" || !hasConfiguredUser || createTaskPending}
                onClick={() => void createTask()}
              >
                <Icon name="plus" size={16} />{createTaskPending ? "创建中…" : "开始新会话"}
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {settingsOpen ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setSettingsOpen(false)}>
          <section
            className="settings-modal split-settings-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="settings-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            {/* 左侧分栏导航 (Navigation Rail) */}
            <aside className="settings-nav-rail">
              <div className="settings-nav-header">
                <Icon name="settings" size={16} />
                <span>设置与偏好</span>
              </div>
              <nav className="settings-nav-list" role="tablist" aria-label="设置分类">
                <button
                  type="button"
                  role="tab"
                  aria-selected={settingsCategory === "general"}
                  className={settingsCategory === "general" ? "settings-nav-item active" : "settings-nav-item"}
                  onClick={() => setSettingsCategory("general")}
                >
                  <Icon name="settings" size={15} />
                  <span>通用与连接</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={settingsCategory === "capabilities"}
                  className={settingsCategory === "capabilities" ? "settings-nav-item active" : "settings-nav-item"}
                  onClick={() => setSettingsCategory("capabilities")}
                >
                  <Icon name="sparkles" size={15} />
                  <span>协助能力</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={settingsCategory === "memory"}
                  className={settingsCategory === "memory" ? "settings-nav-item active" : "settings-nav-item"}
                  onClick={() => setSettingsCategory("memory")}
                >
                  <Icon name="shield" size={15} />
                  <span>记忆与知识库</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={settingsCategory === "security"}
                  className={settingsCategory === "security" ? "settings-nav-item active" : "settings-nav-item"}
                  onClick={() => setSettingsCategory("security")}
                >
                  <Icon name="check" size={15} />
                  <span>安全与审批</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={settingsCategory === "notifications"}
                  className={settingsCategory === "notifications" ? "settings-nav-item active" : "settings-nav-item"}
                  onClick={() => setSettingsCategory("notifications")}
                >
                  <Icon name="clock" size={15} />
                  <span>通知与偏好</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={settingsCategory === "about"}
                  className={settingsCategory === "about" ? "settings-nav-item active" : "settings-nav-item"}
                  onClick={() => setSettingsCategory("about")}
                >
                  <Icon name="activity" size={15} />
                  <span>关于与身份</span>
                </button>
              </nav>
            </aside>

            {/* 右侧主内容区域 (Content Pane) */}
            <div className="settings-content-pane">
              <header className="settings-pane-header">
                <div>
                  <span className="eyebrow">{categoryEyebrow(settingsCategory)}</span>
                  <h2 id="settings-title">{categoryTitle(settingsCategory)}</h2>
                  <p>{categoryDescription(settingsCategory)}</p>
                </div>
                <button className="icon-button" aria-label="关闭设置" onClick={() => setSettingsOpen(false)}>
                  <Icon name="x" size={16} />
                </button>
              </header>

              <div className="settings-pane-body">
                {settingsCategory === "general" && (
                  <div className="settings-section">
                    <label>
                      本地服务地址
                      <input
                        value={draftSettings.apiBaseUrl}
                        onChange={(event) => setDraftSettings({ ...draftSettings, apiBaseUrl: event.target.value })}
                        placeholder="例如：http://127.0.0.1:18080"
                      />
                      <small className="field-hint">本地 Agent 服务的通信接入点</small>
                    </label>
                    <label>
                      默认本机工作目录
                      <input
                        value={draftSettings.defaultWorkdir}
                        onChange={(event) => setDraftSettings({ ...draftSettings, defaultWorkdir: event.target.value })}
                        placeholder="可选，用于需要访问本机文件的任务"
                      />
                      <small className="field-hint">启动任务时默认访问的本机工作文件夹或文档目录</small>
                    </label>
                  </div>
                )}

                {settingsCategory === "capabilities" && (
                  <div className="settings-section-stack">
                    <EffectiveCapabilitiesPanel
                      profile={myCapabilities}
                      status={capabilityLoadStatus}
                      selectedSkillNames={selectedSkillNames}
                      onSkillSelectionChange={setSelectedSkillNames}
                    />

                    <div className="settings-section capability-preferences-section">
                      <header className="settings-subsection-header">
                        <div>
                          <span className="eyebrow">仅影响这台设备</span>
                          <h3>本机使用偏好</h3>
                          <p>本机使用偏好不会扩大你的企业权限。</p>
                        </div>
                      </header>

                      <div className="settings-grid">
                        <label>
                          默认协助能力模式
                          <select
                            value={draftSettings.defaultCommandId || ""}
                            onChange={(event) => setDraftSettings({ ...draftSettings, defaultCommandId: event.target.value })}
                          >
                            <option value="">按当前任务自动选择（默认）</option>
                            {availableOfficeCommands.map((cmd) => (
                              <option key={cmd.command_id} value={cmd.command_id}>
                                {cmd.label} ({cmd.command_id})
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          回复模型运行模式
                          <select
                            value={draftSettings.defaultModelClass}
                            onChange={(event) => setDraftSettings({ ...draftSettings, defaultModelClass: event.target.value as DesktopSettings["defaultModelClass"] })}
                          >
                            <option value="light">轻量快速交互（推荐，低延迟）</option>
                            <option value="standard">标准深度推理</option>
                          </select>
                        </label>
                      </div>

                      <div className="capability-preview-list">
                        <span className="capability-list-heading">可选的本机协助模式（可在输入框输入 / 快捷调用）：</span>
                        <div className="capability-preview-grid">
                          {availableOfficeCommands.map((cmd) => (
                            <div key={cmd.command_id} className="capability-preview-item">
                              <div className="capability-token-chip">/{cmd.command_id}</div>
                              <div className="capability-info">
                                <strong>{cmd.label}</strong>
                                <small>{cmd.description}</small>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {settingsCategory === "memory" && (
                  <div className="settings-section memory-settings-section">
                    <MemoryKnowledgePanel api={api} hasUser={hasConfiguredUser} />
                  </div>
                )}

                {settingsCategory === "security" && (
                  <div className="settings-section">
                    <label>
                      高风险操作审批策略
                      <select
                        value={draftSettings.approvalPolicy}
                        onChange={(event) => setDraftSettings({ ...draftSettings, approvalPolicy: event.target.value as DesktopSettings["approvalPolicy"] })}
                      >
                        <option value="ask">由我确认（执行工具或修改文件前在右侧逐项确认）</option>
                        <option value="require_high_risk">仅高风险操作（只拦截高危写入与系统执行）</option>
                        <option value="read_only">只读模式（禁止任何写操作与高危工具）</option>
                      </select>
                    </label>
                    <div className="security-policy-card">
                      <Icon name="shield" size={16} />
                      <div>
                        <strong>当前策略：{approvalPolicyLabel(draftSettings.approvalPolicy)}</strong>
                        <p>{approvalPolicyDescription(draftSettings.approvalPolicy)}</p>
                      </div>
                    </div>
                  </div>
                )}

                {settingsCategory === "notifications" && (
                  <div className="settings-section">
                    <div className="settings-checkbox-group">
                      <label className="settings-checkbox-item">
                        <input
                          type="checkbox"
                          checked={draftSettings.enableNotifications !== false}
                          onChange={(event) => setDraftSettings({ ...draftSettings, enableNotifications: event.target.checked })}
                        />
                        <div>
                          <strong>桌面系统原生通知提醒</strong>
                          <small>任务执行完毕、等待审批确认或收到定时提醒时触发系统原生 Notification 弹窗</small>
                        </div>
                      </label>
                      <label className="settings-checkbox-item">
                        <input
                          type="checkbox"
                          checked={draftSettings.enableAutoMemory !== false}
                          onChange={(event) => setDraftSettings({ ...draftSettings, enableAutoMemory: event.target.checked })}
                        />
                        <div>
                          <strong>智能记忆与工作偏好自动沉淀</strong>
                          <small>在日常会话与任务解决过程中自动归纳个人习惯与领域知识，跨会话持续生效</small>
                        </div>
                      </label>
                    </div>
                  </div>
                )}

                {settingsCategory === "about" && (
                  <div className="settings-section">
                    <div className="about-identity-card">
                      <div className="identity-avatar large">{session?.display_name ? session.display_name.slice(0, 1).toUpperCase() : "U"}</div>
                      <div className="about-details">
                        <h3>{session?.display_name || "当前用户"}</h3>
                        <p>{session ? `${departmentLabel(session)} · ${roleLabel(session.roles)}` : "已登录"}</p>
                        {session ? <span className="identity-responsibility-badge">{identityResponsibilityLabel(session.roles)}</span> : null}
                        <small>租户：{session?.tenant_id || "local"} · 用户ID：{session?.user_id || "u-local"}</small>
                      </div>
                    </div>
                    <div className="about-system-card">
                      <div className="system-metric-row">
                        <span>客户端形态</span>
                        <strong>Governed Desktop Workstation (Electron Native)</strong>
                      </div>
                      <div className="system-metric-row">
                        <span>连接状态</span>
                        <strong>{connection === "connected" ? "服务正常运行中" : "未连接"}</strong>
                      </div>
                      <div className="system-metric-row">
                        <span>版本信息</span>
                        <strong>v0.1.0</strong>
                      </div>
                    </div>
                  </div>
                )}
              </div>

              <footer className="settings-pane-footer">
                {error ? <p className="settings-error">{userFacingMessage(error)}</p> : null}
                <div className="settings-actions">
                  <button className="secondary-action" onClick={() => setSettingsOpen(false)}>取消</button>
                  <button className="primary-action" onClick={() => void saveSettings()}>
                    <Icon name="check" size={16} />保存并应用设置
                  </button>
                </div>
              </footer>
            </div>
          </section>
        </div>
      ) : null}

      {showCreateSpaceModal ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setShowCreateSpaceModal(false)}>
          <section
            className="modal-card space-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-space-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="modal-header">
              <div className="modal-title-group">
                <Icon name="folder" size={16} />
                <h3 id="create-space-title">新建工作空间</h3>
              </div>
              <button
                type="button"
                className="icon-button modal-close"
                onClick={() => setShowCreateSpaceModal(false)}
                aria-label="关闭"
              >
                <Icon name="x" size={15} />
              </button>
            </header>
            <form onSubmit={(e) => void handleCreateSpace(e)} className="modal-form">
              <label className="form-field">
                <span>空间名称 <strong className="required-star">*</strong></span>
                <input
                  value={newSpaceName}
                  onChange={(e) => setNewSpaceName(e.target.value)}
                  placeholder="例如：产品研发空间、市场运营组…"
                  required
                  autoFocus
                />
              </label>
              <label className="form-field">
                <span>空间描述（可选）</span>
                <textarea
                  value={newSpaceDesc}
                  onChange={(e) => setNewSpaceDesc(e.target.value)}
                  placeholder="简要说明此空间的用途与协作范围…"
                  rows={3}
                />
              </label>
              <footer className="modal-actions">
                <button
                  type="button"
                  className="secondary-action"
                  onClick={() => setShowCreateSpaceModal(false)}
                  disabled={createSpacePending}
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="primary-action"
                  disabled={createSpacePending || !newSpaceName.trim()}
                >
                  {createSpacePending ? "创建中…" : "创建空间"}
                </button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}

      {showRemindersModal ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setShowRemindersModal(false)}>
          <section
            className="modal-card wide-modal workspace-submodal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="reminders-modal-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="modal-header">
              <div className="modal-title-group">
                <Icon name="clock" size={16} />
                <h3 id="reminders-modal-title">提醒中心与日程跟踪</h3>
              </div>
              <button
                type="button"
                className="icon-button modal-close"
                onClick={() => setShowRemindersModal(false)}
                aria-label="关闭"
              >
                <Icon name="x" size={15} />
              </button>
            </header>
            <div className="modal-scrollable-body">
              <RemindersPanel
                api={api}
                notifications={notifications}
                onRefreshNotifications={() => {
                  if (api.hasUserId) {
                    api.pollNotifications().then((items) => setNotifications(items)).catch(() => {});
                  }
                }}
              />
            </div>
          </section>
        </div>
      ) : null}

      {showCapabilityRequestModal ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setShowCapabilityRequestModal(false)}>
          <section
            className="modal-card wide-modal workspace-submodal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="capability-request-modal-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="modal-header">
              <div className="modal-title-group">
                <Icon name="sparkles" size={16} />
                <h3 id="capability-request-modal-title">向 IT 申请能力</h3>
              </div>
              <button
                type="button"
                className="icon-button modal-close"
                onClick={() => setShowCapabilityRequestModal(false)}
                aria-label="关闭"
              >
                <Icon name="x" size={15} />
              </button>
            </header>
            <div className="modal-scrollable-body">
              <CapabilityRequestPanel
                requests={capabilityRequests}
                status={capabilityRequestLoadStatus}
                relatedTaskId={selectedTask?.task_id || null}
                onSubmit={submitCapabilityRequest}
              />
            </div>
          </section>
        </div>
      ) : null}

      <CommandPaletteModal
        isOpen={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        items={paletteItems}
      />

      {taskContextMenu ? (
        <div
          className="custom-context-menu"
          style={{ top: taskContextMenu.y, left: taskContextMenu.x }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="context-menu-item danger"
            onClick={(e) => {
              const t = taskContextMenu.task;
              setTaskContextMenu(null);
              void handleDeleteTask(t, e);
            }}
          >
            <Icon name="trash" size={14} />
            <span>删除此会话</span>
          </button>
          <button
            type="button"
            className="context-menu-item"
            onClick={() => {
              void navigator.clipboard.writeText(taskContextMenu.task.input_text);
              setTaskContextMenu(null);
            }}
          >
            <Icon name="more" size={14} />
            <span>复制会话内容</span>
          </button>
        </div>
      ) : null}
    </>
  );
}


function AuthLoading(): JSX.Element {
  return (
    <main className="auth-shell">
      <section className="auth-card auth-loading" aria-live="polite">
        <img className="guidance-mark" src={brandMark} alt="贾维斯助理" />
        <span className="guidance-kicker">安全工作区</span>
        <h1>正在恢复登录状态</h1>
        <p>正在从本机安全存储读取会话，并向服务端确认当前身份。</p>
      </section>
    </main>
  );
}

type AuthGateProps = {
  initializationRequired: boolean;
  busy: boolean;
  error: string;
  onLogin: (loginName: string, password: string) => Promise<void>;
};

function AuthGate({ initializationRequired, busy, error, onLogin }: AuthGateProps): JSX.Element {
  const [loginName, setLoginName] = useState("");
  const [password, setPassword] = useState("");
  const [localError, setLocalError] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setLocalError("");
    if (!loginName.trim() || !password) {
      setLocalError("请输入登录名和密码。");
      return;
    }
    try {
      await onLogin(loginName.trim(), password);
    } catch {
      // The parent keeps the bounded server error in the gate; no raw response is shown here.
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-title">
        <img className="guidance-mark" src={brandMark} alt="贾维斯助理" />
        <span className="guidance-kicker">企业工作区</span>
        <h1 id="auth-title">登录贾维斯助理</h1>
        {initializationRequired ? (
          <p>本工作区尚未开通账号。账号由企业管理员在管理端创建并邀请，暂不支持自助注册。</p>
        ) : (
          <p>使用企业分配给你的登录名进入工作区。你的部门和可用能力由服务端决定。</p>
        )}
        <form className="auth-form" onSubmit={(event) => void submit(event)}>
          <label>登录名<input value={loginName} onChange={(event) => setLoginName(event.target.value)} autoComplete="username" placeholder="例如：zhangsan" /></label>
          <label>密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="请输入密码" /></label>
          {localError || error ? <p className="auth-error">{localError || userFacingMessage(error)}</p> : null}
          <button className="primary-action auth-submit" type="submit" disabled={busy || initializationRequired}>
            {busy ? "正在验证…" : "登录工作区"}
          </button>
        </form>
        <div className="auth-note"><Icon name="shield" size={15} />账号由企业管理员统一创建和分发；会话由桌面端主进程安全保存，网页渲染层不会接触原始会话凭据。</div>
      </section>
    </main>
  );
}

function renderTimelinePanel(
  events: LocalEvent[],
  task: Task | null,
  tokenStats: ConversationTokenStats | null,
  workSummary: ConversationWorkSummary | null,
  conversationMessages: ConversationMessage[],
  approvals: Approval[],
  approvalReason: string,
  onApprovalReasonChange: (value: string) => void,
  onDecide: (approval: Approval, decision: "approve" | "reject") => void
): JSX.Element {
  const progress = task ? taskProgressPresentation(events, task) : null;
  return (
    <section className="timeline-panel">
      {renderConversationInfoCard(tokenStats, workSummary, conversationMessages)}
      {renderPendingApprovals(
        approvals,
        approvalReason,
        onApprovalReasonChange,
        onDecide
      )}
      {renderPlanProgressCard(events, task)}
      {!progress && task ? (
        <div className="timeline-empty">
          <strong>{formatStatus(task.status)}</strong>
          <p>{planProgressCopy(task.status)}</p>
        </div>
      ) : null}
    </section>
  );
}

function renderConversationInfoCard(
  stats: ConversationTokenStats | null,
  workSummary: ConversationWorkSummary | null,
  conversationMessages: ConversationMessage[],
): JSX.Element | null {
  if (!stats && !workSummary) return null;
  const usage = stats ? conversationUsagePresentation(stats) : null;
  const displayedMessageCount = conversationMessages.length || stats?.message_count || 0;
  const displayedUserMessageCount = conversationMessages.filter((message) => message.role === "user").length;
  const displayedAssistantMessageCount = conversationMessages.filter((message) => message.role === "assistant").length;
  return (
    <article className={`token-card conversation-info-card ${stats?.status || ""}`}>
      <header>
        <div>
          <strong>会话信息</strong>
          <span>当前完整对话的消息与内容用量</span>
        </div>
        {usage ? <span className="conversation-usage-status">{usage.statusLabel}</span> : null}
      </header>

      {stats && usage ? (
        <>
          <div className="conversation-usage-summary">
            <div>
              <span>模型 Token 已用</span>
              <strong>{formatTokenCount(usage.usedTokens)}</strong>
            </div>
            <span>{usage.percentage}%</span>
          </div>
          <div
            className="token-meter"
            role="progressbar"
            aria-label="会话内容 Token 使用比例"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={usage.percentage}
          >
            <span style={{ width: `${usage.percentage}%` }} />
          </div>
          <div className="conversation-info-metrics">
            <div><span>消息</span><strong>{displayedMessageCount} 条</strong></div>
            <div><span>输入用量</span><strong>{formatTokenCount(stats.used_input_tokens)}</strong></div>
            <div><span>输出用量</span><strong>{formatTokenCount(stats.used_output_tokens)}</strong></div>
            <div><span>可用预算</span><strong>{formatTokenCount(usage.remainingTokens)}</strong></div>
          </div>
          <p className="conversation-usage-note">我 {displayedUserMessageCount} 条 · 贾维斯 {displayedAssistantMessageCount} 条 · 上限 {formatTokenCount(usage.limitTokens)}</p>
        </>
      ) : null}

      {workSummary ? (
        <div className="conversation-work-metrics" aria-label="会话工作信息">
          <span><Icon name="folder" size={12} />{workSummary.scope_name === "Personal Work" ? "个人工作空间" : workSummary.scope_name}</span>
          <span>资料 {workSummary.context_count}</span>
          <span>产物 {workSummary.artifact_count}</span>
          {workSummary.pending_approval_count ? <span>待确认 {workSummary.pending_approval_count}</span> : null}
        </div>
      ) : null}
    </article>
  );
}

function renderPendingApprovals(
  approvals: Approval[],
  approvalReason: string,
  onApprovalReasonChange: (value: string) => void,
  onDecide: (approval: Approval, decision: "approve" | "reject") => void
): JSX.Element | null {
  if (!approvals.length) return null;

  return (
    <section className="pending-approvals" aria-label="等待你确认">
      <header>
        <div>
          <strong>等待你确认</strong>
          <small>请确认是否继续这些重要步骤。</small>
        </div>
      </header>
      <textarea
        value={approvalReason}
        onChange={(event) => onApprovalReasonChange(event.target.value)}
        placeholder="可选：写下你的想法…"
      />
      {approvals.map((approval) => (
        <article key={approval.approval_id} className="approval-card">
          <header>
            <strong>请确认这一步</strong>
            <span className="risk-level">
              {approvalRiskLabel(String((approval as Approval & { risk_level?: string }).risk_level || "high"))}
            </span>
          </header>
          <p>{approval.request_summary || approval.subject}</p>
          <div className="approval-actions">
            <button className="primary-action" onClick={() => onDecide(approval, "approve")}>
              <Icon name="check" size={16} />确认执行
            </button>
            <button className="secondary-action danger-action" onClick={() => onDecide(approval, "reject")}>
              <Icon name="x" size={16} />暂不执行
            </button>
          </div>
        </article>
      ))}
    </section>
  );
}


function renderSystemNotifications(events: LocalEvent[], task: Task | null): JSX.Element | null {
  const lifecycleTypes = new Set([
    "task.completed",
    "task.failed",
    "task.waiting_approval",
    "approval.required",
    "task.tool.requested"
  ]);
  const failure = taskFailurePresentation(events, task);
  const notifications = events
    .filter(
      (event) =>
        lifecycleTypes.has(eventType(event)) &&
        !(failure && eventType(event) === "task.failed")
    )
    .map((event) => ({
      id: event.event_id,
      label: notificationLabel(eventType(event)),
      text: eventNotificationText(event),
      status: eventType(event).includes("failed") ? "failed" : "success",
      timestamp: formatEventTime(event.created_at)
    }));

  if (task?.result_text && taskResultKind(task.task_type) === "notification") {
    notifications.push({
      id: "task-result",
      label: "状态查询结果",
      text: task.result_text,
      status: "success",
      timestamp: formatEventTime(task.updated_at)
    });
  }
  if (failure) {
    notifications.push({
      id: "task-failure",
      label: failure.title,
      text: failure.message,
      status: "failed",
      timestamp: formatEventTime(task?.updated_at || "")
    });
  } else if (task?.error_message) {
    notifications.push({
      id: "task-error",
      label: "处理时未完成",
      text: userFacingMessage(task.error_message),
      status: "failed",
      timestamp: formatEventTime(task.updated_at)
    });
  }

  if (!notifications.length) return null;
  return (
    <section className="system-notifications" aria-label={TASK_CONSOLE_COPY.notificationsEyebrow} aria-live="polite">
      <header>
        <span className="eyebrow">{TASK_CONSOLE_COPY.notificationsEyebrow}</span>
        <strong>{TASK_CONSOLE_COPY.notificationsHeading}</strong>
      </header>
      <div>
        {notifications.map((notification) => (
          <article className={`system-notification ${notification.status}`} key={notification.id}>
            <strong>{notification.label}</strong>
            {notification.text ? <p>{notification.text}</p> : null}
            <small>{notification.timestamp}</small>
          </article>
        ))}
      </div>
    </section>
  );
}


function activeMentionQuery(value: string): string | null {
  const match = value.match(/(?:^|\s)@([^\s@]{0,200})$/);
  return match ? (match[1] ?? "") : null;
}

function replaceMentionToken(value: string, displayName: string): string {
  const visibleName = displayName.trim().replace(/\s+/g, "_").slice(0, 80);
  return value.replace(
    /(^|\s)@[^\s@]{0,200}$/,
    (_match, prefix: string) => `${prefix}@${visibleName} `
  );
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function eventType(event: LocalEvent): string {
  return event.normalized_type || event.type;
}

function eventNotificationText(event: LocalEvent): string | null {
  const value = event.payload.text || event.payload.message || event.payload.error || event.payload.summary;
  return typeof value === "string" && value.trim() ? value : null;
}

function eventActionLabel(event: LocalEvent): string {
  const payload = event.payload;
  switch (event.type) {
    case "task.started":
      return "已开始处理";
    case "task.completed":
      return "已完成";
    case "task.failed":
      return "暂时没完成，可稍后再试";
    case "task.message.delta":
      return "正在整理回复";
    case "task.message.completed":
      return "有新的回复";
    case "task.plan.created":
      return "已安排处理步骤";
    case "plan":
      return "已安排处理步骤";
    case "task.action.started":
      return "正在按步骤处理";
    case "task.action.completed":
      return "步骤处理完毕";
    case "task.action.failed":
      return "这一步暂时没完成";
    case "task.log.appended":
      return String(payload.message || payload.text || "有新的处理记录");
    case "task.tool.requested":
      return `等待你确认：${String(payload.tool_name || payload.subject || "一项重要操作")}`;
    default:
      if (payload.tool_name) return `正在处理：${String(payload.tool_name)}`;
      if (payload.command) return "正在处理一项操作";
      if (payload.path) return "正在处理文件";
      return "进展已更新";
  }
}

function formatEventTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", { hour12: false });
}


function commandPresentation(command: OfficeCommand): { label: string; description: string } {
  const option = TASK_TYPE_OPTIONS.find((item) => item.value === command.command_id);
  return option ? { label: option.label, description: option.detail } : {
    label: command.label,
    description: command.description
  };
}

function renderPlanProgressCard(events: LocalEvent[], task: Task | null): JSX.Element | null {
  if (!task) return null;
  const progress = taskProgressPresentation(events, task);
  if (!progress) return null;
  return (
    <article className={`plan-progress-card ${task.status}`}>
      <header>
        <div>
          <strong>{TASK_CONSOLE_COPY.plan}</strong>
          <small>
            共 {progress.steps.length} 步 · {formatEventTime(progress.createdAt)} 已安排
            {progress.currentPhase ? ` · 当前：${progress.currentPhase}` : ""}
          </small>
        </div>
        <span className={`status ${task.status}`}>{formatStatus(task.status)}</span>
      </header>
      <ol>
        {progress.steps.map((step, index) => (
          <li
            key={`${index}-${step.title}`}
            className={step.status}
            aria-current={step.status === "running" ? "step" : undefined}
          >
            <span>
              {step.status === "completed" ? (
                <Icon name="check" size={14} />
              ) : step.status === "failed" ? (
                <Icon name="x" size={14} />
              ) : step.status === "running" ? (
                <Icon name="activity" size={14} />
              ) : (
                index + 1
              )}
            </span>
            <p>{step.title}</p>
          </li>
        ))}
      </ol>
      <p className="plan-progress-copy" role={progress.failure ? "alert" : "status"}>
        {progress.summary}
      </p>
    </article>
  );
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
    session.adapter || "未知来源",
    session.conversation_id || "未关联对话",
    session.sender_id || "未知发送者",
    session.intent_outcome || session.reason
  ]
    .filter(Boolean)
    .join(" · ");
}
