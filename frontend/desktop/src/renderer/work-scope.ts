import type { ConversationWorkSummary, Space } from "./api";

/** Keep only Space choices the server says can mutate an owned Conversation. */
export function editableSpaces(spaces: Space[]): Space[] {
  return spaces.filter(
    (space) => space.status === "active" && (space.role === "owner" || space.role === "editor")
  );
}

/** Render only bounded office-facing counts returned by the backend summary. */
export function scopeSummaryText(summary: ConversationWorkSummary): string {
  return `已添加 ${summary.context_count} 份资料 · 已产出 ${summary.artifact_count} 个结果 · 待确认 ${summary.pending_approval_count} 项`;
}
