import test from "node:test";
import assert from "node:assert/strict";
import { filterPaletteItems } from "./command-palette.ts";

test("filterPaletteItems matches title, subtitle, shortcut, and keywords", () => {
  const items = [
    {
      id: "1",
      type: "command",
      title: "规划任务",
      subtitle: "拆解目标与计划",
      shortcut: "/plan",
      keywords: ["plan", "todo"],
      icon: "sparkles",
      action: () => {}
    },
    {
      id: "2",
      type: "action",
      title: "打开设置",
      subtitle: "修改本地 API 与首选项",
      shortcut: "Cmd+,",
      keywords: ["settings", "config"],
      icon: "settings",
      action: () => {}
    }
  ];

  assert.equal(filterPaletteItems(items, "plan").length, 1);
  assert.equal(filterPaletteItems(items, "设置").length, 1);
  assert.equal(filterPaletteItems(items, "config").length, 1);
  assert.equal(filterPaletteItems(items, "").length, 2);
});
