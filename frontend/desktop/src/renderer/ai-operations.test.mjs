import test from "node:test";
import assert from "node:assert/strict";
import {
  AI_OPERATIONS_POLL_INTERVAL_MS,
  aiOperationsRecommendation
} from "./ai-operations.ts";

const request = (title, description = "") => ({ title, description, status: "pending" });

test("AI operations queue uses a bounded near-real-time polling interval", () => {
  assert.equal(AI_OPERATIONS_POLL_INTERVAL_MS, 10_000);
});

test("AI operations recommendation separates problem, capability, and MCP proposals", () => {
  assert.equal(aiOperationsRecommendation(request("登录报错")).category, "problem");
  assert.equal(aiOperationsRecommendation(request("需要自动化报表能力")).category, "capability");
  const connector = aiOperationsRecommendation(request("接入新的 MCP 搜索接口"));
  assert.equal(connector.category, "connector");
  assert.equal(connector.approvalRequired, true);
  assert.match(connector.nextAction, /AI 部负责人审批/);
});
