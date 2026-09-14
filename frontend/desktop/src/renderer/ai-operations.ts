import type { CapabilityRequest } from "./capability-requests";

export const AI_OPERATIONS_POLL_INTERVAL_MS = 10_000;

export type AiOperationsRecommendation = {
  category: "problem" | "capability" | "connector";
  title: string;
  nextAction: string;
  approvalRequired: boolean;
};

/** Derive a bounded triage hint from explicit request metadata without model execution. */
export function aiOperationsRecommendation(
  request: Pick<CapabilityRequest, "title" | "description" | "status">
): AiOperationsRecommendation {
  const text = `${request.title} ${request.description}`.toLowerCase();
  if (["mcp", "connector", "连接器", "集成", "接口"].some((keyword) => text.includes(keyword))) {
    return {
      category: "connector",
      title: "建议形成 MCP / Connector 候选",
      nextAction: "先明确目标系统、工具清单、数据范围、认证引用、风险与回滚；启用前由 AI 部负责人审批。",
      approvalRequired: true
    };
  }
  if (["skill", "能力", "自动化", "流程"].some((keyword) => text.includes(keyword))) {
    return {
      category: "capability",
      title: "建议进入能力设计",
      nextAction: "补齐用户场景、输入输出、验收测试、权限和灰度方案；发布前由 AI 部负责人审批。",
      approvalRequired: true
    };
  }
  return {
    category: "problem",
    title: "建议先完成问题复现与缓解",
    nextAction: "核对影响、证据和复现条件，先给出安全可逆的解决建议，再判断是否沉淀为能力。",
    approvalRequired: false
  };
}
