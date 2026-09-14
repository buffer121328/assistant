import test from "node:test";
import assert from "node:assert/strict";

import {
  calculatePresetDueTime,
  filterReminders,
  formatReminderTime,
  reminderStatusLabel
} from "./reminders.ts";

test("calculatePresetDueTime calculates valid future dates", () => {
  const base = new Date("2026-08-17T10:00:00.000Z");
  const t10m = calculatePresetDueTime("10m", base);
  assert.equal(new Date(t10m).getTime(), base.getTime() + 10 * 60 * 1000);

  const t1h = calculatePresetDueTime("1h", base);
  assert.equal(new Date(t1h).getTime(), base.getTime() + 60 * 60 * 1000);
});

test("reminderStatusLabel maps statuses to user-friendly Chinese", () => {
  assert.equal(reminderStatusLabel("pending"), "待提醒");
  assert.equal(reminderStatusLabel("completed"), "已触发");
  assert.equal(reminderStatusLabel("cancelled"), "已取消");
});

test("filterReminders filters by query and status", () => {
  const items = [
    { id: "1", user_id: "u1", title: "跟进周会纪要", message: "发送给项目组", due_at: "2026-08-17T12:00:00Z", status: "pending" },
    { id: "2", user_id: "u1", title: "审批采购单", message: "服务器扩容", due_at: "2026-08-17T14:00:00Z", status: "cancelled" },
    { id: "3", user_id: "u1", title: "方案评审", message: "第三季度工作规划", due_at: "2026-08-17T16:00:00Z", status: "pending" }
  ];

  const pending = filterReminders(items, "", "pending");
  assert.equal(pending.length, 2);

  const matchingQuery = filterReminders(items, "周会", "all");
  assert.equal(matchingQuery.length, 1);
  assert.equal(matchingQuery[0].title, "跟进周会纪要");

  const matchingMessage = filterReminders(items, "扩容", "all");
  assert.equal(matchingMessage.length, 1);
  assert.equal(matchingMessage[0].id, "2");
});
