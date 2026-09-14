import test from "node:test";
import assert from "node:assert/strict";

import {
  planProgressCopy,
  taskFailurePresentation,
  taskPlanProgress,
  taskProgressPresentation
} from "./task-progress.ts";

const event = (type, payload, sequence = 1) => ({
  event_id: `event-${sequence}`,
  task_id: "task-1",
  type,
  normalized_type: type,
  sequence,
  created_at: `2026-08-17T13:38:${String(sequence).padStart(2, "0")}Z`,
  payload
});

const task = (overrides = {}) => ({
  task_id: "task-1",
  user_id: "user-1",
  platform: "local",
  task_type: "learn",
  input_text: "如何学习 Python",
  status: "running",
  workflow_key: "langgraph.learn",
  model_class: "standard",
  conversation_id: "conversation-1",
  result_text: null,
  error_message: null,
  created_at: "2026-08-17T13:38:00Z",
  updated_at: "2026-08-17T13:38:10Z",
  ...overrides
});

test("task plan progress uses only the newest server-published non-empty steps", () => {
  const plan = taskPlanProgress([
    event("task.plan.created", { steps: ["旧步骤"] }, 1),
    event("task.action.started", { action: "work" }, 2),
    event("task.plan.created", { steps: [" 明确目标 ", "", 2, "输出行动清单"] }, 3)
  ]);

  assert.deepEqual(plan, {
    steps: ["明确目标", "输出行动清单"],
    createdAt: "2026-08-17T13:38:03Z"
  });
  assert.equal(taskPlanProgress([event("task.plan.created", { steps: [] })]), null);
});

test("live phase facts drive visible plan state without internal executor names", () => {
  const progress = taskProgressPresentation(
    [
      event("task.plan.created", { steps: ["读取上下文", "检索来源", "提炼结论"] }, 1),
      event("task.action.started", { action_name: "langgraph.executor" }, 2),
      event("task.phase.started", { phase: "answer_generation", attempt: 1 }, 3)
    ],
    task()
  );

  assert.ok(progress);
  assert.equal(progress.currentPhase, "生成回答");
  assert.deepEqual(
    progress.steps.map((step) => step.status),
    ["completed", "completed", "running"]
  );
  assert.match(progress.summary, /正在生成回答/);
  assert.doesNotMatch(JSON.stringify(progress), /langgraph\.executor/);
});

test("structured decision failure marks the answer step and explains the retry", () => {
  const failedTask = task({
    status: "failed",
    error_message: "Agent decision must be valid JSON"
  });
  const events = [
    event("task.plan.created", { steps: ["读取上下文", "检索来源", "提炼结论"] }, 1),
    event(
      "task.phase.failed",
      {
        phase: "answer_generation",
        reason_code: "structured_json_extraction_failed",
        retryable: true
      },
      2
    )
  ];
  const progress = taskProgressPresentation(events, failedTask);
  const failure = taskFailurePresentation(events, failedTask);

  assert.ok(progress);
  assert.ok(failure);
  assert.equal(failure.title, "生成回答时未完成");
  assert.match(failure.message, /没有返回一个可读取的回答对象/);
  assert.match(failure.message, /自动重试一次/);
  assert.deepEqual(
    progress.steps.map((step) => step.status),
    ["completed", "completed", "failed"]
  );
});


test("structured decision contract failure explains the required answer structure", () => {
  const failedTask = task({ status: "failed" });
  const events = [
    event("task.plan.created", { steps: ["读取上下文", "生成回答"] }, 1),
    event(
      "task.phase.failed",
      {
        phase: "answer_generation",
        reason_code: "structured_decision_contract_invalid",
        retryable: true
      },
      2
    )
  ];

  const failure = taskFailurePresentation(events, failedTask);

  assert.ok(failure);
  assert.match(failure.message, /回答结构不完整或不符合要求/);
  assert.doesNotMatch(failure.message, /Agent decision|JSON/);
});

test("legacy structured errors still map to a specific safe stage", () => {
  const failure = taskFailurePresentation(
    [],
    task({ status: "failed", error_message: "Work plan must be valid JSON" })
  );

  assert.deepEqual(failure, {
    title: "制定处理步骤时未完成",
    stage: "制定处理步骤",
    message: "模型返回的计划格式无法读取。请重试这项任务。",
    retryable: true
  });
});

test("plan progress copy is explicit about lifecycle rather than inferred step completion", () => {
  assert.match(planProgressCopy("running"), /正在按步骤处理/);
  assert.match(planProgressCopy("waiting_approval"), /需要你确认/);
  assert.equal(planProgressCopy("success"), "这项工作已完成。");
  assert.match(planProgressCopy("failed"), /失败步骤|未完成/);
});
