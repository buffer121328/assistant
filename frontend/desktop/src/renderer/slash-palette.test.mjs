import test from "node:test";
import assert from "node:assert/strict";
import { activeSlashQuery, filterSlashCommands, nextSlashIndex } from "./slash-palette.ts";

test("activeSlashQuery extracts command query when starting with slash", () => {
  assert.equal(activeSlashQuery("/"), "");
  assert.equal(activeSlashQuery("/pl"), "pl");
  assert.equal(activeSlashQuery("/plan"), "plan");
  assert.equal(activeSlashQuery("/plan do something"), null);
  assert.equal(activeSlashQuery("hello /plan"), null);
  assert.equal(activeSlashQuery(""), null);
});

test("filterSlashCommands matches query against command_id, label, and description", () => {
  const all = filterSlashCommands("");
  assert.equal(all.length >= 4, true);

  const planResults = filterSlashCommands("plan");
  assert.equal(planResults.length, 1);
  assert.equal(planResults[0].command_id, "plan");

  const chineseSearch = filterSlashCommands("日报");
  assert.equal(chineseSearch.length, 1);
  assert.equal(chineseSearch[0].command_id, "daily");
});

test("nextSlashIndex wraps around in both directions", () => {
  assert.equal(nextSlashIndex(0, 5, "down"), 1);
  assert.equal(nextSlashIndex(4, 5, "down"), 0);
  assert.equal(nextSlashIndex(0, 5, "up"), 4);
  assert.equal(nextSlashIndex(3, 5, "up"), 2);
});
