import test from "node:test";
import assert from "node:assert/strict";
import { buildConversationGroups, filterConversationGroups } from "./conversation-list.ts";

function task({ id, conversationId, input, status = "success", createdAt }) {
  return {
    task_id: id,
    user_id: "user-1",
    platform: "local",
    task_type: "plan",
    input_text: input,
    status,
    workflow_key: "langgraph.plan",
    model_class: "standard",
    conversation_id: conversationId,
    result_text: null,
    error_message: null,
    created_at: createdAt,
    updated_at: createdAt,
  };
}

test("groups multiple agent runs into one conversation and selects the latest run", () => {
  const groups = buildConversationGroups(
    [
      task({ id: "run-2", conversationId: "conversation-1", input: "继续完善", createdAt: "2026-08-17T10:02:00Z", status: "running" }),
      task({ id: "run-1", conversationId: "conversation-1", input: "先做一个计划", createdAt: "2026-08-17T10:01:00Z" }),
      task({ id: "run-3", conversationId: "conversation-2", input: "另一件事", createdAt: "2026-08-17T10:00:00Z" }),
      task({ id: "status-1", conversationId: null, input: "/status", createdAt: "2026-08-17T10:03:00Z" }),
    ],
    { "conversation-1": "学习 Python" },
  );

  assert.equal(groups.length, 2);
  assert.equal(groups[0].conversationId, "conversation-1");
  assert.equal(groups[0].title, "学习 Python");
  assert.equal(groups[0].taskCount, 2);
  assert.equal(groups[0].latestTask.task_id, "run-2");
  assert.equal(groups[1].conversationId, "conversation-2");
});

test("filters at the conversation level without duplicating matching runs", () => {
  const groups = buildConversationGroups([
    task({ id: "run-2", conversationId: "conversation-1", input: "继续完善", createdAt: "2026-08-17T10:02:00Z", status: "running" }),
    task({ id: "run-1", conversationId: "conversation-1", input: "先做一个计划", createdAt: "2026-08-17T10:01:00Z", status: "failed" }),
    task({ id: "run-3", conversationId: "conversation-2", input: "另一件事", createdAt: "2026-08-17T10:00:00Z", status: "success" }),
  ]);

  const failed = filterConversationGroups(groups, { status: "failed", search: "" });
  assert.deepEqual(failed.map((group) => group.conversationId), ["conversation-1"]);
  assert.equal(failed[0].latestTask.task_id, "run-2");

  const searched = filterConversationGroups(groups, { status: "all", search: "先做一个计划" });
  assert.deepEqual(searched.map((group) => group.conversationId), ["conversation-1"]);

  const localizedStatus = filterConversationGroups(groups, { status: "all", search: "需处理" });
  assert.deepEqual(localizedStatus.map((group) => group.conversationId), ["conversation-1"]);
});

test("desktop history renders grouped conversations and targets the latest run", async () => {
  const { readFile } = await import("node:fs/promises");
  const appSource = await readFile(new URL("./App.tsx", import.meta.url), "utf8");

  assert.match(appSource, /filteredConversations\.map\(\(conversation\)/);
  assert.match(appSource, /setSelectedTaskId\(task\.task_id\)/);
  assert.match(appSource, /conversation\.taskCount > 1/);
});
