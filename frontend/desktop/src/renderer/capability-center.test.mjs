import test from "node:test";
import assert from "node:assert/strict";
import {
  actionCategoryLabels,
  capabilityCenterState,
  informationScopeLabels
} from "./capability-center.ts";

const profile = {
  schema_version: "1",
  authority_revision: 7,
  capabilities: ["finance.expense-review@1.0.0"],
  capability_details: [
    {
      id: "finance.expense-review@1.0.0",
      display_name: "费用审核",
      summary: "在授权范围内查看并审核费用申请"
    }
  ],
  tools: ["expense.read", "workspace.search_text", "opaque.internal-action-123"],
  knowledge_scopes: ["organization.finance", "department.research", "opaque.tenant-123"],
  memory_access: [["personal", "read_write"], ["organization", "read"]]
};

test("capabilityCenterState distinguishes loading, unavailable, empty, and ready", () => {
  assert.equal(capabilityCenterState("loading", null), "loading");
  assert.equal(capabilityCenterState("failed", null), "unavailable");
  assert.equal(capabilityCenterState("ready", { ...profile, capabilities: [], capability_details: [] }), "empty");
  assert.equal(capabilityCenterState("ready", profile), "ready");
});

test("capability center derives bounded action categories without raw tool identifiers", () => {
  const labels = actionCategoryLabels(profile.tools);

  assert.deepEqual(labels, ["费用与报销", "工作区与文件", "其他受管控操作"]);
  assert.doesNotMatch(labels.join(" "), /expense\.read|workspace\.search_text|internal-action-123/);
});

test("capability center summarizes information scopes without opaque identities", () => {
  const labels = informationScopeLabels(profile.knowledge_scopes, profile.memory_access);

  assert.deepEqual(labels, ["企业知识", "部门知识", "其他已授权知识", "个人记忆", "组织记忆"]);
  assert.doesNotMatch(labels.join(" "), /finance|research|tenant-123/);
});
