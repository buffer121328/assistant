/** Classify server-owned task output without pretending lifecycle facts are AI chat replies. */
export type ConversationOutputKind = "assistant" | "notification";

/** Normalize stored or streamed text while leaving it as plain text for React to render safely. */
export function normalizeConversationMessage(content: string): string {
  return content.replace(/\r\n?/g, "\n").trim();
}

export function isAssistantMessageEvent(eventType: string): boolean {
  return ["task.message.delta", "task.message.completed"].includes(eventType);
}

export function taskResultKind(taskType: string): ConversationOutputKind {
  return taskType === "status" ? "notification" : "assistant";
}

export function notificationLabel(eventType: string): string {
  switch (eventType) {
    case "task.completed":
      return "任务已完成";
    case "task.failed":
      return "暂时没完成";
    case "task.waiting_approval":
    case "approval.required":
    case "task.tool.requested":
      return "等待你的确认";
    default:
      return "任务状态更新";
  }
}
