import test from "node:test";
import assert from "node:assert/strict";

import {
  conversationUsagePresentation,
  formatTokenCount,
} from "./conversation-usage.ts";

function stats(overrides = {}) {
  return {
    conversation_id: "conversation-1",
    message_count: 6,
    user_message_count: 3,
    assistant_message_count: 3,
    total_estimated_tokens: 22,
    user_estimated_tokens: 10,
    assistant_estimated_tokens: 12,
    token_limit: 200000,
    used_input_tokens: 300,
    used_output_tokens: 950,
    used_total_tokens: 1250,
    reserved_input_tokens: 0,
    reserved_output_tokens: 0,
    reserved_total_tokens: 0,
    remaining_tokens: 198750,
    available_tokens: 198750,
    blocked_reason: null,
    usage_ratio: 0.00625,
    status: "ok",
    ...overrides,
  };
}

test("conversation usage shows bounded estimated token consumption and remaining context", () => {
  assert.deepEqual(conversationUsagePresentation(stats()), {
    usedTokens: 1250,
    remainingTokens: 198750,
    limitTokens: 200000,
    percentage: 1,
    statusLabel: "使用正常",
  });
  assert.equal(formatTokenCount(12345), "12,345");
});

test("conversation usage clamps a full conversation instead of rendering an overflowing meter", () => {
  assert.deepEqual(
    conversationUsagePresentation(stats({
      used_total_tokens: 4500,
      available_tokens: 0,
      usage_ratio: 1.125,
      status: "full",
    })),
    {
      usedTokens: 4500,
      remainingTokens: 0,
      limitTokens: 200000,
      percentage: 100,
      statusLabel: "已达到上限",
    },
  );
});
