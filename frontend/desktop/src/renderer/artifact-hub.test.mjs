import test from "node:test";
import assert from "node:assert/strict";
import { classifyArtifact, formatArtifactSize, filterArtifacts } from "./artifact-hub.ts";

test("classifyArtifact identifies document, image, spreadsheet correctly", () => {
  const dummy = (filename, media_type, generation_method = "") => ({
    artifact_id: "art-1",
    tenant_id: "t-1",
    organization_id: null,
    owner_type: "user",
    owner_id: "u-1",
    conversation_id: "c-1",
    task_id: "task-1",
    run_id: null,
    filename,
    media_type,
    size_bytes: 1024,
    content_hash: "hash",
    version: 1,
    generation_method,
    visibility: "private",
    sensitivity: "normal",
    lifecycle_state: "active",
    created_at: "2026-08-16T12:00:00Z",
    updated_at: "2026-08-16T12:00:00Z"
  });

  assert.equal(classifyArtifact(dummy("patch.diff", "text/x-diff")), "document");
  assert.equal(classifyArtifact(dummy("summary.txt", "text/plain")), "document");
  assert.equal(classifyArtifact(dummy("report.md", "text/markdown")), "document");
  assert.equal(classifyArtifact(dummy("data.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")), "spreadsheet");
  assert.equal(classifyArtifact(dummy("screenshot.png", "image/png")), "image");
  assert.equal(classifyArtifact(dummy("capture.bin", "application/octet-stream", "screenshot")), "image");
});

test("formatArtifactSize formats bytes correctly", () => {
  assert.equal(formatArtifactSize(500), "500 B");
  assert.equal(formatArtifactSize(2048), "2.0 KB");
  assert.equal(formatArtifactSize(1048576 * 3.5), "3.5 MB");
});

test("filterArtifacts filters by query, category, and state", () => {
  const list = [
    {
      artifact_id: "art-001",
      filename: "architecture.md",
      media_type: "text/markdown",
      lifecycle_state: "active"
    },
    {
      artifact_id: "art-002",
      filename: "sales_data.xlsx",
      media_type: "application/vnd.ms-excel",
      lifecycle_state: "archived"
    }
  ];

  assert.equal(filterArtifacts(list, { query: "architecture" }).length, 1);
  assert.equal(filterArtifacts(list, { category: "spreadsheet" }).length, 1);
  assert.equal(filterArtifacts(list, { state: "active" }).length, 1);
  assert.equal(filterArtifacts(list, { state: "archived" }).length, 1);
});
