import type { MyCapabilities } from "./api.ts";

export type CapabilityLoadStatus = "idle" | "loading" | "ready" | "failed";
export type CapabilityCenterState = "loading" | "unavailable" | "empty" | "ready";

const MAX_SUMMARY_LABELS = 8;

const ACTION_CATEGORY_LABELS: Record<string, string> = {
  expense: "费用与报销",
  finance: "费用与报销",
  workspace: "工作区与文件",
  file: "工作区与文件",
  files: "工作区与文件",
  artifact: "成果文件",
  search: "信息检索",
  browser: "信息检索",
  web: "信息检索",
  email: "沟通协作",
  mail: "沟通协作",
  message: "沟通协作",
  calendar: "日程与提醒",
  schedule: "日程与提醒",
  reminder: "日程与提醒",
  memory: "工作记忆",
  knowledge: "知识资料",
  rag: "知识资料"
};

const KNOWLEDGE_SCOPE_LABELS: Record<string, string> = {
  organization: "企业知识",
  company: "企业知识",
  enterprise: "企业知识",
  department: "部门知识",
  team: "部门知识",
  personal: "个人知识",
  user: "个人知识",
  private: "个人知识"
};

const MEMORY_SCOPE_LABELS: Record<string, string> = {
  personal: "个人记忆",
  user: "个人记忆",
  private: "个人记忆",
  department: "部门记忆",
  team: "部门记忆",
  organization: "组织记忆",
  company: "组织记忆",
  enterprise: "组织记忆"
};

function stableCategory(value: string): string {
  return value.trim().toLowerCase().split(/[.:/]/, 1)[0] || "";
}

function uniqueBounded(values: string[]): string[] {
  return [...new Set(values)].slice(0, MAX_SUMMARY_LABELS);
}

export function capabilityCenterState(
  status: CapabilityLoadStatus,
  profile: MyCapabilities | null
): CapabilityCenterState {
  if (status === "failed") return "unavailable";
  if (status !== "ready" || profile === null) return "loading";
  if (profile.capability_details.length === 0) return "empty";
  return "ready";
}

export function actionCategoryLabels(tools: string[]): string[] {
  return uniqueBounded(
    tools.map((tool) => ACTION_CATEGORY_LABELS[stableCategory(tool)] || "其他受管控操作")
  );
}

export function informationScopeLabels(
  knowledgeScopes: string[],
  memoryAccess: string[][]
): string[] {
  const knowledge = knowledgeScopes.map(
    (scope) => KNOWLEDGE_SCOPE_LABELS[stableCategory(scope)] || "其他已授权知识"
  );
  const memory = memoryAccess.map(
    ([scope]) => MEMORY_SCOPE_LABELS[stableCategory(scope || "")] || "其他已授权记忆"
  );
  return uniqueBounded([...knowledge, ...memory]);
}
