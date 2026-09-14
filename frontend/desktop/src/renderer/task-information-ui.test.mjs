import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const streamSource = readFileSync(new URL("./execution-stream.tsx", import.meta.url), "utf8");
const stylesSource = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

test("ordinary task progress does not render per-event cards or a detailed-record panel", () => {
  assert.doesNotMatch(appSource, /TaskDiagnosticsPanel/);
  assert.doesNotMatch(appSource, /timeline-event/);
  assert.doesNotMatch(appSource, /查看详细信息/);
});

test("pending confirmations appear in task progress instead of a separate right-side view", () => {
  assert.doesNotMatch(appSource, /<option value="approvals">/);
  assert.doesNotMatch(appSource, /activePanel === "approvals"/);

  const timelineStart = appSource.indexOf("function renderTimelinePanel");
  const timelineEnd = appSource.indexOf("\nfunction ", timelineStart + 1);
  const timelineSource = appSource.slice(timelineStart, timelineEnd);
  assert.match(timelineSource, /renderPendingApprovals\(/);
  assert.ok(
    timelineSource.indexOf("renderPendingApprovals(") < timelineSource.indexOf("renderPlanProgressCard("),
    "pending confirmation cards should appear before processing steps"
  );
});

test("conversation information restores bounded token usage and workspace facts", () => {
  assert.match(appSource, /renderConversationInfoCard\(tokenStats, workSummary, conversationMessages\)/);
  assert.match(appSource, /模型 Token 已用/);
  assert.match(appSource, /输入用量/);
  assert.match(appSource, /输出用量/);
  assert.doesNotMatch(appSource, /这里是对话内容估算，不是供应商账单/);
  assert.match(appSource, /conversationWorkSummary/);
});

test("conversation speakers use distinct directional chat bubbles without avatars", () => {
  assert.doesNotMatch(appSource, /message-avatar/);
  assert.doesNotMatch(streamSource, /message-avatar/);
  assert.match(stylesSource, /background: linear-gradient\(135deg, #2563eb/);
  assert.match(stylesSource, /border-left: 4px solid #7c3aed/);
  assert.match(stylesSource, /\.user-message-row \{\s*justify-content: flex-end/);
  assert.match(stylesSource, /\.assistant-message-row \{\s*justify-content: flex-start/);
});
