export type DepartmentNode = {
  id: string;
  organization_id: string;
  organization_name?: string;
  name: string;
  status: "active" | "revoked";
  connection_state: "online" | "stale" | "offline" | "revoked";
  agent_version: string;
  desired_config_revision: number;
  applied_config_revision: number;
  configuration_drift: boolean;
  accepts_new_work: boolean;
  health_status: string;
  health_summary: string;
  last_seen_at: string | null;
};

export type DiagnosticRecord = {
  task_id: string;
  organization_id: string | null;
  node_id: string | null;
  status: string;
  task_type: string;
  capability_versions: string[];
  error_code: string | null;
  error_summary: string;
  created_at: string;
  updated_at: string;
};

export type DiagnosticPackage = {
  id: string;
  organization_id: string | null;
  node_id: string | null;
  status: "ready" | "expired";
  checksum: string;
  record_count: number;
  expires_at: string;
  created_at: string;
  downloaded_at: string | null;
};

export const NODE_OPERATION_TYPES = [
  "refresh_config",
  "resync_config",
  "pause_new_work",
  "resume_new_work",
  "stop_task",
  "restart_agent"
] as const;

export type NodeOperationType = typeof NODE_OPERATION_TYPES[number];

export type NodeRemoteOperation = {
  id: string;
  organization_id: string;
  node_id: string;
  operation_type: NodeOperationType;
  target_task_id: string | null;
  status: "queued" | "delivered" | "succeeded" | "failed" | "expired";
  expires_at: string;
  delivered_at: string | null;
  completed_at: string | null;
  result_code: string | null;
  result_summary: string;
  created_at: string;
};

/** Present the finite server operation vocabulary without exposing command text. */
export function nodeOperationLabel(value: NodeOperationType): string {
  return {
    refresh_config: "刷新配置",
    resync_config: "重新同步配置",
    pause_new_work: "暂停接收新任务",
    resume_new_work: "恢复接收任务",
    stop_task: "停止指定任务",
    restart_agent: "重启 Agent 服务"
  }[value];
}

/** Limit presentation actions by role; the backend remains authoritative. */
export function allowedNodeOperationTypes(roles: readonly string[]): NodeOperationType[] {
  if (roles.includes("enterprise_admin")) return [...NODE_OPERATION_TYPES];
  if (roles.includes("department_admin")) return NODE_OPERATION_TYPES.filter((item) => item !== "restart_agent");
  return [];
}

/** Translate heartbeat age state to concise administrator-facing Chinese. */
export function nodeConnectionLabel(state: DepartmentNode["connection_state"]): string {
  return { online: "在线", stale: "心跳延迟", offline: "离线", revoked: "已停用" }[state];
}
