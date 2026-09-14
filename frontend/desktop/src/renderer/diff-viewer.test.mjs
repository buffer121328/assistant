import test from "node:test";
import assert from "node:assert/strict";
import { parseUnifiedDiff } from "./diff-viewer.ts";

test("parseUnifiedDiff parses additions, deletions, line numbers and file paths", () => {
  const sampleDiff = `--- a/src/index.ts
+++ b/src/index.ts
@@ -1,5 +1,6 @@
 import React from "react";
-const oldTitle = "Hello";
+const newTitle = "Hello World";
+console.log(newTitle);
 export default App;
`;

  const parsed = parseUnifiedDiff(sampleDiff);
  assert.equal(parsed.filePath, "src/index.ts");
  assert.equal(parsed.additions, 2);
  assert.equal(parsed.deletions, 1);
  assert.equal(parsed.chunks.length, 1);

  const lines = parsed.chunks[0].lines;
  assert.equal(lines[0].type, "context");
  assert.equal(lines[0].oldLineNumber, 1);
  assert.equal(lines[0].newLineNumber, 1);

  assert.equal(lines[1].type, "delete");
  assert.equal(lines[1].oldLineNumber, 2);
  assert.equal(lines[1].content, 'const oldTitle = "Hello";');

  assert.equal(lines[2].type, "add");
  assert.equal(lines[2].newLineNumber, 2);
  assert.equal(lines[2].content, 'const newTitle = "Hello World";');

  assert.equal(lines[3].type, "add");
  assert.equal(lines[3].newLineNumber, 3);
  assert.equal(lines[3].content, "console.log(newTitle);");
});

test("parseUnifiedDiff safely handles empty or non-diff strings", () => {
  const parsed = parseUnifiedDiff("", "fallback.py");
  assert.equal(parsed.filePath, "fallback.py");
  assert.equal(parsed.additions, 0);
  assert.equal(parsed.deletions, 0);
  assert.equal(parsed.chunks.length, 0);
});
