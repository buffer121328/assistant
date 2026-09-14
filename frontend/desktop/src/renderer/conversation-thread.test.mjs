import test from "node:test";
import assert from "node:assert/strict";

import { buildConversationThread, hasAssistantTurn } from "./conversation-thread.ts";

function task({ id, input, result = null, status = "success", createdAt, updatedAt = createdAt }) {
  return {
    task_id: id,
    user_id: "user-1",
    platform: "local",
    task_type: "plan",
    input_text: input,
    status,
    workflow_key: "langgraph.plan",
    model_class: "standard",
    conversation_id: "conversation-1",
    result_text: result,
    error_message: null,
    created_at: createdAt,
    updated_at: updatedAt,
  };
}

test("a follow-up appends to the complete conversation instead of replacing earlier turns", () => {
  const tasks = [
    task({
      id: "task-2",
      input: "你好",
      status: "running",
      createdAt: "2026-08-17T13:38:00Z",
    }),
    task({
      id: "task-1",
      input: "如何学习 Python",
      result: "可以从语法、练习和项目三个阶段开始。",
      createdAt: "2026-08-17T13:30:00Z",
      updatedAt: "2026-08-17T13:31:00Z",
    }),
  ];
  const persisted = [
    {
      message_id: "message-1",
      conversation_id: "conversation-1",
      task_id: "task-1",
      role: "user",
      content: "如何学习 Python",
      created_at: "2026-08-17T13:30:00Z",
    },
    {
      message_id: "message-2",
      conversation_id: "conversation-1",
      task_id: "task-1",
      role: "assistant",
      content: "可以从语法、练习和项目三个阶段开始。",
      created_at: "2026-08-17T13:31:00Z",
    },
    {
      message_id: "message-3",
      conversation_id: "conversation-1",
      task_id: "task-2",
      role: "user",
      content: "你好",
      created_at: "2026-08-17T13:38:00Z",
    },
  ];

  const thread = buildConversationThread(tasks, persisted);

  assert.deepEqual(
    thread.map((message) => [message.role, message.content]),
    [
      ["user", "如何学习 Python"],
      ["assistant", "可以从语法、练习和项目三个阶段开始。"],
      ["user", "你好"],
    ],
  );
  assert.equal(hasAssistantTurn(thread, "task-2"), false);
});

test("task fallbacks preserve legacy history without duplicating persisted messages", () => {
  const tasks = [
    task({
      id: "task-1",
      input: "第一问",
      result: "第一答",
      createdAt: "2026-08-17T13:00:00Z",
      updatedAt: "2026-08-17T13:01:00Z",
    }),
    task({
      id: "task-2",
      input: "第二问",
      result: "第二答",
      createdAt: "2026-08-17T13:02:00Z",
      updatedAt: "2026-08-17T13:03:00Z",
    }),
  ];
  const persisted = [
    {
      message_id: "message-1",
      conversation_id: "conversation-1",
      task_id: "task-1",
      role: "user",
      content: "第一问",
      created_at: "2026-08-17T13:00:00Z",
    },
  ];

  const thread = buildConversationThread(tasks, persisted);

  assert.deepEqual(
    thread.map((message) => [message.task_id, message.role, message.content]),
    [
      ["task-1", "user", "第一问"],
      ["task-1", "assistant", "第一答"],
      ["task-2", "user", "第二问"],
      ["task-2", "assistant", "第二答"],
    ],
  );
  assert.equal(hasAssistantTurn(thread, "task-2"), true);
});
