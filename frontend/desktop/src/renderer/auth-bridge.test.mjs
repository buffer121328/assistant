import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const preload = await readFile(new URL("../preload/index.ts", import.meta.url), "utf8");
const main = await readFile(new URL("../main/index.ts", import.meta.url), "utf8");
const api = await readFile(new URL("./api.ts", import.meta.url), "utf8");
const app = await readFile(new URL("./App.tsx", import.meta.url), "utf8");

test("preload exposes bounded auth operations but not a session-token getter", () => {
  assert.match(preload, /authState/);
  assert.match(preload, /apiRequest/);
  assert.match(preload, /logout/);
  assert.doesNotMatch(preload, /getSessionToken/);
  assert.doesNotMatch(preload, /sessionToken:\s*\(/);
});

test("desktop session proxy keeps credentials in main while task events use the bounded live subscription", () => {
  assert.match(main, /safeStorage\.encryptString/);
  assert.match(main, /safeStorage\.decryptString/);
  assert.match(main, /localHosts = new Set/);
  assert.match(main, /!localHosts\.has\(parsed\.hostname\)/);
  assert.match(main, /X-Assistant-Session/);
  assert.match(main, /Idempotency-Key/);
  assert.doesNotMatch(api, /new WebSocket\(/);
  assert.match(app, /subscribeTaskEvents/);
  assert.doesNotMatch(app, /pollEvents/);
  assert.doesNotMatch(app, /X-Assistant-Session/);
});

test("logout clears in-memory employee workspace state", () => {
  assert.match(app, /setSession\(null\)/);
  assert.match(app, /setTasks\(\[\]\)/);
  assert.match(app, /setMyCapabilities\(null\)/);
  assert.match(app, /setBridgeSessions\(\[\]\)/);
});

test("desktop API proxy returns a bounded timeout response", () => {
  assert.match(main, /LOCAL_API_REQUEST_TIMEOUT_MS/);
  assert.match(main, /AbortSignal\.timeout\(LOCAL_API_REQUEST_TIMEOUT_MS\)/);
  assert.match(main, /timedOut \? 504 : 503/);
  assert.match(main, /本地服务响应超时，请稍候查看任务列表或重试。/);
});

test("new-session creation is visibly pending and cannot be submitted twice", () => {
  assert.match(app, /const \[createTaskPending, setCreateTaskPending\] = useState\(false\)/);
  assert.match(app, /if \(!inputText\.trim\(\) \|\| createTaskPending\) return;/);
  assert.match(app, /setCreateTaskPending\(true\)/);
  assert.match(app, /finally \{\s*setCreateTaskPending\(false\);\s*\}/);
  assert.match(app, /disabled=\{[^}]*createTaskPending[^}]*\}/);
  assert.match(app, /createTaskPending \? "创建中…" : "开始新会话"/);
});

test("desktop window does not wait on a blank initial compositor frame", () => {
  assert.match(main, /show:\s*true/);
  assert.match(main, /backgroundColor:\s*"#eff4fa"/);
  assert.doesNotMatch(main, /mainWindow\.once\("ready-to-show"/);
});

test("native menu and tray can open the separately bundled management surface", () => {
  assert.match(main, /label: "企业管理台", click: \(\) => void createAdminWindow\(\)/);
  assert.match(main, /label: "打开企业管理台", click: \(\) => void createAdminWindow\(\)/);
  assert.match(main, /new URL\("\/admin\.html", process\.env\.ELECTRON_RENDERER_URL\)/);
  assert.match(main, /loadFile\(join\(__dirname, "\.\.\/renderer\/admin\.html"\)\)/);
  assert.match(main, /preload: join\(__dirname, "\.\.\/preload\/index\.mjs"\)/);
});
