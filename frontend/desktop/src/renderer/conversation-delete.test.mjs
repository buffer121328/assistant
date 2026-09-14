import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const handlerStart = appSource.indexOf("async function handleDeleteTask");
const handlerEnd = appSource.indexOf("\n  async function ", handlerStart + 1);
const handlerSource = appSource.slice(handlerStart, handlerEnd);

test("deleting a conversation waits for the owned conversation archive result", () => {
  const archiveIndex = handlerSource.indexOf("await api.archiveConversation(convId)");
  const taskListUpdateIndex = handlerSource.indexOf("setTasks(");

  assert.ok(archiveIndex >= 0, "the delete action should archive the conversation itself");
  assert.doesNotMatch(handlerSource, /api\.deleteTask\(/);
  assert.ok(
    archiveIndex < taskListUpdateIndex,
    "the task list must not hide a conversation before archival succeeds"
  );
});

test("a failed conversation archive keeps the list visible and shows a retry message", () => {
  assert.match(handlerSource, /setError\(reason instanceof Error \? reason\.message : "删除会话失败，请稍后重试"\)/);
  assert.doesNotMatch(handlerSource, /console\.warn\("Delete task API warning/);
});
