import test from "node:test";
import assert from "node:assert/strict";

import {
  approvalPolicyDescription,
  approvalPolicyLabel,
  approvalRiskLabel,
  TASK_CONSOLE_COPY,
  availableTaskInformationPanels,
  resolveTaskInformationPanel,
  connectionCopy,
  formatStatus,
  formatTaskType,
  isCancellableStatus,
  isWaitingApprovalStatus,
  safeEndpoint,
  userFacingMessage,
  workspaceStateCopy
} from "./presentation.ts";

test("desktop presentation labels are Chinese-first", () => {
  assert.equal(formatTaskType("office"), "办公处理");
  assert.equal(formatTaskType("memory"), "管理记忆");
  assert.equal(formatTaskType("screen"), "获取屏幕截图");
  assert.equal(formatStatus("waiting_approval"), "待我确认");
  assert.equal(connectionCopy("connected", false), "完成初始设置后即可开始使用");
});

test("task inspector uses plain-language labels for non-technical users", () => {
  assert.equal(TASK_CONSOLE_COPY.heading, "任务信息");
  assert.equal(TASK_CONSOLE_COPY.artifacts, "生成的文件");
  assert.equal(approvalRiskLabel("high"), "需要确认");
});

test("task information keeps confirmation requests inside task progress", () => {
  assert.deepEqual(availableTaskInformationPanels(0), ["timeline"]);
  assert.deepEqual(availableTaskInformationPanels(2), ["timeline", "artifacts"]);
  assert.equal(resolveTaskInformationPanel("artifacts", 0), "timeline");
  assert.equal(resolveTaskInformationPanel("artifacts", 1), "artifacts");
});

test("model failure copy gives a simple next step", () => {
  assert.equal(userFacingMessage("Agent model request failed"), "这项工作暂时没完成，请稍后再试。");
});

test("approval policy labels and descriptions match governance levels", () => {
  assert.equal(approvalPolicyLabel("ask"), "全部操作确认");
  assert.equal(approvalPolicyLabel("require_high_risk"), "高风险操作确认");
  assert.equal(approvalPolicyLabel("read_only"), "只读模式");
  assert.equal(approvalPolicyLabel("unknown"), "标准审批");
  assert.match(approvalPolicyDescription("require_high_risk"), /高风险/);
  assert.match(approvalPolicyDescription("read_only"), /只读/);
});

test("isWaitingApprovalStatus accurately identifies pending approval state", () => {
  assert.equal(isWaitingApprovalStatus("waiting_approval"), true);
  assert.equal(isWaitingApprovalStatus("running"), false);
  assert.equal(isWaitingApprovalStatus("pending"), false);
});

test("recovery copy hides raw transport errors and keeps deliberate Chinese guidance", () => {
  assert.equal(userFacingMessage("Failed to fetch"), "暂时无法连接服务，请稍后再试。");
  assert.equal(userFacingMessage("请先在设置中填写本机用户标识。"), "请先在设置中填写本机用户标识。");
});

test("task cancel is offered only before a terminal status", () => {
  assert.equal(isCancellableStatus("pending"), true);
  assert.equal(isCancellableStatus("running"), true);
  assert.equal(isCancellableStatus("waiting_approval"), true);
  assert.equal(isCancellableStatus("success"), false);
  assert.equal(isCancellableStatus("failed"), false);
  assert.equal(isCancellableStatus("cancelled"), false);
  assert.equal(isCancellableStatus(""), false);
});

test("recovery endpoint excludes credentials and query values", () => {
  assert.equal(safeEndpoint("https://alice:secret@example.test:8443/local?token=hidden"), "https://example.test:8443/local");
  assert.equal(safeEndpoint("not a URL"), "尚未设置");
});


test("workspace states provide concise next-step copy", () => {
  assert.deepEqual(workspaceStateCopy("empty"), {
    kicker: "从这里开始",
    title: "选择一项工作开始协作",
    body: "从左侧打开已有任务，或者先告诉贾维斯你想完成什么。"
  });
  assert.equal(workspaceStateCopy("approval").kicker, "需要你确认");
  assert.equal(workspaceStateCopy("active").title, "任务正在进行");
});
