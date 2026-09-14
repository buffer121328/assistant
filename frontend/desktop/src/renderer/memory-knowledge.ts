import type { MemoryItem, KnowledgeDocument } from "./api.ts";

export const MEMORY_CATEGORIES: { value: string; label: string }[] = [
  { value: "all", label: "全部记忆" },
  { value: "preferences", label: "偏好与习惯" },
  { value: "project", label: "项目背景" },
  { value: "guidelines", label: "规则要求" },
  { value: "general", label: "通用事实" }
];

export function filterMemories(memories: MemoryItem[], query: string, category = "all"): MemoryItem[] {
  const q = (query || "").trim().toLowerCase();
  return memories.filter((item) => {
    if (category !== "all" && item.category !== category) return false;
    if (!q) return true;
    return item.fact.toLowerCase().includes(q) || item.category.toLowerCase().includes(q);
  });
}

export function filterKnowledgeDocs(docs: KnowledgeDocument[], query: string): KnowledgeDocument[] {
  const q = (query || "").trim().toLowerCase();
  if (!q) return docs;
  return docs.filter((doc) => {
    return (
      doc.title.toLowerCase().includes(q) ||
      doc.summary.toLowerCase().includes(q) ||
      (doc.content_snippet || "").toLowerCase().includes(q)
    );
  });
}
