export const CAPABILITY_REQUEST_STATUSES = [
  "pending",
  "communicating",
  "configuring",
  "testing",
  "pending_approval",
  "published",
  "rejected",
  "closed"
] as const;

export type CapabilityRequestStatus = typeof CAPABILITY_REQUEST_STATUSES[number];

export type CapabilityRequest = {
  id: string;
  title: string;
  description: string;
  status: CapabilityRequestStatus;
  organization_name: string;
  related_task_id: string | null;
  requester_display_name?: string | null;
  created_at: string;
  updated_at: string;
};

const STATUS_LABELS: Record<CapabilityRequestStatus, string> = {
  pending: "待受理",
  communicating: "沟通中",
  configuring: "配置中",
  testing: "测试中",
  pending_approval: "待审批",
  published: "已发布",
  rejected: "已拒绝",
  closed: "已关闭"
};

const NEXT_STATUSES: Record<CapabilityRequestStatus, CapabilityRequestStatus[]> = {
  pending: ["communicating", "rejected", "closed"],
  communicating: ["configuring", "rejected", "closed"],
  configuring: ["testing", "rejected", "closed"],
  testing: ["configuring", "pending_approval", "rejected", "closed"],
  pending_approval: ["configuring", "published", "rejected", "closed"],
  published: ["closed"],
  rejected: ["closed"],
  closed: []
};

export function capabilityRequestStatusLabel(status: string): string {
  return STATUS_LABELS[status as CapabilityRequestStatus] || "状态未知";
}

export function allowedNextCapabilityRequestStatuses(
  status: CapabilityRequestStatus
): CapabilityRequestStatus[] {
  return NEXT_STATUSES[status] || [];
}
