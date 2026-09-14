import type { AuthenticatedSession } from "./api";

/** Extract only bounded user-facing messages from the backend error envelope. */
export function authErrorMessage(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const payload = body as { error?: { message?: unknown }; detail?: unknown };
  if (typeof payload.error?.message === "string") return payload.error.message;
  if (typeof payload.detail === "string") return payload.detail;
  if (Array.isArray(payload.detail) && payload.detail[0] && typeof payload.detail[0] === "object") {
    const message = (payload.detail[0] as { msg?: unknown }).msg;
    return typeof message === "string" ? message : null;
  }
  return null;
}

/** Render department context from server-provided names, with a bounded fallback when names are unavailable. */
export function departmentLabel(session: AuthenticatedSession): string {
  if (session.organization_names?.length) return session.organization_names.join("、");
  if (session.organization_ids.length === 1) return "已加入部门";
  if (session.organization_ids.length > 1) return `已加入 ${session.organization_ids.length} 个部门`;
  return "未分配部门";
}

/** Hide raw governance role keys behind employee-facing labels. */
export function roleLabel(roles: string[]): string {
  const labels: Record<string, string> = {
    enterprise_admin: "企业管理员",
    department_admin: "部门负责人",
    capability_publisher: "能力发布者",
    member: "员工",
    manager: "员工",
    auditor: "审计员"
  };
  return [...new Set(roles.map((role) => labels[role] || "员工"))].join("、") || "员工";
}

/** Present one stable responsibility badge independently from detailed governance roles. */
export function identityResponsibilityLabel(roles: readonly string[]): "部门负责人" | "员工" {
  return roles.includes("enterprise_admin") || roles.includes("department_admin")
    ? "部门负责人"
    : "员工";
}
