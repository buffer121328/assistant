import test from "node:test";
import assert from "node:assert/strict";

import { editableSpaces, scopeSummaryText } from "./work-scope.ts";

const spaces = [
  { space_id: "owner", name: "Owner space", description: "", status: "active", role: "owner" },
  { space_id: "editor", name: "Editor space", description: "", status: "active", role: "editor" },
  { space_id: "viewer", name: "Viewer space", description: "", status: "active", role: "viewer" },
  { space_id: "archived", name: "Archived space", description: "", status: "archived", role: "owner" }
];

test("editableSpaces excludes viewer and archived memberships", () => {
  assert.deepEqual(
    editableSpaces(spaces).map((space) => space.space_id),
    ["owner", "editor"]
  );
});

test("scopeSummaryText renders bounded office counts", () => {
  const summary = {
    conversation_id: "conversation-1",
    scope_kind: "space",
    scope_name: "Client A",
    space_id: "owner",
    context_count: 3,
    artifact_count: 2,
    pending_approval_count: 1
  };
  assert.equal(scopeSummaryText(summary), "已添加 3 份资料 · 已产出 2 个结果 · 待确认 1 项");
  assert.equal(scopeSummaryText(summary).includes("/"), false);
});
