import test from "node:test";
import assert from "node:assert/strict";
import {
  activeSlashQuery,
  removeResourceToken,
  replaceSlashToken,
  serializeResourceToken
} from "./composer.ts";

test("slash discovery is limited to the active trigger token", () => {
  assert.equal(activeSlashQuery("/"), "");
  assert.equal(activeSlashQuery("please /pla"), "pla");
  assert.equal(activeSlashQuery("ordinary text"), null);
});

test("slash selection keeps a visible command token and stable ID separately", () => {
  assert.equal(replaceSlashToken("/pla", "plan"), "/plan ");
  assert.equal(serializeResourceToken("ref-7"), "resource:ref-7");
});

test("resource token removal only removes the exact stable identity", () => {
  assert.deepEqual(removeResourceToken(["ref-1", "ref-2"], "ref-1"), ["ref-2"]);
});
