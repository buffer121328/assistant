import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const app = await readFile(new URL("./App.tsx", import.meta.url), "utf8");
const api = await readFile(new URL("./api.ts", import.meta.url), "utf8");
const panel = await readFile(new URL("./WorkspaceFilesPanel.tsx", import.meta.url), "utf8");

test("new desktop conversations use the selected personal workspace", () => {
  assert.match(api, /async createTask\([\s\S]*spaceId\?: string/);
  assert.match(api, /space_id: spaceId/);
  assert.match(app, /请先选择或新建一个工作区，再开始新会话。/);
  assert.match(app, /selectedSpaceFilter\n\s*\);/);
  assert.match(app, /api\.createTask\([\s\S]*selectedSpaceFilter/);
  assert.match(app, /api\.listPersonalWorkspaces\(/);
  assert.match(app, /api\.importConversationResource\(/);
  assert.match(app, /setWorkspaceFiles\(await api\.listWorkspaceFiles\(selectedSpaceFilter\)\)/);
  assert.doesNotMatch(app, /全部工作区/);
  assert.doesNotMatch(app, /默认个人工作区/);
});

test("workspace files use plain-language labels and backend-governed links", () => {
  assert.match(api, /\/local\/workspaces\?user_id=/);
  assert.match(api, /\/local\/workspaces\/\$\{encodeURIComponent\(workspaceId\)\}\/files/);
  assert.match(api, /\/local\/workspace-files\/\$\{encodeURIComponent\(file\.file_id\)\}\/download/);
  assert.match(panel, /上传的资料/);
  assert.match(panel, /生成的文件/);
  assert.match(panel, /这里还没有文件/);
  assert.match(panel, /来自对话：/);
  assert.doesNotMatch(panel, /storage_reference|source_ref|文件路径/);
});
