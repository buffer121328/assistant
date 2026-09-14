import assert from "node:assert/strict";
import test from "node:test";

import {
  allowedNodeOperationTypes,
  nodeConnectionLabel,
  nodeOperationLabel
} from "./node-operations.ts";

test("enterprise admins see only the finite supported operation catalog", () => {
  const actions = allowedNodeOperationTypes(["enterprise_admin"]);
  assert.deepEqual(actions, [
    "refresh_config", "resync_config", "pause_new_work", "resume_new_work", "stop_task", "restart_agent"
  ]);
  assert.equal(actions.includes("shell"), false);
});

test("department admins cannot request restart and auditors cannot mutate nodes", () => {
  assert.equal(allowedNodeOperationTypes(["department_admin"]).includes("restart_agent"), false);
  assert.deepEqual(allowedNodeOperationTypes(["auditor"]), []);
});

test("node state and operations use plain-language labels", () => {
  assert.equal(nodeConnectionLabel("stale"), "心跳延迟");
  assert.equal(nodeOperationLabel("resync_config"), "重新同步配置");
});
