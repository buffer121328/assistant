import type { CapabilityRequest } from "./capability-requests";

export type DesktopSettings = {
  apiBaseUrl: string;
  defaultWorkdir: string;
  defaultModelClass: "light" | "standard";
  approvalPolicy: "ask" | "require_high_risk" | "read_only";
  defaultCommandId?: string;
  enableNotifications?: boolean;
  enableAutoMemory?: boolean;
};

export type AuthenticatedSession = {
  expires_at: string;
  tenant_id: string;
  user_id: string;
  display_name: string;
  organization_ids: string[];
  organization_names?: string[];
  roles: string[];
  authority_revision: number;
};

export type LocalTaskType = "plan" | "learn" | "daily" | "office" | "memory" | "status" | "screen";

export type OfficeCommand = {
  command_id: string;
  label: string;
  description: string;
  selectable: boolean;
};

export type TaskStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "success"
  | "failed"
  | "cancelled";

export type Task = {
  task_id: string;
  user_id: string;
  platform: string;
  task_type: string;
  input_text: string;
  status: TaskStatus;
  workflow_key: string | null;
  model_class: string | null;
  conversation_id: string | null;
  result_text: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type ConversationMessage = {
  message_id: string;
  conversation_id: string;
  task_id: string | null;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

export type LocalEvent = {
  event_id: string;
  task_id: string;
  type: string;
  normalized_type?: string;
  run_id?: string | null;
  created_at: string;
  sequence: number;
  payload: Record<string, unknown>;
};

export type Approval = {
  approval_id: string;
  task_id: string;
  tool_name: string;
  approval_type: "tool" | "plan" | "review" | "change";
  subject: string;
  request_summary: string | null;
  capability_key: string | null;
  policy_decision: string | null;
  allowed_decisions: string[];
  expires_at: string | null;
  status: string;
  decided_by_user_id: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Health = {
  service_name: string;
  status: "ok";
};

export type LocalConfig = {
  service_name: string;
  app_env: string;
  local_api_auth_required: boolean;
  features: Record<string, boolean | string>;
};

export type EffectiveCapability = {
  id: string;
  display_name: string;
  summary: string;
};
export type AuthorizedSkill = {
  name: string;
  display_name: string;
  source: string;
  status: string;
  selectable: boolean;
};

export type MyCapabilities = {
  schema_version: string;
  authority_revision: number;
  capabilities: string[];
  capability_details: EffectiveCapability[];
  tools: string[];
  knowledge_scopes: string[];
  memory_access: string[][];
  skills?: AuthorizedSkill[];
};


export type ConversationTokenStats = {
  conversation_id: string;
  message_count: number;
  user_message_count: number;
  assistant_message_count: number;
  total_estimated_tokens: number;
  user_estimated_tokens: number;
  assistant_estimated_tokens: number;
  token_limit: number;
  used_input_tokens: number;
  used_output_tokens: number;
  used_total_tokens: number;
  reserved_input_tokens: number;
  reserved_output_tokens: number;
  reserved_total_tokens: number;
  remaining_tokens: number;
  available_tokens: number;
  blocked_reason: string | null;
  usage_ratio: number;
  status: "ok" | "warning" | "full";
};

export type ResourceReference = {
  reference_id: string;
  conversation_id: string;
  resource_kind: string;
  provider: string;
  display_name: string;
  version: string;
  content_hash: string | null;
  size_bytes: number;
  user_id: string;
  tenant_id: string;
  organization_id: string | null;
  owner_type: string;
  owner_id: string;
  visibility: string;
  sensitivity: string;
  status: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
  last_resolved_at: string | null;
};

export type Space = {
  space_id: string;
  name: string;
  description: string;
  status: "active" | "archived";
  role: "owner" | "editor" | "viewer";
};

export type WorkspaceFile = {
  file_id: string;
  source_kind: "uploaded" | "generated";
  display_name: string;
  media_type: string | null;
  size_bytes: number;
  conversation_id: string;
  conversation_title: string;
  created_at: string;
  updated_at: string;
};

export type ConversationWorkSummary = {
  conversation_id: string;
  scope_kind: "personal" | "space";
  scope_name: string;
  space_id: string | null;
  context_count: number;
  artifact_count: number;
  pending_approval_count: number;
};

export type LocalArtifact = {
  artifact_id: string;
  tenant_id: string;
  organization_id: string | null;
  owner_type: string;
  owner_id: string;
  conversation_id: string;
  task_id: string;
  run_id: string | null;
  filename: string;
  media_type: string;
  size_bytes: number;
  content_hash: string | null;
  version: number;
  generation_method: string;
  source_reference_versions?: string[];
  visibility: string;
  sensitivity: string;
  lifecycle_state: "active" | "archived" | "revoked";
  publication_state?: string;
  retention_state?: string;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
  revoked_at?: string | null;
};

export type MemoryItem = {
  id: string;
  user_id: string;
  category: string;
  fact: string;
  status: "active" | "archived" | "deleted";
  source_task_id?: string | null;
  created_at: string;
  updated_at: string;
};

export type MemoryOverview = {
  total_memories: number;
  categories: Record<string, number>;
  recent_facts: string[];
};

export type KnowledgeDocument = {
  id: string;
  title: string;
  source_type: string;
  summary: string;
  content_snippet?: string;
  created_at: string;
  updated_at: string;
};

export type ReminderItem = {
  reminder_id?: string;
  id?: string;
  user_id: string;
  task_id?: string | null;
  title: string;
  message?: string;
  due_at: string;
  channel?: string;
  status: "pending" | "completed" | "cancelled" | string;
  created_at?: string;
  cancelled_at?: string | null;
};

export type DesktopNotification = {
  id?: string;
  outbox_id?: string;
  reminder_id?: string;
  notification_type?: string;
  title: string;
  message?: string;
  body?: string;
  severity?: "info" | "warning" | "error";
  acknowledged?: boolean;
  due_at?: string;
  created_at?: string;
};

export type RemoteControlBridgeResponseTarget = {
  adapter: string;
  conversation_id: string;
  conversation_type: string;
};

export type RemoteControlBridgeSession = {
  bridge_id: string;
  platform: string;
  message_id: string;
  adapter: string | null;
  sender_id: string | null;
  conversation_id: string | null;
  conversation_type: string | null;
  message_text: string | null;
  intent_outcome: string | null;
  reason: string;
  task_id: string | null;
  task_status: string | null;
  response_target: RemoteControlBridgeResponseTarget | null;
  delivery_status: string | null;
  delivery_attempt_count: number;
  delivery_error_summary: string | null;
  delivery_result_json: string | null;
  created_at: string;
  updated_at: string;
};

export type RemoteControlBridgeReplay = {
  dispatch_status: string;
  message: string;
  session: RemoteControlBridgeSession;
};

type RequestOptions = {
  method?: string;
  body?: unknown;
};

/** Typed client for the backend's local-control and remote-bridge endpoints. */
export class LocalApiClient {
  constructor(
    private readonly settings: DesktopSettings,
    private readonly session: AuthenticatedSession | null
  ) {}

  get hasUserId(): boolean {
    return Boolean(this.userId);
  }

  private get userId(): string {
    return this.session?.user_id.trim() || "";
  }

  /** Check whether the local backend is reachable. */
  async health(): Promise<Health> {
    return this.request("/local/health");
  }

  /** Read the backend capabilities exposed to the desktop console. */
  async config(): Promise<LocalConfig> {
    return this.request("/local/config");
  }

  /** Resolve the employee's governed capabilities without exposing admin mutations. */
  async myCapabilities(): Promise<MyCapabilities> {
    return this.request(
      `/api/me/capabilities?user_id=${encodeURIComponent(this.userId)}`
    );
  }

  async listCapabilityRequests(): Promise<CapabilityRequest[]> {
    const response = await this.request<{ items: CapabilityRequest[] }>(
      "/api/me/capability-requests"
    );
    return response.items;
  }

  async createCapabilityRequest(payload: {
    title: string;
    description: string;
    related_task_id: string | null;
  }): Promise<CapabilityRequest> {
    return this.request("/api/me/capability-requests", { method: "POST", body: payload });
  }

  /** List tasks owned by the configured local user. */
  async listTasks(): Promise<Task[]> {
    const response = await this.request<{ items: Task[] }>(
      `/local/tasks?user_id=${encodeURIComponent(this.userId)}`
    );
    return response.items;
  }

  /** Create a task and return the server-created task record. */
  async officeCommandCatalog(): Promise<OfficeCommand[]> {
    const response = await this.request<{ items: OfficeCommand[] }>(
      `/local/commands/catalog?user_id=${encodeURIComponent(this.userId)}`
    );
    return response.items;
  }

  async createTask(
    inputText: string,
    taskType: LocalTaskType = "plan",
    commandId?: string,
    spaceId?: string,
    selectedSkillNames: string[] = []
  ): Promise<Task> {
    const response = await this.request<{ task: Task; queued: boolean }>("/local/tasks", {
      method: "POST",
      body: {
        user_id: this.userId,
        task_type: taskType,
        command_id: commandId,
        input_text: inputText,
        model_class: this.settings.defaultModelClass,
        space_id: spaceId,
        selected_skill_names: selectedSkillNames
      }
    });
    return response.task;
  }

  /** Append a user message to an existing task. */
  async appendMessage(
    taskId: string,
    content: string,
    resourceReferenceIds: string[] = [],
    commandId?: string
  ): Promise<Task> {
    const response = await this.request<{ task: Task; queued: boolean }>(
      `/local/tasks/${encodeURIComponent(taskId)}/messages`,
      {
        method: "POST",
        body: {
          user_id: this.userId,
          content,
          command_id: commandId,
          resource_reference_ids: resourceReferenceIds
        }
      }
    );
    return response.task;
  }

  /** List path-free references attached to one owned active Conversation. */
  async listConversationResources(conversationId: string): Promise<ResourceReference[]> {
    const response = await this.request<{ items: ResourceReference[] }>(
      `/local/conversations/${encodeURIComponent(conversationId)}/resources?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
    return response.items;
  }

  /** Attach one explicitly selected local file through the owner-scoped backend. */
  async attachConversationResource(
    conversationId: string,
    sourceRef: string
  ): Promise<ResourceReference> {
    return this.request(
      `/local/conversations/${encodeURIComponent(conversationId)}/resources`,
      {
        method: "POST",
        body: {
          user_id: this.userId,
          resource_kind: "local_file",
          source_ref: sourceRef,
          sensitivity: "normal",
          pinned: false
        }
      }
    );
  }

  /** Copy a user-selected desktop file into managed Conversation storage. */
  async importConversationResource(
    conversationId: string,
    sourceRef: string
  ): Promise<ResourceReference> {
    return this.request(
      `/local/conversations/${encodeURIComponent(conversationId)}/resources/import`,
      {
        method: "POST",
        body: {
          user_id: this.userId,
          resource_kind: "local_file",
          source_ref: sourceRef,
          sensitivity: "normal",
          pinned: false
        }
      }
    );
  }

  /** Upload one explicitly selected file through the backend-managed input boundary. */
  async uploadConversationResource(
    conversationId: string,
    file: File
  ): Promise<ResourceReference> {
    const response = await window.assistantDesktop.uploadResource(this.settings.apiBaseUrl, {
      path: `/local/conversations/${encodeURIComponent(conversationId)}/resources/upload`,
      userId: this.userId,
      name: file.name,
      type: file.type || "application/octet-stream",
      bytes: await file.arrayBuffer()
    });
    return this.readBridgeResponse<ResourceReference>(response);
  }

  /** Attach an authorized Artifact by its stable ID only. */
  async attachArtifactResource(
    conversationId: string,
    artifactId: string
  ): Promise<ResourceReference> {
    return this.request(
      `/local/conversations/${encodeURIComponent(conversationId)}/resources/artifact`,
      {
        method: "POST",
        body: { user_id: this.userId, artifact_id: artifactId, sensitivity: "normal", pinned: false }
      }
    );
  }

  /** Attach an opaque external-record identity without provider credentials. */
  async attachExternalRecordResource(
    conversationId: string,
    payload: {
      provider: string;
      externalResourceId: string;
      displayName: string;
      version?: string;
    }
  ): Promise<ResourceReference> {
    return this.request(
      `/local/conversations/${encodeURIComponent(conversationId)}/resources/external-record`,
      {
        method: "POST",
        body: {
          user_id: this.userId,
          provider: payload.provider,
          external_resource_id: payload.externalResourceId,
          display_name: payload.displayName,
          version: payload.version || null,
          sensitivity: "normal",
          pinned: false
        }
      }
    );
  }

  /** Soft-remove one exact Conversation reference without touching its source file. */
  async removeConversationResource(
    conversationId: string,
    referenceId: string
  ): Promise<ResourceReference> {
    return this.request(
      `/local/conversations/${encodeURIComponent(conversationId)}/resources/${encodeURIComponent(
        referenceId
      )}?user_id=${encodeURIComponent(this.userId)}`,
      { method: "DELETE" }
    );
  }

  /** Search owner-scoped mention candidates by safe display metadata. */
  async searchConversationMentions(
    conversationId: string,
    query: string
  ): Promise<ResourceReference[]> {
    const params = new URLSearchParams({
      user_id: this.userId,
      conversation_id: conversationId,
      query,
      limit: "20"
    });
    const response = await this.request<{ items: ResourceReference[] }>(
      `/local/mentions?${params.toString()}`
    );
    return response.items;
  }

  /** Fetch one task while preserving the owner-scoped query boundary. */
  async task(taskId: string): Promise<Task> {
    return this.request(
      `/local/tasks/${encodeURIComponent(taskId)}?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
  }

  /** Cancel an owned task that has not reached a terminal state. */
  async cancelTask(taskId: string): Promise<Task> {
    return this.request(
      `/local/tasks/${encodeURIComponent(taskId)}/cancel?user_id=${encodeURIComponent(
        this.userId
      )}`,
      { method: "POST" }
    );
  }

  /** Delete / archive an owned task. */
  async deleteTask(taskId: string): Promise<void> {
    await this.request(
      `/local/tasks/${encodeURIComponent(taskId)}?user_id=${encodeURIComponent(this.userId)}`,
      { method: "DELETE" }
    );
  }

  /** Read task events after an optional cursor for incremental hydration. */
  async events(taskId: string, afterEventId?: string): Promise<LocalEvent[]> {
    const cursor = afterEventId ? `&after_event_id=${encodeURIComponent(afterEventId)}` : "";
    const response = await this.request<{ items: LocalEvent[] }>(
      `/local/tasks/${encodeURIComponent(taskId)}/events?user_id=${encodeURIComponent(
        this.userId
      )}${cursor}`
    );
    return response.items;
  }

  /** Read the task's sanitized execution log entries. */
  async logs(taskId: string): Promise<LocalEvent[]> {
    const response = await this.request<{ items: LocalEvent[] }>(
      `/local/tasks/${encodeURIComponent(taskId)}/logs?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
    return response.items;
  }

  /** Read approval records associated with the selected task. */
  async approvals(taskId: string): Promise<Approval[]> {
    const response = await this.request<{ items: Approval[] }>(
      `/local/tasks/${encodeURIComponent(taskId)}/approvals?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
    return response.items;
  }

  /** Submit an approval decision and return the updated server state. */
  async decideApproval(
    taskId: string,
    approvalId: string,
    decision: "approve" | "reject",
    reason: string
  ): Promise<{ approval: Approval; task: Task; queued: boolean }> {
    return this.request(`/local/tasks/${encodeURIComponent(taskId)}/approvals/${approvalId}`, {
      method: "POST",
      body: {
        user_id: this.userId,
        decision,
        reason
      }
    });
  }


  /** Read token accounting for the selected conversation. */
  async conversationTokenStats(conversationId: string): Promise<ConversationTokenStats> {
    return this.request(
      `/local/conversations/${encodeURIComponent(conversationId)}/token-stats?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
  }

  /** List personal Workspaces owned by the current desktop user. */
  async listPersonalWorkspaces(): Promise<Space[]> {
    const response = await this.request<{ items: Space[] }>(
      `/local/workspaces?user_id=${encodeURIComponent(this.userId)}`
    );
    return response.items;
  }

  /** List active Spaces the configured user can currently access. */
  async listSpaces(): Promise<Space[]> {
    const response = await this.request<{ items: Space[] }>(
      `/api/spaces?user_id=${encodeURIComponent(this.userId)}`
    );
    return response.items;
  }

  /** List uploaded and generated files in one current personal Workspace. */
  async listWorkspaceFiles(workspaceId: string): Promise<WorkspaceFile[]> {
    const response = await this.request<{ items: WorkspaceFile[] }>(
      `/local/workspaces/${encodeURIComponent(workspaceId)}/files?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
    return response.items || [];
  }

  /** Build the server-authorized download URL for one Workspace file. */
  workspaceFileDownloadUrl(file: WorkspaceFile): string {
    const path = file.source_kind === "generated"
      ? `/local/artifacts/${encodeURIComponent(file.file_id)}/download`
      : `/local/workspace-files/${encodeURIComponent(file.file_id)}/download`;
    return `${this.settings.apiBaseUrl}${path}?user_id=${encodeURIComponent(this.userId)}`;
  }

  /** Create a new personal or collaborative Space. */
  async createSpace(name: string, description?: string): Promise<Space> {
    return this.request("/api/spaces", {
      method: "POST",
      body: {
        user_id: this.userId,
        name,
        description: description?.trim() || ""
      }
    });
  }

  /** List active conversations owned by the configured user. */
  async listConversations(): Promise<Array<{ conversation_id: string; title: string; space_id: string | null; created_at: string; updated_at: string }>> {
    const response = await this.request<{ items: Array<{ conversation_id: string; title: string; space_id: string | null; created_at: string; updated_at: string }> }>(
      `/api/conversations?user_id=${encodeURIComponent(this.userId)}`
    );
    return response.items;
  }

  /** Read the complete bounded message history for one owned Conversation. */
  async listConversationMessages(conversationId: string): Promise<ConversationMessage[]> {
    const response = await this.request<{ items: ConversationMessage[] }>(
      `/api/conversations/${encodeURIComponent(conversationId)}/messages?user_id=${encodeURIComponent(
        this.userId
      )}&limit=200`
    );
    return response.items;
  }

  /** Archive or delete a conversation. */
  async archiveConversation(conversationId: string): Promise<void> {
    await this.request(
      `/api/conversations/${encodeURIComponent(conversationId)}/archive`,
      {
        method: "POST",
        body: { user_id: this.userId }
      }
    );
  }

  /** Read a server-derived Personal Work or Space summary for one owned Conversation. */
  async conversationWorkSummary(conversationId: string): Promise<ConversationWorkSummary> {
    return this.request(
      `/local/conversations/${encodeURIComponent(conversationId)}/work-summary?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
  }

  /** Move an owned Conversation to an editable Space or back to Personal Work. */
  async updateConversationSpace(
    conversationId: string,
    spaceId: string | null
  ): Promise<ConversationWorkSummary> {
    return this.request(
      `/local/conversations/${encodeURIComponent(conversationId)}/space`,
      { method: "PATCH", body: { user_id: this.userId, space_id: spaceId } }
    );
  }

  /** List owner-authorized references from other Conversations in the active Space. */
  async listSpaceContextCandidates(conversationId: string): Promise<ResourceReference[]> {
    const response = await this.request<{ items: ResourceReference[] }>(
      `/local/conversations/${encodeURIComponent(conversationId)}/space-context?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
    return response.items;
  }

  /** Attach one selected same-owner, same-Space context candidate. */
  async attachSpaceContextCandidate(
    conversationId: string,
    referenceId: string
  ): Promise<ResourceReference> {
    return this.request(
      `/local/conversations/${encodeURIComponent(conversationId)}/space-context/${encodeURIComponent(
        referenceId
      )}`,
      { method: "POST", body: { user_id: this.userId } }
    );
  }

  /** List remote-control bridge deliveries, optionally narrowed to a conversation. */
  async bridgeSessions(limit = 20, conversationId?: string): Promise<RemoteControlBridgeSession[]> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (conversationId) {
      params.set("conversation_id", conversationId);
    }
    const response = await this.request<{ items: RemoteControlBridgeSession[] }>(
      `/api/remote-control/bridge/sessions?${params.toString()}`
    );
    return response.items;
  }

  /** Fetch the server snapshot for one bridge delivery. */
  async bridgeSession(messageId: string): Promise<RemoteControlBridgeSession> {
    return this.request(`/api/remote-control/bridge/sessions/${encodeURIComponent(messageId)}`);
  }

  /** Request a server-side replay of one bridge delivery. */
  async replayBridgeSession(messageId: string): Promise<RemoteControlBridgeReplay> {
    return this.request(`/api/remote-control/bridge/sessions/${encodeURIComponent(messageId)}/replay`, {
      method: "POST"
    });
  }

  /** List governed Artifacts generated for the active conversation. */
  async listConversationArtifacts(conversationId: string): Promise<LocalArtifact[]> {
    const response = await this.request<{ items: LocalArtifact[] }>(
      `/local/conversations/${encodeURIComponent(conversationId)}/artifacts?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
    return response.items || [];
  }

  /** Archive an artifact owned by the local user. */
  async archiveArtifact(artifactId: string): Promise<LocalArtifact> {
    return this.request(`/local/artifacts/${encodeURIComponent(artifactId)}/archive`, {
      method: "POST",
      body: { user_id: this.userId }
    });
  }

  /** Revoke normal access to an artifact while preserving audit logs. */
  async revokeArtifact(artifactId: string): Promise<LocalArtifact> {
    return this.request(`/local/artifacts/${encodeURIComponent(artifactId)}/revoke`, {
      method: "POST",
      body: { user_id: this.userId }
    });
  }

  /** Read the user's memory overview summary. */
  async memoriesOverview(): Promise<MemoryOverview> {
    return this.request(`/api/memories/overview?user_id=${encodeURIComponent(this.userId)}`);
  }

  /** List long-term memories matching an optional search query. */
  async listMemories(query?: string): Promise<MemoryItem[]> {
    const params = new URLSearchParams({ user_id: this.userId });
    if (query) params.set("query", query);
    const response = await this.request<{ items: MemoryItem[] }>(`/api/memories?${params.toString()}`);
    return response.items || [];
  }

  /** Record a new memory fact for the user. */
  async createMemory(fact: string, category = "general"): Promise<MemoryItem> {
    return this.request("/api/memories", {
      method: "POST",
      body: { user_id: this.userId, fact, category }
    });
  }

  /** Delete or archive a single memory fact. */
  async deleteMemory(memoryId: string): Promise<void> {
    await this.request(`/api/memories/${encodeURIComponent(memoryId)}/actions/delete`, {
      method: "POST",
      body: { user_id: this.userId }
    });
  }

  /** List documents in the organizational knowledge base. */
  async listKnowledgeDocuments(query?: string): Promise<KnowledgeDocument[]> {
    const params = new URLSearchParams({ user_id: this.userId });
    if (query) params.set("query", query);
    const response = await this.request<{ items: KnowledgeDocument[] }>(
      `/api/knowledge/documents?${params.toString()}`
    );
    return response.items || [];
  }

  /** Search knowledge base documents. */
  async searchKnowledge(query: string): Promise<KnowledgeDocument[]> {
    const params = new URLSearchParams({ user_id: this.userId, query });
    const response = await this.request<{ items: KnowledgeDocument[] }>(
      `/api/knowledge/search?${params.toString()}`
    );
    return response.items || [];
  }

  /** List pending and active reminders for the user. */
  async listReminders(): Promise<ReminderItem[]> {
    const response = await this.request<{ items: ReminderItem[] }>(
      `/api/reminders?user_id=${encodeURIComponent(this.userId)}`
    );
    return response.items || [];
  }

  /** Create a new reminder scheduled for the user. */
  async createReminder(
    title: string,
    message: string,
    dueAt: string,
    channel: "desktop" | "langbot" = "desktop"
  ): Promise<ReminderItem> {
    return this.request("/api/reminders", {
      method: "POST",
      body: {
        user_id: this.userId,
        title,
        message,
        due_at: dueAt,
        channel
      }
    });
  }

  /** Cancel an active reminder. */
  async cancelReminder(reminderId: string): Promise<ReminderItem> {
    return this.request(`/api/reminders/${encodeURIComponent(reminderId)}/cancel`, {
      method: "POST",
      body: { user_id: this.userId }
    });
  }

  /** Poll pending desktop notifications. */
  async pollNotifications(): Promise<DesktopNotification[]> {
    const response = await this.request<{ items: DesktopNotification[] }>(
      `/api/notifications/poll?user_id=${encodeURIComponent(this.userId)}`
    );
    return response.items || [];
  }

  /** Acknowledge a notification. */
  async ackNotification(notificationId: string): Promise<void> {
    await this.request(`/api/notifications/${encodeURIComponent(notificationId)}/ack`, {
      method: "POST",
      body: { user_id: this.userId }
    });
  }

  /** Read full low-level diagnostics for one owned task. */
  async taskDiagnostics(taskId: string): Promise<Record<string, unknown>> {
    return this.request(
      `/api/tasks/${encodeURIComponent(taskId)}/diagnostics?user_id=${encodeURIComponent(this.userId)}`
    );
  }

  /** Validate settings through the backend before they replace the local draft. */
  async validateSettings(settings: DesktopSettings): Promise<DesktopSettings> {
    const response = await this.request<{ ok: boolean; settings: Record<string, unknown> }>(
      "/local/settings/validate",
      {
        method: "POST",
        body: {
          api_base_url: settings.apiBaseUrl,
          default_workdir: settings.defaultWorkdir || null,
          default_model_class: settings.defaultModelClass,
          approval_policy: settings.approvalPolicy
        }
      }
    );
    return {
      ...settings,
      apiBaseUrl: String(response.settings.api_base_url),
      defaultWorkdir: String(response.settings.default_workdir || "")
    };
  }

  /** Perform one JSON request and convert backend errors to safe UI messages. */
  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const response = await window.assistantDesktop.apiRequest(this.settings.apiBaseUrl, {
      path,
      method: options.method,
      body: options.body
    });
    return this.readBridgeResponse<T>(response);
  }

  private readBridgeResponse<T>(response: { status: number; body: unknown }): T {
    if (response.status < 200 || response.status >= 300) {
      throw new Error(readErrorMessage(response.body) || `Local API request failed (${response.status})`);
    }
    return response.body as T;
  }
}


/** Extract the backend's supported error shapes without exposing raw payloads. */
function readErrorMessage(error: unknown): string | null {
  if (!error || typeof error !== "object") return null;
  const payload = error as { error?: { message?: unknown }; detail?: unknown };
  if (typeof payload.error?.message === "string" && payload.error.message) {
    return payload.error.message;
  }
  if (typeof payload.detail === "string" && payload.detail) {
    return payload.detail;
  }
  if (Array.isArray(payload.detail) && payload.detail.length) {
    const first = payload.detail[0] as { msg?: unknown; loc?: unknown } | undefined;
    if (first && typeof first.msg === "string") {
      const loc = Array.isArray(first.loc) ? first.loc.join(".") : "request";
      return `${loc}: ${first.msg}`;
    }
  }
  return null;
}
