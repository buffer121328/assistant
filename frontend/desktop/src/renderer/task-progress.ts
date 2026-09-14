import type { LocalEvent, Task, TaskStatus } from "./api";

/** A server-published plan. Per-step state is derived only from safe server lifecycle facts. */
export type TaskPlanProgress = {
  steps: string[];
  createdAt: string;
};

export type TaskPlanStepStatus = "pending" | "running" | "completed" | "failed";

export type TaskPlanStepPresentation = {
  title: string;
  status: TaskPlanStepStatus;
};

export type TaskFailurePresentation = {
  title: string;
  stage: string;
  message: string;
  retryable: boolean;
};

export type TaskProgressPresentation = {
  steps: TaskPlanStepPresentation[];
  createdAt: string;
  currentPhase: string | null;
  summary: string;
  failure: TaskFailurePresentation | null;
};

type SafePhase = "planning" | "answer_generation" | "review" | "execution";

type PhaseFact = {
  phase: SafePhase;
  state: "started" | "retrying" | "completed" | "failed";
  reasonCode: string | null;
  retryable: boolean | null;
};

const PHASE_LABELS: Record<SafePhase, string> = {
  planning: "制定处理步骤",
  execution: "执行处理步骤",
  answer_generation: "生成回答",
  review: "检查结果"
};

/** Read the newest complete plan from the task event stream. */
export function taskPlanProgress(events: LocalEvent[]): TaskPlanProgress | null {
  const source = [...events]
    .reverse()
    .find((event) => eventType(event) === "task.plan.created");
  if (!source || !Array.isArray(source.payload.steps)) return null;
  const steps = source.payload.steps.flatMap((item) =>
    typeof item === "string" && item.trim() ? [item.trim()] : []
  );
  return steps.length ? { steps, createdAt: source.created_at } : null;
}

/** Build one coherent progress model for both the task panel and conversation UI. */
export function taskProgressPresentation(
  events: LocalEvent[],
  task: Task
): TaskProgressPresentation | null {
  const plan = taskPlanProgress(events);
  if (!plan) return null;
  const phase = latestPhaseFact(events);
  const failure = taskFailurePresentation(events, task);
  const steps = presentPlanSteps(plan.steps, task.status, phase, failure);
  const currentPhase = phase && phase.state !== "completed" ? PHASE_LABELS[phase.phase] : null;

  return {
    steps,
    createdAt: plan.createdAt,
    currentPhase,
    summary: progressSummary(task.status, phase, failure),
    failure
  };
}

/** Derive one actionable, non-technical failure notice from phase events or legacy errors. */
export function taskFailurePresentation(
  events: LocalEvent[],
  task: Task | null
): TaskFailurePresentation | null {
  if (!task || task.status !== "failed") return null;
  const failedPhase = [...events]
    .reverse()
    .map(readPhaseFact)
    .find((fact): fact is PhaseFact => Boolean(fact && fact.state === "failed"));
  if (failedPhase) {
    const stage = PHASE_LABELS[failedPhase.phase];
    const reason = failureReasonCopy(failedPhase.reasonCode);
    const retryable = failedPhase.retryable !== false;
    return {
      title: `${stage}时未完成`,
      stage,
      message: `${reason}${retryable ? " 请重试这项任务。" : " 请检查任务范围后再试。"}`,
      retryable
    };
  }

  const legacy = legacyFailure(task.error_message || "");
  if (legacy) return legacy;
  return {
    title: "处理时未完成",
    stage: "处理任务",
    message: "这项工作暂时没完成。请重试；如果仍然失败，可查看失败阶段后再调整任务。",
    retryable: true
  };
}

/** Explain the task lifecycle when no more specific server phase is available. */
export function planProgressCopy(status: TaskStatus): string {
  switch (status) {
    case "pending":
      return "已安排好处理步骤，正在等待开始。";
    case "running":
      return "正在按步骤处理，最新进展会显示在这里。";
    case "waiting_approval":
      return "需要你确认后才能继续。";
    case "success":
      return "这项工作已完成。";
    case "failed":
      return "处理过程中有一步未完成，请查看失败步骤。";
    case "cancelled":
      return "这项工作已取消。";
  }
}

/** Return true for implementation identifiers that must never reach employee UI. */
export function isInternalExecutionName(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const normalized = value.trim().toLowerCase();
  return (
    normalized === "langgraph.executor" ||
    normalized.startsWith("langgraph.step.") ||
    normalized.startsWith("runtime.")
  );
}

/** Convert stable phase codes to employee-facing copy. */
export function taskPhaseLabel(value: unknown): string | null {
  return isSafePhase(value) ? PHASE_LABELS[value] : null;
}

function eventType(event: LocalEvent): string {
  return event.normalized_type || event.type;
}

function latestPhaseFact(events: LocalEvent[]): PhaseFact | null {
  for (const event of [...events].reverse()) {
    const fact = readPhaseFact(event);
    if (fact) return fact;
  }
  return null;
}

function readPhaseFact(event: LocalEvent): PhaseFact | null {
  const type = eventType(event);
  if (!type.startsWith("task.phase.")) return null;
  const state = type.slice("task.phase.".length);
  if (!isPhaseState(state) || !isSafePhase(event.payload.phase)) return null;
  return {
    phase: event.payload.phase,
    state,
    reasonCode:
      typeof event.payload.reason_code === "string" ? event.payload.reason_code : null,
    retryable:
      typeof event.payload.retryable === "boolean" ? event.payload.retryable : null
  };
}

function presentPlanSteps(
  titles: string[],
  status: TaskStatus,
  phase: PhaseFact | null,
  failure: TaskFailurePresentation | null
): TaskPlanStepPresentation[] {
  const states: TaskPlanStepStatus[] = titles.map(() => "pending");
  if (status === "success") {
    states.fill("completed");
  } else if (status === "cancelled") {
    // Cancellation does not claim that an unreported step completed.
  } else {
    const activeIndex = phaseStepIndex(phase?.phase || null, titles.length);
    if (activeIndex !== null) {
      for (let index = 0; index < activeIndex; index += 1) states[index] = "completed";
      states[activeIndex] = phase?.state === "failed" ? "failed" : "running";
    } else if (status === "running" && titles.length) {
      states[0] = "running";
    } else if (status === "failed" && failure && titles.length) {
      states[legacyFailureIndex(failure.stage, titles.length)] = "failed";
    }
  }
  return titles.map((title, index) => ({ title, status: states[index] }));
}

function phaseStepIndex(phase: SafePhase | null, stepCount: number): number | null {
  if (!phase || stepCount < 1) return null;
  if (phase === "planning") return 0;
  if (phase === "execution") return Math.min(1, stepCount - 1);
  return stepCount - 1;
}

function legacyFailureIndex(stage: string, stepCount: number): number {
  return stage === "制定处理步骤" ? 0 : Math.max(0, stepCount - 1);
}

function progressSummary(
  status: TaskStatus,
  phase: PhaseFact | null,
  failure: TaskFailurePresentation | null
): string {
  if (failure) return failure.message;
  if (status === "running" && phase) {
    if (phase.state === "retrying") {
      return `${PHASE_LABELS[phase.phase]}时遇到格式问题，正在自动重试。`;
    }
    if (phase.state === "completed") {
      return `${PHASE_LABELS[phase.phase]}已完成，正在进入下一步。`;
    }
    return `正在${PHASE_LABELS[phase.phase]}。`;
  }
  return planProgressCopy(status);
}

function failureReasonCopy(reasonCode: string | null): string {
  switch (reasonCode) {
    case "truncated_structured_output":
      return "模型回答在完成前被截断，系统已自动重试一次。";
    case "empty_structured_output":
      return "模型没有返回可读取的内容，系统已自动重试一次。";
    case "structured_json_extraction_failed":
      return "模型没有返回一个可读取的回答对象，系统已自动重试一次。";
    case "structured_decision_contract_invalid":
      return "模型返回的回答结构不完整或不符合要求，系统已自动重试一次。";
    case "invalid_structured_output":
      return "模型返回的格式无法读取，系统已自动重试一次。";
    case "provider_unavailable":
      return "模型服务暂时不可用。";
    case "budget_exceeded":
      return "本次任务已达到允许的运行额度。";
    default:
      return "这一阶段没有得到可用结果。";
  }
}

function legacyFailure(value: string): TaskFailurePresentation | null {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return null;
  if (normalized.includes("work plan") && normalized.includes("valid json")) {
    return {
      title: "制定处理步骤时未完成",
      stage: "制定处理步骤",
      message: "模型返回的计划格式无法读取。请重试这项任务。",
      retryable: true
    };
  }
  if (normalized.includes("agent decision") && normalized.includes("valid json")) {
    return {
      title: "生成回答时未完成",
      stage: "生成回答",
      message: "模型返回的回答格式无法读取。请重试这项任务。",
      retryable: true
    };
  }
  if (normalized.includes("modelgatewayerror") || normalized.includes("model request failed")) {
    return {
      title: "连接模型服务时未完成",
      stage: "连接模型服务",
      message: "模型服务暂时不可用。请稍后重试。",
      retryable: true
    };
  }
  return null;
}

function isSafePhase(value: unknown): value is SafePhase {
  return typeof value === "string" && value in PHASE_LABELS;
}

function isPhaseState(value: string): value is PhaseFact["state"] {
  return ["started", "retrying", "completed", "failed"].includes(value);
}
