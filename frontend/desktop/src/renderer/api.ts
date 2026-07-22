export type DesktopSettings = {
  apiBaseUrl: string;
  userId: string;
  defaultWorkdir: string;
  defaultModelClass: "light" | "standard";
  approvalPolicy: "ask" | "require_high_risk" | "read_only";
};

export type LocalTaskType = "plan" | "learn" | "daily" | "office";

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

export type LocalEvent = {
  event_id: string;
  task_id: string;
  type: string;
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


export type ConversationTokenStats = {
  conversation_id: string;
  message_count: number;
  user_message_count: number;
  assistant_message_count: number;
  total_estimated_tokens: number;
  user_estimated_tokens: number;
  assistant_estimated_tokens: number;
  token_limit: number;
  usage_ratio: number;
  status: "ok" | "warning" | "full";
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

export class LocalApiClient {
  constructor(private readonly settings: DesktopSettings) {}

  get hasUserId(): boolean {
    return this.userId.length > 0;
  }

  private get userId(): string {
    return this.settings.userId.trim();
  }

  async health(): Promise<Health> {
    return this.request("/local/health");
  }

  async config(): Promise<LocalConfig> {
    return this.request("/local/config");
  }

  async listTasks(): Promise<Task[]> {
    const response = await this.request<{ items: Task[] }>(
      `/local/tasks?user_id=${encodeURIComponent(this.userId)}`
    );
    return response.items;
  }

  async createTask(inputText: string, taskType: LocalTaskType = "plan"): Promise<Task> {
    const response = await this.request<{ task: Task; queued: boolean }>("/local/tasks", {
      method: "POST",
      body: {
        user_id: this.userId,
        task_type: taskType,
        input_text: inputText,
        model_class: this.settings.defaultModelClass
      }
    });
    return response.task;
  }

  async appendMessage(taskId: string, content: string): Promise<Task> {
    const response = await this.request<{ task: Task; queued: boolean }>(
      `/local/tasks/${encodeURIComponent(taskId)}/messages`,
      {
        method: "POST",
        body: {
          user_id: this.userId,
          content
        }
      }
    );
    return response.task;
  }

  async task(taskId: string): Promise<Task> {
    return this.request(
      `/local/tasks/${encodeURIComponent(taskId)}?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
  }

  async events(taskId: string, afterEventId?: string): Promise<LocalEvent[]> {
    const cursor = afterEventId ? `&after_event_id=${encodeURIComponent(afterEventId)}` : "";
    const response = await this.request<{ items: LocalEvent[] }>(
      `/local/tasks/${encodeURIComponent(taskId)}/events?user_id=${encodeURIComponent(
        this.userId
      )}${cursor}`
    );
    return response.items;
  }

  async logs(taskId: string): Promise<LocalEvent[]> {
    const response = await this.request<{ items: LocalEvent[] }>(
      `/local/tasks/${encodeURIComponent(taskId)}/logs?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
    return response.items;
  }

  async approvals(taskId: string): Promise<Approval[]> {
    const response = await this.request<{ items: Approval[] }>(
      `/local/tasks/${encodeURIComponent(taskId)}/approvals?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
    return response.items;
  }

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


  async conversationTokenStats(conversationId: string): Promise<ConversationTokenStats> {
    return this.request(
      `/local/conversations/${encodeURIComponent(conversationId)}/token-stats?user_id=${encodeURIComponent(
        this.userId
      )}`
    );
  }

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

  async bridgeSession(messageId: string): Promise<RemoteControlBridgeSession> {
    return this.request(`/api/remote-control/bridge/sessions/${encodeURIComponent(messageId)}`);
  }

  async replayBridgeSession(messageId: string): Promise<RemoteControlBridgeReplay> {
    return this.request(`/api/remote-control/bridge/sessions/${encodeURIComponent(messageId)}/replay`, {
      method: "POST"
    });
  }

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

  streamEvents(taskId: string, afterEventId: string | undefined, onEvent: (event: LocalEvent) => void): WebSocket {
    const url = new URL(
      `/local/tasks/${encodeURIComponent(taskId)}/events/stream`,
      this.settings.apiBaseUrl.replace(/^http/, "ws")
    );
    url.searchParams.set("user_id", this.userId);
    if (afterEventId) {
      url.searchParams.set("after_event_id", afterEventId);
    }
    const socket = new WebSocket(url);
    socket.addEventListener("message", (message) => {
      onEvent(JSON.parse(String(message.data)) as LocalEvent);
    });
    return socket;
  }

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const response = await fetch(`${this.settings.apiBaseUrl}${path}`, {
      method: options.method || "GET",
      headers: {
        "content-type": "application/json"
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body)
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: { message: response.statusText } }));
      throw new Error(readErrorMessage(error) || response.statusText || "Local API request failed");
    }
    return response.json() as Promise<T>;
  }
}


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
