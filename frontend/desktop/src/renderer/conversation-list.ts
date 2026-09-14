import type { Task, TaskStatus } from "./api";

const taskTypeLabels: Record<string, string> = {
  plan: "制定计划",
  learn: "调研并学习",
  daily: "生成日报",
  office: "办公处理",
  memory: "管理记忆",
  status: "查看任务状态",
  screen: "获取屏幕截图",
};

const taskStatusLabels: Record<TaskStatus, string> = {
  pending: "待开始",
  running: "进行中",
  waiting_approval: "待我确认",
  success: "已完成",
  failed: "需处理",
  cancelled: "已取消",
};

export type ConversationGroup = {
  conversationId: string;
  title: string;
  latestTask: Task;
  tasks: Task[];
  taskCount: number;
};

export type ConversationGroupFilter = {
  status: "all" | TaskStatus;
  search: string;
};

function compareTasks(left: Task, right: Task): number {
  const createdDifference = right.created_at.localeCompare(left.created_at);
  return createdDifference || right.task_id.localeCompare(left.task_id);
}

/** Group execution records into the user-visible Conversation unit. */
export function buildConversationGroups(
  tasks: Task[],
  conversationTitles: Record<string, string> = {},
): ConversationGroup[] {
  const groups = new Map<string, ConversationGroup>();
  for (const task of tasks) {
    if (!task.conversation_id) continue;
    const conversationId = task.conversation_id;
    const existing = groups.get(conversationId);
    if (existing) {
      existing.tasks.push(task);
      existing.tasks.sort(compareTasks);
      existing.latestTask = existing.tasks[0];
      existing.taskCount = existing.tasks.length;
      continue;
    }
    groups.set(conversationId, {
      conversationId,
      title: conversationTitles[conversationId] || task.input_text || "新会话",
      latestTask: task,
      tasks: [task],
      taskCount: 1,
    });
  }
  return [...groups.values()].sort((left, right) => compareTasks(left.latestTask, right.latestTask));
}

/** Apply history filters after grouping so one Conversation cannot become duplicate rows. */
export function filterConversationGroups(
  groups: ConversationGroup[],
  filter: ConversationGroupFilter,
): ConversationGroup[] {
  const normalizedSearch = filter.search.trim().toLowerCase();
  return groups.filter((group) => {
    const matchesStatus =
      filter.status === "all" || group.tasks.some((task) => task.status === filter.status);
    if (!matchesStatus) return false;
    if (!normalizedSearch) return true;
    const searchable = [
      group.title,
      ...group.tasks.flatMap((task) => [
        task.input_text,
        taskTypeLabels[task.task_type] || "其他任务",
        taskStatusLabels[task.status],
        task.workflow_key || "",
        task.model_class || "",
      ]),
    ]
      .join(" ")
      .toLowerCase();
    return searchable.includes(normalizedSearch);
  });
}
