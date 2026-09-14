import type { ConversationTokenStats } from "./api";

export type ConversationUsagePresentation = {
  usedTokens: number;
  remainingTokens: number;
  limitTokens: number;
  percentage: number;
  statusLabel: string;
};

/** Derive bounded, employee-facing Conversation usage from server-owned estimates. */
export function conversationUsagePresentation(
  stats: ConversationTokenStats,
): ConversationUsagePresentation {
  const limitTokens = Math.max(1, Number(stats.token_limit) || 1);
  const usedTokens = Math.max(0, Number(stats.used_total_tokens) || 0);
  const ratio = Number.isFinite(stats.usage_ratio)
    ? stats.usage_ratio
    : usedTokens / limitTokens;
  const percentage = Math.round(Math.min(1, Math.max(0, ratio)) * 100);
  const remainingTokens = Math.max(
    0,
    Number.isFinite(stats.available_tokens)
      ? stats.available_tokens
      : limitTokens - usedTokens,
  );
  const statusLabel = stats.status === "full"
    ? "已达到上限"
    : stats.status === "warning"
      ? "接近上限"
      : "使用正常";

  return {
    usedTokens,
    remainingTokens,
    limitTokens,
    percentage,
    statusLabel,
  };
}

/** Format integer usage values consistently without exposing false precision. */
export function formatTokenCount(value: number): string {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(
    Math.max(0, Math.round(value)),
  );
}
