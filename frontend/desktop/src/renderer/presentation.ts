/** Chinese labels and safe recovery copy for the local desktop workspace. */
export type ConnectionState = "checking" | "connected" | "disconnected";


export type WorkspaceState = "first_use" | "disconnected" | "empty" | "approval" | "active";

export type WorkspaceStateCopy = {
  kicker: string;
  title: string;
  body: string;
};

/** Shared labels for the right-side task information area. */
export const TASK_CONSOLE_COPY = {
  heading: "任务信息",
  selectAriaLabel: "选择要查看的内容",
  timeline: "任务进展",
  artifacts: "生成的文件",
  expand: "展开任务信息",
  collapse: "收起任务信息",
  notificationsEyebrow: "进展提醒",
  notificationsHeading: "当前情况",
  timelineAriaLabel: "任务进展记录",
  plan: "处理步骤"
} as const;

export type TaskInformationPanel = "timeline" | "artifacts";

/** Keep optional file access out of the task menu until a file is actually available. */
export function availableTaskInformationPanels(availableFileCount: number): TaskInformationPanel[] {
  return availableFileCount > 0 ? ["timeline", "artifacts"] : ["timeline"];
}

/** Fall back to task progress when the selected task has no generated files. */
export function resolveTaskInformationPanel(
  current: TaskInformationPanel,
  availableFileCount: number
): TaskInformationPanel {
  return availableTaskInformationPanels(availableFileCount).includes(current)
    ? current
    : "timeline";
}

/** Keep the most important workspace states concise and consistent across surfaces. */
export function workspaceStateCopy(state: WorkspaceState): WorkspaceStateCopy {
  return {
    first_use: {
      kicker: "欢迎使用",
      title: "让贾维斯帮你把事情办好",
      body: "先完成一次本机设置。用户标识只用于区分你的本地工作记录，不是账号，也不是密码。"
    },
    disconnected: {
      kicker: "需要完成连接",
      title: "暂时没有连接到本地服务",
      body: "检查服务是否已启动，或确认下面的本地服务地址。"
    },
    empty: {
      kicker: "从这里开始",
      title: "选择一项工作开始协作",
      body: "从左侧打开已有任务，或者先告诉贾维斯你想完成什么。"
    },
    approval: {
      kicker: "需要你确认",
      title: "有一项操作正在等待确认",
      body: "贾维斯会在执行外部操作前说明范围和风险。"
    },
    active: {
      kicker: "正在协助你完成",
      title: "任务正在进行",
      body: "你可以继续补充要求，或在右侧查看最新进展。"
    }
  }[state];
}

export const TASK_TYPE_OPTIONS = [
  { value: "plan", label: "制定计划", detail: "梳理目标、拆解步骤并推进执行" },
  { value: "learn", label: "调研并学习", detail: "查找资料、理解内容并整理结论" },
  { value: "daily", label: "生成日报", detail: "汇集线索、生成工作日报" },
  { value: "office", label: "办公写作", detail: "起草办公文档、处理信息与文案" }
] as const;

export const TASK_STATUS_FILTERS = [
  { value: "all", label: "全部" },
  { value: "pending", label: "待开始" },
  { value: "running", label: "进行中" },
  { value: "waiting_approval", label: "待我确认" },
  { value: "success", label: "已完成" },
  { value: "failed", label: "需处理" },
  { value: "cancelled", label: "已取消" }
] as const;

export const BRIDGE_DELIVERY_FILTERS = [
  { value: "all", label: "全部" },
  { value: "pending", label: "等待发送" },
  { value: "succeeded", label: "已送达" },
  { value: "retry", label: "等待重试" },
  { value: "failed", label: "发送失败" },
  { value: "unknown", label: "状态未知" }
] as const;

const taskTypeLabels: Record<string, string> = {
  plan: "制定计划",
  learn: "调研并学习",
  daily: "生成日报",
  office: "办公处理",
  memory: "管理记忆",
  status: "查看任务状态",
  screen: "获取屏幕截图",
};
const taskStatusLabels: Record<string, string> = Object.fromEntries(
  TASK_STATUS_FILTERS.map((option) => [option.value, option.label])
);

/** Turn stable backend enum values into non-technical product language. */
export function formatTaskType(value: string): string {
  return taskTypeLabels[value] || "其他任务";
}

/** Turn stable backend task statuses into concise, user-facing Chinese labels. */
export function formatStatus(value: string): string {
  return taskStatusLabels[value] || "状态更新中";
}

/** The server state machine allows owner cancel only before a terminal state. */
export function isCancellableStatus(value: string): boolean {
  return value === "pending" || value === "running" || value === "waiting_approval";
}

/** Check if the task is blocked waiting for human-in-the-loop approval. */
export function isWaitingApprovalStatus(value: string): boolean {
  return value === "waiting_approval";
}

/** Human-friendly label for current tool approval policy (aligned with Claude Code permissions visibility). */
export function approvalPolicyLabel(policy?: string): string {
  switch (policy) {
    case "ask":
      return "全部操作确认";
    case "require_high_risk":
      return "高风险操作确认";
    case "read_only":
      return "只读模式";
    default:
      return "标准审批";
  }
}

/** Human-friendly detailed explanation of current approval policy. */
export function approvalPolicyDescription(policy?: string): string {
  switch (policy) {
    case "ask":
      return "全部确认：涉及文件修改、系统命令等操作前均会向你确认";
    case "require_high_risk":
      return "高风险确认：仅在执行删除、覆写或外部变更等高风险操作时向你确认";
    case "read_only":
      return "只读模式：仅允许读取与分析，禁止执行任何修改操作";
    default:
      return "标准审批：遵循系统治理与审批策略";
  }
}

/** Turn backend risk levels into plain-language approval labels. */
export function approvalRiskLabel(value?: string): string {
  switch ((value || "").toLowerCase()) {
    case "low":
      return "一般操作";
    case "medium":
      return "重要操作";
    case "high":
      return "需要确认";
    default:
      return "需要确认";
  }
}

/** Use bounded copy instead of surfacing raw transport errors to non-technical users. */
export function friendlyErrorText(value: string): string {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return "暂时无法完成这项操作，请稍后再试。";
  if (
    normalized.includes("failed to fetch") ||
    normalized.includes("network") ||
    normalized.includes("fetch") ||
    normalized.includes("connection") ||
    normalized.includes("econnrefused")
  ) {
    return "暂时无法连接服务，请稍后再试。";
  }
  if (normalized.includes("unauthorized") || normalized.includes("forbidden")) {
    return "当前没有权限完成这项操作，请联系管理员。";
  }
  return "这项工作暂时没完成，请稍后再试。";
}

/** Preserve deliberate Chinese guidance while hiding raw transport and backend failures. */
export function userFacingMessage(value: string): string {
  return /[\u3400-\u9fff]/.test(value) ? value : friendlyErrorText(value);
}

/** Describe workspace state without implying an account login exists. */
export function connectionCopy(state: ConnectionState, hasConfiguredUser: boolean): string {
  if (state === "checking") return "正在检查本地服务…";
  if (state === "disconnected") return "暂时无法连接本地服务";
  if (!hasConfiguredUser) return "完成初始设置后即可开始使用";
  return "服务已连接，可以开始工作";
}

/** Display an endpoint without query values or credentials in recovery UI. */
export function safeEndpoint(value: string): string {
  try {
    const parsed = new URL(value);
    return `${parsed.protocol}//${parsed.host}${parsed.pathname === "/" ? "" : parsed.pathname}`;
  } catch {
    return "尚未设置";
  }
}

/** Present low-frequency delivery statuses without leaking backend enum names. */
export function formatBridgeDeliveryStatus(status: string | null): string {
  if (!status) return "状态未知";
  return {
    pending: "等待发送",
    succeeded: "已送达",
    retry: "等待重试",
    failed: "发送失败"
  }[status] || "状态未知";
}
