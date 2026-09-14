import test from "node:test";
import assert from "node:assert/strict";

import {
  isAssistantMessageEvent,
  normalizeConversationMessage,
  notificationLabel,
  taskResultKind
} from "./conversation-presentation.ts";
import { parseAssistantMarkdown, safeAssistantLinkHref } from "./assistant-markdown.ts";

test("only task message events are presented as AI conversation", () => {
  assert.equal(isAssistantMessageEvent("task.message.completed"), true);
  assert.equal(isAssistantMessageEvent("task.message.delta"), true);
  assert.equal(isAssistantMessageEvent("task.completed"), false);
  assert.equal(isAssistantMessageEvent("task.failed"), false);
});

test("deterministic status results are presented as notifications", () => {
  assert.equal(taskResultKind("status"), "notification");
  assert.equal(taskResultKind("plan"), "assistant");
  assert.equal(notificationLabel("task.completed"), "任务已完成");
  assert.equal(notificationLabel("task.failed"), "暂时没完成");
});


test("conversation messages normalize outer whitespace and preserve safe plain text", () => {
  const normalized = normalizeConversationMessage("\r\n  第一段\r\n第二段 <script>alert(1)</script>  \r\n");

  assert.equal(normalized, "第一段\n第二段 <script>alert(1)</script>");
});

test("assistant Markdown parses headings, lists, quotes, code and links", () => {
  const blocks = parseAssistantMarkdown(
    "# 标题\n\n**重点**\n\n- 第一项\n- 第二项\n\n> 注意事项\n\n```ts\nconst value = 1;\n```\n\n[文档](https://example.com)"
  );

  assert.deepEqual(
    blocks.map((block) => block.kind),
    ["heading", "paragraph", "unordered_list", "quote", "code", "paragraph"]
  );
  assert.equal(blocks[0].level, 1);
  assert.equal(blocks[2].items.length, 2);
  assert.equal(blocks[4].language, "ts");
});

test("assistant Markdown does not activate unsafe links or HTML", () => {
  const blocks = parseAssistantMarkdown("<script>alert(1)</script> [危险](javascript:alert(1))");
  assert.equal(blocks[0].kind, "paragraph");
  assert.match(blocks[0].text, /<script>/);
  assert.equal(safeAssistantLinkHref("javascript:alert(1)"), null);
  assert.equal(safeAssistantLinkHref("/internal-path"), null);
  assert.equal(safeAssistantLinkHref("https://example.com/docs"), "https://example.com/docs");
  assert.equal(safeAssistantLinkHref("mailto:hello@example.com"), "mailto:hello@example.com");
});
