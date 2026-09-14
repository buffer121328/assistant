import test from "node:test";
import assert from "node:assert/strict";

import {
  approvalPolicyDescription,
  approvalPolicyLabel,
  formatStatus,
  isCancellableStatus,
  isWaitingApprovalStatus
} from "./presentation.ts";

test("task control eligibility matches cancellation specification", () => {
  // Only running, pending, and waiting_approval are cancellable
  assert.equal(isCancellableStatus("pending"), true);
  assert.equal(isCancellableStatus("running"), true);
  assert.equal(isCancellableStatus("waiting_approval"), true);
  assert.equal(isCancellableStatus("success"), false);
  assert.equal(isCancellableStatus("failed"), false);
  assert.equal(isCancellableStatus("cancelled"), false);
});

test("waiting approval status correctly triggers alert banners", () => {
  assert.equal(isWaitingApprovalStatus("waiting_approval"), true);
  assert.equal(isWaitingApprovalStatus("running"), false);
  assert.equal(isWaitingApprovalStatus("pending"), false);
  assert.equal(isWaitingApprovalStatus("success"), false);
  assert.equal(formatStatus("waiting_approval"), "待我确认");
});

test("approval policy visibility provides clear Chinese descriptions", () => {
  assert.equal(approvalPolicyLabel("ask"), "全部操作确认");
  assert.equal(approvalPolicyLabel("require_high_risk"), "高风险操作确认");
  assert.equal(approvalPolicyLabel("read_only"), "只读模式");
  assert.match(approvalPolicyDescription("ask"), /全部确认/);
  assert.match(approvalPolicyDescription("require_high_risk"), /高风险/);
  assert.match(approvalPolicyDescription("read_only"), /只读模式/);
});
