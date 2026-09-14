import test from "node:test";
import assert from "node:assert/strict";
import { filterMemories, filterKnowledgeDocs } from "./memory-knowledge.ts";

test("filterMemories filters by query and category", () => {
  const memories = [
    { id: "1", user_id: "u1", category: "preferences", fact: "Prefer TypeScript over JavaScript", status: "active", created_at: "", updated_at: "" },
    { id: "2", user_id: "u1", category: "project", fact: "Backend runs on FastAPI and Python 3.12", status: "active", created_at: "", updated_at: "" }
  ];

  assert.equal(filterMemories(memories, "typescript").length, 1);
  assert.equal(filterMemories(memories, "", "project").length, 1);
  assert.equal(filterMemories(memories, "", "all").length, 2);
});

test("filterKnowledgeDocs filters documents by title or summary", () => {
  const docs = [
    { id: "d1", title: "Architecture Guidelines", source_type: "manual", summary: "Overview of microservices and agent boundaries", created_at: "", updated_at: "" },
    { id: "d2", title: "Deployment Guide", source_type: "docker", summary: "Docker compose setups and env variables", created_at: "", updated_at: "" }
  ];

  assert.equal(filterKnowledgeDocs(docs, "architecture").length, 1);
  assert.equal(filterKnowledgeDocs(docs, "docker").length, 1);
  assert.equal(filterKnowledgeDocs(docs, "unknown").length, 0);
});
