import test from "node:test";
import assert from "node:assert/strict";
import { extractExecutionItems, groupExecutionItems } from "./execution-stream-model.ts";

test("extractExecutionItems extracts thoughts, commands, diffs, searches and steps", () => {
  const dummyTask = {
    task_id: "t-001",
    user_id: "u-001",
    platform: "desktop",
    task_type: "plan",
    input_text: "Help me refactor the database query",
    status: "running",
    workflow_key: null,
    model_class: "standard",
    conversation_id: "c-001",
    result_text: null,
    error_message: null,
    created_at: "2026-08-16T10:00:00Z",
    updated_at: "2026-08-16T10:05:00Z"
  };

  const sampleEvents = [
    {
      event_id: "e-1",
      task_id: "t-001",
      type: "task.thought",
      created_at: "2026-08-16T10:01:00Z",
      sequence: 1,
      payload: { thought: "Analyzing file structure..." }
    },
    {
      event_id: "e-2",
      task_id: "t-001",
      type: "task.tool.command",
      created_at: "2026-08-16T10:02:00Z",
      sequence: 2,
      payload: {
        command: "pytest tests/",
        exit_code: 0,
        output: "=== 10 passed in 0.4s ===",
        duration_ms: 400
      }
    },
    {
      event_id: "e-3",
      task_id: "t-001",
      type: "task.action.completed",
      created_at: "2026-08-16T10:03:00Z",
      sequence: 3,
      payload: {
        tool_name: "replace_file_content",
        path: "backend/db.py",
        diff: "--- a/backend/db.py\n+++ b/backend/db.py\n@@ -1,3 +1,3 @@\n-old_sql()\n+new_sql()\n"
      }
    },
    {
      event_id: "e-4",
      task_id: "t-001",
      type: "task.tool.search",
      created_at: "2026-08-16T10:04:00Z",
      sequence: 4,
      payload: {
        tool_name: "search.web",
        query: "sqlalchemy async session best practice",
        url: "https://docs.sqlalchemy.org"
      }
    },
    {
      event_id: "e-5",
      task_id: "t-001",
      type: "task.message.completed",
      created_at: "2026-08-16T10:05:00Z",
      sequence: 5,
      payload: { text: "Refactoring completed successfully!" }
    }
  ];

  const items = extractExecutionItems(sampleEvents, dummyTask);

  assert.equal(items.length, 5);
  assert.equal(items[0].kind, "thought");
  assert.equal(items[0].text, "Analyzing file structure...");

  assert.equal(items[1].kind, "command");
  assert.equal(items[1].command, "pytest tests/");
  assert.equal(items[1].status, "success");
  assert.equal(items[1].exitCode, 0);

  assert.equal(items[2].kind, "diff");
  assert.equal(items[2].filePath, "backend/db.py");
  assert.equal(items[2].parsed.additions, 1);
  assert.equal(items[2].parsed.deletions, 1);

  assert.equal(items[3].kind, "search");
  assert.equal(items[3].query, "sqlalchemy async session best practice");
  assert.equal(items[3].url, "https://docs.sqlalchemy.org");

  assert.equal(items[4].kind, "assistant_message");
  assert.equal(items[4].text, "Refactoring completed successfully!");
});

test("execution stream hides internal executor names and renders safe live phases", () => {
  const dummyTask = {
    task_id: "t-002",
    user_id: "u-001",
    platform: "desktop",
    task_type: "learn",
    input_text: "你好",
    status: "running",
    workflow_key: "langgraph.learn",
    model_class: "standard",
    conversation_id: "c-001",
    result_text: null,
    error_message: null,
    created_at: "2026-08-17T13:38:00Z",
    updated_at: "2026-08-17T13:38:10Z"
  };
  const events = [
    {
      event_id: "internal-start",
      task_id: "t-002",
      type: "task.action.started",
      normalized_type: "task.action.started",
      created_at: "2026-08-17T13:38:01Z",
      sequence: 1,
      payload: { action_name: "langgraph.executor" }
    },
    {
      event_id: "phase-start",
      task_id: "t-002",
      type: "task.phase.started",
      normalized_type: "task.phase.started",
      created_at: "2026-08-17T13:38:02Z",
      sequence: 2,
      payload: { phase: "answer_generation", attempt: 1 }
    },
    {
      event_id: "phase-retry",
      task_id: "t-002",
      type: "task.phase.retrying",
      normalized_type: "task.phase.retrying",
      created_at: "2026-08-17T13:38:03Z",
      sequence: 3,
      payload: { phase: "answer_generation", attempt: 2 }
    }
  ];

  const items = extractExecutionItems(events, dummyTask);

  assert.deepEqual(
    items.map((item) => item.kind === "step" ? item.title : item.kind),
    ["正在生成回答", "生成回答遇到问题，正在重试"]
  );
  assert.doesNotMatch(JSON.stringify(items), /langgraph\.executor/);
});

test("execution stream coalesces answer deltas and reconciles the completed result", () => {
  const task = {
    task_id: "t-delta",
    user_id: "u-001",
    platform: "desktop",
    task_type: "plan",
    input_text: "请总结",
    status: "success",
    workflow_key: null,
    model_class: "standard",
    conversation_id: "c-001",
    result_text: "你好，世界",
    error_message: null,
    created_at: "2026-08-17T12:00:00Z",
    updated_at: "2026-08-17T12:00:03Z"
  };
  const events = [
    {
      event_id: "delta-1",
      task_id: task.task_id,
      type: "task.message.delta",
      created_at: "2026-08-17T12:00:01Z",
      sequence: 1,
      payload: { text: "你好" }
    },
    {
      event_id: "delta-2",
      task_id: task.task_id,
      type: "task.message.delta",
      created_at: "2026-08-17T12:00:02Z",
      sequence: 2,
      payload: { text: "，世界" }
    },
    {
      event_id: "completed",
      task_id: task.task_id,
      type: "task.message.completed",
      created_at: "2026-08-17T12:00:03Z",
      sequence: 3,
      payload: { text: "你好，世界" }
    }
  ];

  const messages = extractExecutionItems(events, task).filter((item) => item.kind === "assistant_message");

  assert.equal(messages.length, 1);
  assert.equal(messages[0].text, "你好，世界");
});

test("execution stream groups adjacent phase items without grouping other execution output", () => {
  const items = [
    { kind: "step", id: "phase-plan", title: "正在制定处理步骤", status: "running", timestamp: "2026-08-17T13:38:00Z" },
    { kind: "step", id: "phase-plan-done", title: "制定处理步骤已完成", status: "completed", timestamp: "2026-08-17T13:38:01Z" },
    { kind: "assistant_message", id: "message-1", text: "处理中", timestamp: "2026-08-17T13:38:02Z" },
    { kind: "step", id: "phase-answer", title: "正在生成回答", status: "running", timestamp: "2026-08-17T13:38:03Z" }
  ];

  const groups = groupExecutionItems(items);

  assert.deepEqual(
    groups.map((group) => group.kind === "phase_track" ? group.items.map((item) => item.id) : group.item.id),
    [["phase-plan", "phase-plan-done"], "message-1", ["phase-answer"]]
  );
});
