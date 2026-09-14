import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  CAPABILITY_REQUEST_STATUSES,
  allowedNextCapabilityRequestStatuses,
  capabilityRequestStatusLabel
} from "./capability-requests.ts";

test("capability request status labels cover the PRD lifecycle", () => {
  assert.equal(CAPABILITY_REQUEST_STATUSES.length, 8);
  assert.deepEqual(
    CAPABILITY_REQUEST_STATUSES.map(capabilityRequestStatusLabel),
    ["待受理", "沟通中", "配置中", "测试中", "待审批", "已发布", "已拒绝", "已关闭"]
  );
});

test("capability request next states match the server transition graph", () => {
  assert.deepEqual(allowedNextCapabilityRequestStatuses("pending"), ["communicating", "rejected", "closed"]);
  assert.deepEqual(allowedNextCapabilityRequestStatuses("testing"), ["configuring", "pending_approval", "rejected", "closed"]);
  assert.deepEqual(allowedNextCapabilityRequestStatuses("closed"), []);
});

test("employee request surface preserves drafts on failure and never implies authority", async () => {
  const panel = await readFile(new URL("./CapabilityRequestPanel.tsx", import.meta.url), "utf8");
  const app = await readFile(new URL("./App.tsx", import.meta.url), "utf8");
  const api = await readFile(new URL("./api.ts", import.meta.url), "utf8");

  assert.match(panel, /await onSubmit/);
  assert.match(panel, /填写的内容已保留/);
  assert.match(panel, /不会立即扩大你的工作权限/);
  assert.match(app, /CapabilityRequestPanel/);
  assert.match(app, /api\.listCapabilityRequests\(\)/);
  assert.match(app, /api\.createCapabilityRequest\(draft\)/);
  assert.match(api, /\/api\/me\/capability-requests/);
  assert.doesNotMatch(panel, /grant|publishCapability|distributeCapability/);
});
