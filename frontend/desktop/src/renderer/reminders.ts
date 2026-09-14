import type { ReminderItem, DesktopNotification } from "./api.ts";

export type ReminderPreset = "10m" | "30m" | "1h" | "3h" | "tomorrow_9am";

export const REMINDER_PRESETS: { value: ReminderPreset; label: string }[] = [
  { value: "10m", label: "10 分钟后" },
  { value: "30m", label: "30 分钟后" },
  { value: "1h", label: "1 小时后" },
  { value: "3h", label: "3 小时后" },
  { value: "tomorrow_9am", label: "明天上午 9:00" }
];

export function calculatePresetDueTime(preset: ReminderPreset, now: Date = new Date()): string {
  const d = new Date(now);
  switch (preset) {
    case "10m":
      d.setMinutes(d.getMinutes() + 10);
      break;
    case "30m":
      d.setMinutes(d.getMinutes() + 30);
      break;
    case "1h":
      d.setHours(d.getHours() + 1);
      break;
    case "3h":
      d.setHours(d.getHours() + 3);
      break;
    case "tomorrow_9am":
      d.setDate(d.getDate() + 1);
      d.setHours(9, 0, 0, 0);
      break;
  }
  return d.toISOString();
}

export function formatReminderTime(dueAt: string): string {
  try {
    const d = new Date(dueAt);
    if (isNaN(d.getTime())) return dueAt;
    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();
    const timeStr = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    if (isToday) {
      return `今天 ${timeStr}`;
    }
    const month = d.getMonth() + 1;
    const day = d.getDate();
    return `${month}月${day}日 ${timeStr}`;
  } catch {
    return dueAt;
  }
}

export function reminderStatusLabel(status: string): string {
  switch (status) {
    case "pending":
      return "待提醒";
    case "completed":
    case "sent":
      return "已触发";
    case "cancelled":
      return "已取消";
    default:
      return status;
  }
}

export function filterReminders(
  items: ReminderItem[],
  query: string = "",
  statusFilter: string = "all"
): ReminderItem[] {
  const normalizedQuery = query.trim().toLowerCase();
  return items.filter((item) => {
    if (statusFilter !== "all" && item.status !== statusFilter) {
      return false;
    }
    if (!normalizedQuery) return true;
    const title = (item.title || "").toLowerCase();
    const msg = (item.message || "").toLowerCase();
    return title.includes(normalizedQuery) || msg.includes(normalizedQuery);
  });
}
