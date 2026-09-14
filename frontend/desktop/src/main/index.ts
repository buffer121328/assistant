import { app, BrowserWindow, Menu, nativeImage, shell, Tray, ipcMain, dialog, safeStorage } from "electron";
import { join } from "node:path";
import { readFileSync, existsSync } from "node:fs";
import { readFile, writeFile, mkdir, stat, utimes, readdir, rm, rename } from "node:fs/promises";

export type DesktopSettings = {
  apiBaseUrl: string;
  defaultWorkdir: string;
  defaultModelClass: "light" | "standard";
  approvalPolicy: "ask" | "require_high_risk" | "read_only";
};

type ApiRequest = { path: string; method?: string; body?: unknown };
type UploadRequest = { path: string; userId: string; name: string; type: string; bytes: ArrayBuffer };
type BackendResponse = { status: number; body: unknown };

const BRAND_NAME = "贾维斯助理";
const DEFAULT_SETTINGS: DesktopSettings = {
  apiBaseUrl: "http://127.0.0.1:18080",
  defaultWorkdir: "",
  defaultModelClass: "light",
  approvalPolicy: "ask"
};
const MAX_CONTEXT_FILES = 32;
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
const LOCAL_API_REQUEST_TIMEOUT_MS = 15_000;

let mainWindow: BrowserWindow | null = null;
let adminWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let inMemorySessionToken: string | null = null;

/** Return the per-user settings file used by the Electron main process. */
function settingsPath(): string {
  return join(app.getPath("userData"), "desktop-settings.json");
}

/** Read the bundled brand mark into a NativeImage with a harmless empty-image fallback. */
function applicationIcon(): Electron.NativeImage {
  const iconPath = app.isPackaged
    ? join(process.resourcesPath, "icons", "jarvis-mark.svg")
    : join(app.getAppPath(), "build", "icons", "icon.svg");
  try {
    const encoded = Buffer.from(readFileSync(iconPath)).toString("base64");
    return nativeImage.createFromDataURL(`data:image/svg+xml;base64,${encoded}`);
  } catch {
    return nativeImage.createEmpty();
  }
}

/** Normalize settings and deliberately discard legacy hand-entered identity fields. */
function normalizeSettings(value: unknown): DesktopSettings {
  const input = value && typeof value === "object" ? value as Record<string, unknown> : {};
  return {
    apiBaseUrl: typeof input.apiBaseUrl === "string" && input.apiBaseUrl.trim()
      ? input.apiBaseUrl.trim()
      : DEFAULT_SETTINGS.apiBaseUrl,
    defaultWorkdir: typeof input.defaultWorkdir === "string" ? input.defaultWorkdir : DEFAULT_SETTINGS.defaultWorkdir,
    defaultModelClass: input.defaultModelClass === "light" ? "light" : DEFAULT_SETTINGS.defaultModelClass,
    approvalPolicy: input.approvalPolicy === "require_high_risk" || input.approvalPolicy === "read_only"
      ? input.approvalPolicy
      : DEFAULT_SETTINGS.approvalPolicy
  };
}

/** Load persisted settings and fall back to safe local defaults on invalid data. */
async function loadSettings(): Promise<DesktopSettings> {
  const path = settingsPath();
  if (!existsSync(path)) return DEFAULT_SETTINGS;
  try {
    const raw = await readFile(path, "utf8");
    return normalizeSettings(JSON.parse(raw));
  } catch {
    return DEFAULT_SETTINGS;
  }
}

/** Normalize and persist settings received from the renderer process. */
async function saveSettings(next: DesktopSettings): Promise<DesktopSettings> {
  const normalized = normalizeSettings(next);
  await mkdir(app.getPath("userData"), { recursive: true });
  await writeFile(settingsPath(), JSON.stringify(normalized, null, 2), "utf8");
  return normalized;
}

function sessionPath(): string {
  return join(app.getPath("userData"), "desktop-session.bin");
}

/** Read only the encrypted session blob; the raw token never enters renderer memory. */
async function readSessionToken(): Promise<string | null> {
  if (inMemorySessionToken) return inMemorySessionToken;
  if (!safeStorage.isEncryptionAvailable() || !existsSync(sessionPath())) return null;
  try {
    const encoded = await readFile(sessionPath(), "utf8");
    inMemorySessionToken = safeStorage.decryptString(Buffer.from(encoded, "base64"));
    return inMemorySessionToken;
  } catch {
    return null;
  }
}

/** Encrypt and persist a newly issued opaque server session. */
async function storeSessionToken(token: string): Promise<void> {
  inMemorySessionToken = token;
  if (!safeStorage.isEncryptionAvailable()) return;
  await mkdir(app.getPath("userData"), { recursive: true });
  const encrypted = safeStorage.encryptString(token).toString("base64");
  await writeFile(sessionPath(), encrypted, "utf8");
}

/** Revoke local access material without exposing the token to the renderer. */
async function clearSessionToken(): Promise<void> {
  inMemorySessionToken = null;
  try {
    await writeFile(sessionPath(), "", "utf8");
  } catch {
    // A missing or unavailable session file is already a signed-out state.
  }
}

function backendUrl(baseUrl: string, path: string): string {
  if (!path.startsWith("/") || path.startsWith("//")) throw new Error("invalid_api_path");
  const parsed = new URL(baseUrl);
  const localHosts = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
  if (
    (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
    parsed.username ||
    parsed.password ||
    !localHosts.has(parsed.hostname) ||
    (parsed.pathname !== "" && parsed.pathname !== "/")
  ) {
    throw new Error("invalid_api_url");
  }
  return new URL(path, parsed).toString();
}

/** Proxy bounded JSON requests through main so renderer code never receives the session token. */
async function readBackendResponse(response: Response): Promise<BackendResponse> {
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: "Backend returned an unreadable response." };
    }
  }
  return { status: response.status, body };
}

/** Normalize local transport failures without exposing token or connection details to the renderer. */
function localRequestFailure(reason: unknown): BackendResponse {
  const timedOut = reason instanceof Error && (reason.name === "AbortError" || reason.name === "TimeoutError");
  return {
    status: timedOut ? 504 : 503,
    body: {
      detail: timedOut
        ? "本地服务响应超时，请稍候查看任务列表或重试。"
        : "无法连接本地服务，请检查服务状态后重试。"
    }
  };
}

async function requestBackend(baseUrl: string, request: ApiRequest, token?: string | null): Promise<BackendResponse> {
  try {
    const response = await fetch(backendUrl(baseUrl, request.path), {
      method: request.method || "GET",
      headers: {
        "content-type": "application/json",
        ...(request.method && request.method !== "GET" ? { "Idempotency-Key": crypto.randomUUID() } : {}),
        ...(token ? { "X-Assistant-Session": token } : {})
      },
      body: request.body === undefined ? undefined : JSON.stringify(request.body),
      signal: AbortSignal.timeout(LOCAL_API_REQUEST_TIMEOUT_MS)
    });
    return readBackendResponse(response);
  } catch (reason) {
    return localRequestFailure(reason);
  }
}

async function uploadBackend(baseUrl: string, request: UploadRequest, token?: string | null): Promise<BackendResponse> {
  if (request.bytes.byteLength > MAX_UPLOAD_BYTES) return { status: 413, body: { detail: "文件不能超过 25 MB。" } };
  const form = new FormData();
  form.set("user_id", request.userId);
  form.set("file", new Blob([request.bytes], { type: request.type || "application/octet-stream" }), request.name);
  try {
    const response = await fetch(backendUrl(baseUrl, request.path), {
      method: "POST",
      headers: token ? { "X-Assistant-Session": token } : undefined,
      body: form,
      signal: AbortSignal.timeout(LOCAL_API_REQUEST_TIMEOUT_MS)
    });
    return readBackendResponse(response);
  } catch (reason) {
    return localRequestFailure(reason);
  }
}

function sessionWithoutToken(body: unknown): unknown {
  if (!body || typeof body !== "object") return body;
  const { session_token: _sessionToken, ...safe } = body as Record<string, unknown>;
  return safe;
}

async function authenticate(baseUrl: string, path: string, body: unknown): Promise<BackendResponse> {
  const response = await requestBackend(baseUrl, { path, method: "POST", body });
  if (response.status >= 200 && response.status < 300 && response.body && typeof response.body === "object") {
    const token = (response.body as Record<string, unknown>).session_token;
    if (typeof token === "string" && token) {
      await storeSessionToken(token);
      return { ...response, body: sessionWithoutToken(response.body) };
    }
  }
  return response;
}

const ARTIFACT_CACHE_MAX_BYTES = 500 * 1024 * 1024;
const ARTIFACT_CACHE_EVICT_TARGET = 400 * 1024 * 1024;

/** Resolve the content-addressed artifact cache directory and keep it below budget. */
async function artifactCacheDir(): Promise<string> {
  const cacheRoot = join(app.getPath("userData"), "artifact-cache");
  await mkdir(cacheRoot, { recursive: true });
  return cacheRoot;
}

/** Evict least-recently-used cached artifacts until total size fits the budget. */
async function evictArtifactCache(cacheRoot: string): Promise<void> {
  const entries = await readdir(cacheRoot);
  const stats: Array<{ file: string; size: number; atime: number }> = [];
  let total = 0;
  for (const file of entries) {
    const info = await stat(join(cacheRoot, file)).catch(() => null);
    if (!info?.isFile()) continue;
    stats.push({ file, size: info.size, atime: info.atimeMs });
    total += info.size;
  }
  if (total <= ARTIFACT_CACHE_MAX_BYTES) return;
  stats.sort((a, b) => a.atime - b.atime);
  for (const item of stats) {
    if (total <= ARTIFACT_CACHE_EVICT_TARGET) break;
    await rm(join(cacheRoot, item.file), { force: true });
    total -= item.size;
  }
}

/** Download one artifact through the authenticated tunnel into the hash-addressed cache and open it. */
async function downloadArtifactWithCache(args: {
  baseUrl: string;
  artifactId: string;
  filename: string;
  contentHash: string;
}): Promise<{ cached: boolean; path: string }> {
  const safeHash = args.contentHash.replace(/[^a-f0-9]/gi, "").slice(0, 64) || "unknown";
  const safeName = args.filename.replace(/[/\\\x00]/g, "_").slice(0, 120) || "artifact";
  const cacheRoot = await artifactCacheDir();
  const cachedPath = join(cacheRoot, `${safeHash}-${safeName}`);
  try {
    const info = await stat(cachedPath);
    if (info.isFile() && info.size > 0) {
      await utimes(cachedPath, new Date(), new Date());
      await shell.openPath(cachedPath);
      return { cached: true, path: cachedPath };
    }
  } catch {
    // Cache miss falls through to a fresh authenticated download.
  }
  const token = await readSessionToken();
  if (!token) throw new Error("session_required");
  const url = backendUrl(args.baseUrl, `/local/artifacts/${encodeURIComponent(args.artifactId)}/download`);
  const response = await fetch(url, {
    headers: { "X-Assistant-Session": token },
    signal: AbortSignal.timeout(120_000)
  });
  if (!response.ok) throw new Error(`download_failed_${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  const tempPath = `${cachedPath}.part-${process.pid}-${Date.now()}`;
  await writeFile(tempPath, bytes);
  await rename(tempPath, cachedPath).catch(async () => {
    await rm(tempPath, { force: true });
    throw new Error("download_cache_write_failed");
  });
  void evictArtifactCache(cacheRoot).catch(() => {});
  await shell.openPath(cachedPath);
  return { cached: false, path: cachedPath };
}

/** Install the native application menu without exposing renderer capabilities. */
function createMenu(): void {
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: BRAND_NAME,
        submenu: [{ role: "about", label: `关于${BRAND_NAME}` }, { type: "separator" }, { role: "quit", label: "退出" }]
      },
      {
        label: "工作区",
        submenu: [
          { label: "员工工作台", click: () => mainWindow?.show() },
          { label: "企业管理台", click: () => void createAdminWindow() }
        ]
      },
      {
        label: "编辑",
        submenu: [
          { role: "undo", label: "撤销" },
          { role: "redo", label: "重做" },
          { type: "separator" },
          { role: "cut", label: "剪切" },
          { role: "copy", label: "复制" },
          { role: "paste", label: "粘贴" },
          { role: "delete", label: "删除" },
          { type: "separator" },
          { role: "selectAll", label: "全选" }
        ]
      },
      {
        label: "视图",
        submenu: [
          { role: "reload", label: "重新加载" },
          { role: "toggleDevTools", label: "开发者工具" },
          { type: "separator" },
          { role: "resetZoom", label: "恢复默认缩放" },
          { role: "zoomIn", label: "放大" },
          { role: "zoomOut", label: "缩小" }
        ]
      }
    ])
  );
}

/** Create a visible, branded tray entry point used to restore or quit the workspace. */
function createTray(): void {
  tray = new Tray(applicationIcon());
  tray.setToolTip(BRAND_NAME);
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "显示贾维斯助理", click: () => mainWindow?.show() },
      { label: "打开企业管理台", click: () => void createAdminWindow() },
      { label: "退出", click: () => app.quit() }
    ])
  );
}

/** Keep system text editing shortcuts working in every focused renderer control. */
function installTextEditingShortcuts(window: BrowserWindow): void {
  window.webContents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown" || (!input.meta && !input.control) || input.alt) return;
    const key = input.key.toLowerCase();
    const command =
      key === "c"
        ? "copy"
        : key === "v"
          ? "paste"
          : key === "x"
            ? "cut"
            : key === "a"
              ? "selectAll"
              : key === "z"
                ? input.shift
                  ? "redo"
                  : "undo"
                : null;
    if (!command) return;
    event.preventDefault();
    window.webContents[command]();
  });
}

/** Provide a native context menu for focused text controls and selections. */
function installTextContextMenu(window: BrowserWindow): void {
  window.webContents.on("context-menu", (event, params) => {
    event.preventDefault();
    const hasSelection = params.selectionText.trim().length > 0;
    const menu = Menu.buildFromTemplate([
      { role: "undo", label: "撤销", enabled: params.isEditable },
      { role: "redo", label: "重做", enabled: params.isEditable },
      { type: "separator" },
      { role: "cut", label: "剪切", enabled: params.isEditable && hasSelection },
      { role: "copy", label: "复制", enabled: hasSelection },
      { role: "paste", label: "粘贴", enabled: params.isEditable },
      { role: "delete", label: "删除", enabled: params.isEditable && hasSelection },
      { type: "separator" },
      { role: "selectAll", label: "全选", enabled: params.isEditable || hasSelection }
    ]);
    void menu.popup({ window });
  });
}

/** Open the separately bundled management surface with the same isolated preload proxy. */
async function createAdminWindow(): Promise<void> {
  if (adminWindow && !adminWindow.isDestroyed()) {
    adminWindow.show();
    adminWindow.focus();
    return;
  }
  adminWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 960,
    minHeight: 640,
    show: true,
    backgroundColor: "#eff4fa",
    title: `${BRAND_NAME} · 企业管理`,
    icon: applicationIcon(),
    webPreferences: {
      preload: join(__dirname, "../preload/index.mjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });
  installTextEditingShortcuts(adminWindow);
  installTextContextMenu(adminWindow);
  adminWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
  adminWindow.on("closed", () => { adminWindow = null; });
  if (process.env.ELECTRON_RENDERER_URL) {
    await adminWindow.loadURL(new URL("/admin.html", process.env.ELECTRON_RENDERER_URL).toString());
  } else {
    await adminWindow.loadFile(join(__dirname, "../renderer/admin.html"));
  }
}

/** Create the isolated BrowserWindow and load the Vite or packaged renderer. */
async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 1024,
    minHeight: 680,
    // Render directly into a visible, branded surface. Delaying visibility until
    // `ready-to-show` can leave some macOS Electron windows on an unpainted blank frame.
    show: true,
    backgroundColor: "#eff4fa",
    title: BRAND_NAME,
    icon: applicationIcon(),
    webPreferences: {
      preload: join(__dirname, "../preload/index.mjs"),
      // The current preload is emitted as ESM. Keep renderer isolation and Node disabled,
      // while allowing that limited bridge to load in Electron's preload context.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });

  installTextEditingShortcuts(mainWindow);
  installTextContextMenu(mainWindow);
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.on("closed", () => { mainWindow = null; });

  if (process.env.ELECTRON_RENDERER_URL) {
    await mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL);
  } else {
    await mainWindow.loadFile(join(__dirname, "../renderer/index.html"));
  }
}

ipcMain.handle("settings:load", async () => loadSettings());
ipcMain.handle("settings:save", async (_event, next: DesktopSettings) => saveSettings(next));
ipcMain.handle("auth:state", async (_event, baseUrl: string) => {
  const token = await readSessionToken();
  let session: BackendResponse = { status: 401, body: null };
  if (token) session = await requestBackend(baseUrl, { path: "/api/auth/session" }, token);
  if (session.status === 401 || session.status === 403) await clearSessionToken();
  const initialization = await requestBackend(baseUrl, { path: "/api/auth/initialization-status" });
  return {
    initializationRequired: Boolean((initialization.body as { initialization_required?: unknown } | null)?.initialization_required),
    session: session.status >= 200 && session.status < 300 ? session.body : null
  };
});
ipcMain.handle("auth:initialize", async (_event, args: { baseUrl: string; displayName: string; loginName: string; password: string }) =>
  authenticate(args.baseUrl, "/api/auth/initialize", {
    display_name: args.displayName,
    login_name: args.loginName,
    password: args.password
  })
);
ipcMain.handle("auth:login", async (_event, args: { baseUrl: string; loginName: string; password: string }) =>
  authenticate(args.baseUrl, "/api/auth/login", { login_name: args.loginName, password: args.password })
);
ipcMain.handle("auth:recovery-status", async (_event, baseUrl: string) => requestBackend(baseUrl, { path: "/api/auth/recovery-status" }));
ipcMain.handle("auth:recover-password", async (_event, args: { baseUrl: string; loginName: string; answer: string; newPassword: string }) =>
  authenticate(args.baseUrl, "/api/auth/recover-password", { login_name: args.loginName, answer: args.answer, new_password: args.newPassword })
);
ipcMain.handle("auth:logout", async (_event, baseUrl: string) => {
  const token = await readSessionToken();
  if (token) {
    try { await requestBackend(baseUrl, { path: "/api/auth/logout", method: "POST" }, token); } catch { /* local cleanup still wins */ }
  }
  await clearSessionToken();
});
ipcMain.handle("api:request", async (_event, args: { baseUrl: string; request: ApiRequest }) => {
  const token = await readSessionToken();
  const response = await requestBackend(args.baseUrl, args.request, token);
  if ((response.status === 401 || response.status === 403) && token) await clearSessionToken();
  return response;
});
ipcMain.handle("api:upload", async (_event, args: { baseUrl: string; request: UploadRequest }) => {
  const token = await readSessionToken();
  const response = await uploadBackend(args.baseUrl, args.request, token);
  if ((response.status === 401 || response.status === 403) && token) await clearSessionToken();
  return response;
});
ipcMain.handle("context:choose-files", async () => {
  const result = await dialog.showOpenDialog({ properties: ["openFile", "multiSelections"] });
  if (result.canceled) return [];
  return [...new Set(result.filePaths)].slice(0, MAX_CONTEXT_FILES);
});
ipcMain.handle("path:open", async (_event, path: string) => shell.openPath(path));
ipcMain.handle("external:open", async (_event, url: string) => shell.openExternal(url));
ipcMain.handle("artifacts:download", async (_event, args: { baseUrl: string; artifactId: string; filename: string; contentHash: string }) =>
  downloadArtifactWithCache(args)
);

app.setName(BRAND_NAME);
app.whenReady().then(async () => {
  createMenu();
  createTray();
  await createWindow();
});
app.on("activate", async () => {
  if (BrowserWindow.getAllWindows().length === 0) await createWindow();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
