export type SlashCommandItem = {
  command_id: string;
  label: string;
  description: string;
  shortcut: string;
  iconName?: string;
};

export const BUILTIN_SLASH_COMMANDS: SlashCommandItem[] = [
  {
    command_id: "plan",
    label: "制定计划",
    description: "目标拆解、执行计划与下一步行动",
    shortcut: "/plan",
    iconName: "sparkles"
  },
  {
    command_id: "learn",
    label: "调研并学习",
    description: "检索、核对来源、提炼结论",
    shortcut: "/learn",
    iconName: "sparkles"
  },
  {
    command_id: "daily",
    label: "生成日报",
    description: "汇集线索、生成工作日报",
    shortcut: "/daily",
    iconName: "sparkles"
  },
  {
    command_id: "office",
    label: "办公写作",
    description: "起草办公文档或处理事务",
    shortcut: "/office",
    iconName: "sparkles"
  }
];

/**
 * Extracts active slash query from composer text if user is typing a command at the start.
 * Returns null if not in slash command mode.
 */
export function activeSlashQuery(text: string): string | null {
  if (!text || typeof text !== "string") return null;
  const trimmed = text.trimStart();
  if (!trimmed.startsWith("/")) return null;

  // If there's a space after the slash token or a newline, user has completed typing the command
  const firstLine = trimmed.split("\n")[0];
  const firstWord = firstLine.split(" ")[0];

  if (trimmed.includes(" ") || trimmed.includes("\n")) {
    return null;
  }

  return firstWord.substring(1).toLowerCase();
}

/**
 * Filter slash commands matching the active query by label, id, or shortcut.
 */
export function filterSlashCommands(
  query: string | null,
  commands: SlashCommandItem[] = BUILTIN_SLASH_COMMANDS
): SlashCommandItem[] {
  if (query === null) return [];
  const normalized = query.trim().toLowerCase();
  if (!normalized) return commands;

  return commands.filter((cmd) => {
    return (
      cmd.command_id.toLowerCase().includes(normalized) ||
      cmd.label.toLowerCase().includes(normalized) ||
      cmd.shortcut.toLowerCase().includes(normalized) ||
      cmd.description.toLowerCase().includes(normalized)
    );
  });
}

/**
 * Returns the updated selected index after navigating up/down in slash suggestions.
 */
export function nextSlashIndex(currentIndex: number, total: number, direction: "up" | "down"): number {
  if (total <= 0) return 0;
  if (direction === "down") {
    return (currentIndex + 1) % total;
  }
  return (currentIndex - 1 + total) % total;
}
