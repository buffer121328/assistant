export type DesktopSettings = {
  apiBaseUrl: string;
  userId: string;
  defaultWorkdir: string;
  defaultModelClass: "light" | "standard";
  approvalPolicy: "ask" | "require_high_risk" | "read_only";
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
  approval_type: "tool" | "plan" | "review";
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
  features: Record<string, boolean>;
};

type RequestOptions = {
  method?: string;
  body?: unknown;
};

export class LocalApiClient {
  constructor(private readonly settings: DesktopSettings) {}

  async health(): Promise<Health> {
    return this.request("/local/health");
  }

  async config(): Promise<LocalConfig> {
    return this.request("/local/config");
  }

  async listTasks(): Promise<Task[]> {
    const response = await this.request<{ items: Task[] }>(
      `/local/tasks?user_id=${encodeURIComponent(this.settings.userId)}`
    );
    return response.items;
  }

  async createTask(inputText: string, taskType = "plan"): Promise<Task> {
    const response = await this.request<{ task: Task; queued: boolean }>("/local/tasks", {
      method: "POST",
      body: {
        user_id: this.settings.userId,
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
          user_id: this.settings.userId,
          content
        }
      }
    );
    return response.task;
  }

  async task(taskId: string): Promise<Task> {
    return this.request(
      `/local/tasks/${encodeURIComponent(taskId)}?user_id=${encodeURIComponent(
        this.settings.userId
      )}`
    );
  }

  async events(taskId: string, afterEventId?: string): Promise<LocalEvent[]> {
    const cursor = afterEventId ? `&after_event_id=${encodeURIComponent(afterEventId)}` : "";
    const response = await this.request<{ items: LocalEvent[] }>(
      `/local/tasks/${encodeURIComponent(taskId)}/events?user_id=${encodeURIComponent(
        this.settings.userId
      )}${cursor}`
    );
    return response.items;
  }

  async logs(taskId: string): Promise<LocalEvent[]> {
    const response = await this.request<{ items: LocalEvent[] }>(
      `/local/tasks/${encodeURIComponent(taskId)}/logs?user_id=${encodeURIComponent(
        this.settings.userId
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
        user_id: this.settings.userId,
        decision,
        reason
      }
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
    url.searchParams.set("user_id", this.settings.userId);
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
      throw new Error(error.error?.message || "Local API request failed");
    }
    return response.json() as Promise<T>;
  }
}
