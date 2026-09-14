import { parseUnifiedDiff, type ParsedDiff } from "./diff-viewer.ts";
import type { LocalEvent, Task } from "./api";
import { isInternalExecutionName, taskPhaseLabel } from "./task-progress.ts";

export type ExecutionItem =
  | { kind: "thought"; id: string; text: string; timestamp: string }
  | {
      kind: "command";
      id: string;
      command: string;
      exitCode?: number;
      output?: string;
      status: "running" | "success" | "failed";
      durationMs?: number;
      timestamp: string;
    }
  | {
      kind: "diff";
      id: string;
      filePath: string;
      diffText: string;
      parsed: ParsedDiff;
      timestamp: string;
    }
  | {
      kind: "search";
      id: string;
      query: string;
      url?: string;
      summary?: string;
      timestamp: string;
    }
  | {
      kind: "tool";
      id: string;
      toolName: string;
      summary: string;
      details?: Record<string, unknown>;
      status: "running" | "success" | "failed";
      timestamp: string;
    }
  | {
      kind: "step";
      id: string;
      stepIndex?: number;
      title: string;
      status: "pending" | "running" | "completed" | "failed";
      timestamp: string;
    }
  | {
      kind: "assistant_message";
      id: string;
      text: string;
      timestamp: string;
    };

type ExecutionStepItem = Extract<ExecutionItem, { kind: "step" }>;

export type ExecutionStreamGroup =
  | { kind: "phase_track"; id: string; items: ExecutionStepItem[] }
  | { kind: "item"; id: string; item: ExecutionItem };

/**
 * Keeps phase state compact without changing the chronological layout of other execution items.
 */
export function groupExecutionItems(items: ExecutionItem[]): ExecutionStreamGroup[] {
  const groups: ExecutionStreamGroup[] = [];
  let index = 0;

  while (index < items.length) {
    const item = items[index];
    if (item.kind !== "step") {
      groups.push({ kind: "item", id: item.id, item });
      index += 1;
      continue;
    }

    const phaseItems: ExecutionStepItem[] = [];
    while (index < items.length && items[index].kind === "step") {
      phaseItems.push(items[index] as ExecutionStepItem);
      index += 1;
    }
    groups.push({ kind: "phase_track", id: `phase-track-${phaseItems[0].id}`, items: phaseItems });
  }

  return groups;
}

/**
 * Extracts structured CC & Codex-style execution items from raw backend events.
 */
export function extractExecutionItems(events: LocalEvent[], task: Task): ExecutionItem[] {
  const items: ExecutionItem[] = [];

  for (const event of events) {
    const type = event.normalized_type || event.type;
    const payload = event.payload || {};
    const timestamp = event.created_at;

    if (type.startsWith("task.phase.")) {
      const label = taskPhaseLabel(payload.phase);
      if (label) {
        const state = type.slice("task.phase.".length);
        const status =
          state === "completed"
            ? "completed"
            : state === "failed"
              ? "failed"
              : "running";
        const title =
          state === "retrying"
            ? `${label}遇到问题，正在重试`
            : state === "completed"
              ? `${label}已完成`
              : state === "failed"
                ? `${label}未完成`
                : `正在${label}`;
        items.push({
          kind: "step",
          id: `${event.event_id}-phase`,
          title,
          status,
          timestamp
        });
      }
      continue;
    }

    if (
      type.startsWith("task.action.") &&
      isInternalExecutionName(payload.action_name)
    ) {
      continue;
    }

    // 1. Thought / Reasoning
    if (
      type === "task.thought" ||
      type === "agent.thought" ||
      (typeof payload.thought === "string" && payload.thought.trim()) ||
      (typeof payload.reasoning === "string" && payload.reasoning.trim())
    ) {
      const thoughtText = String(payload.thought || payload.reasoning || payload.text || "").trim();
      if (thoughtText) {
        items.push({
          kind: "thought",
          id: `${event.event_id}-thought`,
          text: thoughtText,
          timestamp
        });
      }
    }

    // 2. Command / Shell execution
    const isCommand =
      type === "task.tool.command" ||
      type === "command.executed" ||
      payload.tool_name === "shell" ||
      payload.tool_name === "run_command" ||
      payload.tool_name === "bash" ||
      (typeof payload.command === "string" && Boolean(payload.command.trim()));

    if (isCommand) {
      const cmd = String(payload.command || payload.command_line || payload.tool_name || "command").trim();
      const exitCode = typeof payload.exit_code === "number" ? payload.exit_code : undefined;
      const output = String(payload.output || payload.stdout || payload.stderr || payload.result || "").trim();
      const status =
        exitCode === 0 || type === "task.action.completed"
          ? "success"
          : exitCode && exitCode !== 0
          ? "failed"
          : "running";

      items.push({
        kind: "command",
        id: `${event.event_id}-cmd`,
        command: cmd,
        exitCode,
        output: output || undefined,
        status,
        durationMs: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
        timestamp
      });
      continue;
    }

    // 3. File Diff / Patch
    const isDiff =
      typeof payload.diff === "string" ||
      typeof payload.patch === "string" ||
      payload.tool_name === "file_edit" ||
      payload.tool_name === "replace_file_content" ||
      payload.tool_name === "write_to_file";

    if (isDiff && (payload.diff || payload.patch)) {
      const rawDiff = String(payload.diff || payload.patch || "");
      const filePath = String(payload.path || payload.file_path || "modified-file");
      const parsed = parseUnifiedDiff(rawDiff, filePath);
      items.push({
        kind: "diff",
        id: `${event.event_id}-diff`,
        filePath: parsed.filePath,
        diffText: rawDiff,
        parsed,
        timestamp
      });
      continue;
    }

    // 4. Web Search & Browser
    const isSearch =
      payload.tool_name === "search.web" ||
      payload.tool_name === "browser.read" ||
      type.includes("search") ||
      (typeof payload.query === "string" && Boolean(payload.query));

    if (isSearch) {
      items.push({
        kind: "search",
        id: `${event.event_id}-search`,
        query: String(payload.query || payload.url || "检索信息"),
        url: typeof payload.url === "string" ? payload.url : undefined,
        summary: typeof payload.summary === "string" ? payload.summary : undefined,
        timestamp
      });
      continue;
    }

    // 5. Generic Tool Execution
    if (
      payload.tool_name &&
      typeof payload.tool_name === "string" &&
      !isInternalExecutionName(payload.tool_name)
    ) {
      items.push({
        kind: "tool",
        id: `${event.event_id}-tool`,
        toolName: String(payload.tool_name),
        summary: String(payload.summary || payload.description || `执行 ${payload.tool_name}`),
        details: payload,
        status: type.includes("failed") ? "failed" : "success",
        timestamp
      });
      continue;
    }

    // 6. Plan Steps
    if (type === "task.action.started" || type === "task.action.completed") {
      const stepIndex = typeof payload.step_index === "number" ? payload.step_index + 1 : undefined;
      const stepTitle = String(payload.step_title || payload.action_name || payload.title || `步骤 ${stepIndex || 1}`);
      items.push({
        kind: "step",
        id: `${event.event_id}-step`,
        stepIndex,
        title: stepTitle,
        status: type === "task.action.started" ? "running" : "completed",
        timestamp
      });
      continue;
    }

    // 7. Assistant messages: preserve delta whitespace while growing one transient message.
    const rawMessageText = String(payload.text || payload.message || payload.content || "");
    if (type === "task.message.delta" && rawMessageText) {
      const streamed = latestStreamedAssistantMessage(items);
      if (streamed) {
        streamed.text += rawMessageText;
        streamed.timestamp = timestamp;
      } else {
        items.push({
          kind: "assistant_message",
          id: `stream-${event.event_id}`,
          text: rawMessageText,
          timestamp
        });
      }
      continue;
    }
    const messageText = rawMessageText.trim();
    if ((type === "task.message.completed" || type === "message") && messageText) {
      reconcileAssistantMessage(items, messageText, event.event_id, timestamp);
    }
  }

  // A persisted result is authoritative and reconciles the live transient message if present.
  if (task.result_text) {
    reconcileAssistantMessage(
      items,
      task.result_text,
      `task-${task.task_id}-result`,
      task.updated_at
    );
  }

  return items;
}

function latestStreamedAssistantMessage(items: ExecutionItem[]): Extract<ExecutionItem, { kind: "assistant_message" }> | null {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.kind === "assistant_message" && item.id.startsWith("stream-")) return item;
  }
  return null;
}

function reconcileAssistantMessage(
  items: ExecutionItem[],
  text: string,
  id: string,
  timestamp: string
): void {
  const streamed = latestStreamedAssistantMessage(items);
  if (streamed && (text === streamed.text || text.startsWith(streamed.text))) {
    streamed.id = id;
    streamed.text = text;
    streamed.timestamp = timestamp;
    return;
  }
  if (!items.some((item) => item.kind === "assistant_message" && item.text === text)) {
    items.push({ kind: "assistant_message", id, text, timestamp });
  }
}
