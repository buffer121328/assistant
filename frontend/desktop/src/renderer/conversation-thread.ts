import type { ConversationMessage, Task } from "./api";

function compareMessages(left: ConversationMessage, right: ConversationMessage): number {
  const createdDifference = left.created_at.localeCompare(right.created_at);
  if (createdDifference) return createdDifference;
  if (left.task_id && left.task_id === right.task_id && left.role !== right.role) {
    return left.role === "user" ? -1 : 1;
  }
  return left.message_id.localeCompare(right.message_id);
}

/**
 * Merge the authoritative Conversation message ledger with Task fallbacks.
 *
 * Persisted messages win. Task text is used only while message hydration is in
 * flight or for legacy records, so selecting a newer run can never replace the
 * earlier turns that belong to the same Conversation.
 */
export function buildConversationThread(
  tasks: Task[],
  persistedMessages: ConversationMessage[],
): ConversationMessage[] {
  const messages = [...persistedMessages];
  const rolesByTask = new Map<string, Set<ConversationMessage["role"]>>();

  for (const message of messages) {
    if (!message.task_id) continue;
    const roles = rolesByTask.get(message.task_id) || new Set<ConversationMessage["role"]>();
    roles.add(message.role);
    rolesByTask.set(message.task_id, roles);
  }

  for (const task of tasks) {
    if (!task.conversation_id) continue;
    const roles = rolesByTask.get(task.task_id) || new Set<ConversationMessage["role"]>();
    if (!roles.has("user")) {
      messages.push({
        message_id: `task-${task.task_id}-user-fallback`,
        conversation_id: task.conversation_id,
        task_id: task.task_id,
        role: "user",
        content: task.input_text,
        created_at: task.created_at,
      });
    }
    if (!roles.has("assistant") && task.result_text) {
      messages.push({
        message_id: `task-${task.task_id}-assistant-fallback`,
        conversation_id: task.conversation_id,
        task_id: task.task_id,
        role: "assistant",
        content: task.result_text,
        created_at: task.updated_at,
      });
    }
  }

  return messages.sort(compareMessages);
}

/** Return whether the server ledger already contains an assistant turn for a task. */
export function hasAssistantTurn(
  messages: ConversationMessage[],
  taskId: string,
): boolean {
  return messages.some((message) => message.task_id === taskId && message.role === "assistant");
}
