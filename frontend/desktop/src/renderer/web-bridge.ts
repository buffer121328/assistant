import type { AssistantDesktopApi, AuthenticatedSession, DesktopSettings } from "../preload";

type BackendResponse = { status: number; body: unknown };
type ApiRequest = { path: string; method?: string; body?: unknown };

const tokenKey = "assistant.web.session";
const defaultSettings: DesktopSettings = {
  apiBaseUrl: (import.meta.env.VITE_WEB_API_BASE_URL as string | undefined)?.trim() || window.location.origin,
  defaultWorkdir: "",
  defaultModelClass: "light",
  approvalPolicy: "ask"
};

function apiUrl(baseUrl: string, path: string): string {
  if (!path.startsWith("/") || path.startsWith("//")) throw new Error("invalid_api_path");
  return new URL(path, baseUrl).toString();
}

async function request(baseUrl: string, input: ApiRequest, token = sessionStorage.getItem(tokenKey)): Promise<BackendResponse> {
  try {
    const response = await fetch(apiUrl(baseUrl, input.path), {
      method: input.method || "GET",
      headers: { "content-type": "application/json", ...(token ? { "X-Assistant-Session": token } : {}) },
      body: input.body === undefined ? undefined : JSON.stringify(input.body),
      credentials: "same-origin"
    });
    const text = await response.text();
    let body: unknown = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = { detail: "服务端返回了无法读取的响应。" }; }
    if (response.status === 401 || response.status === 403) sessionStorage.removeItem(tokenKey);
    return { status: response.status, body };
  } catch {
    return { status: 503, body: { detail: "无法连接服务，请检查 Web/API 服务状态后重试。" } };
  }
}

const webApi: AssistantDesktopApi = {
  loadSettings: async () => ({ ...defaultSettings }),
  saveSettings: async (settings) => ({ ...defaultSettings, ...settings }),
  authState: async (baseUrl) => {
    const token = sessionStorage.getItem(tokenKey);
    const session = token ? await request(baseUrl, { path: "/api/auth/session" }, token) : { status: 401, body: null };
    const initialization = await request(baseUrl, { path: "/api/auth/initialization-status" }, null);
    return {
      initializationRequired: Boolean((initialization.body as { initialization_required?: unknown } | null)?.initialization_required),
      session: session.status >= 200 && session.status < 300 ? session.body as AuthenticatedSession : null
    };
  },
  initialize: async ({ baseUrl, displayName, loginName, password }) => {
    const response = await request(baseUrl, { path: "/api/auth/initialize", method: "POST", body: { display_name: displayName, login_name: loginName, password } }, null);
    const token = response.body && typeof response.body === "object" ? (response.body as Record<string, unknown>).session_token : null;
    if (response.status >= 200 && response.status < 300 && typeof token === "string") sessionStorage.setItem(tokenKey, token);
    return response;
  },
  login: async ({ baseUrl, loginName, password }) => {
    const response = await request(baseUrl, { path: "/api/auth/login", method: "POST", body: { login_name: loginName, password } }, null);
    const token = response.body && typeof response.body === "object" ? (response.body as Record<string, unknown>).session_token : null;
    if (response.status >= 200 && response.status < 300 && typeof token === "string") sessionStorage.setItem(tokenKey, token);
    return response;
  },
  recoveryStatus: async (baseUrl) => request(baseUrl, { path: "/api/auth/recovery-status" }, null),
  recoverPassword: async ({ baseUrl, loginName, answer, newPassword }) => {
    const response = await request(baseUrl, { path: "/api/auth/recover-password", method: "POST", body: { login_name: loginName, answer, new_password: newPassword } }, null);
    const token = response.body && typeof response.body === "object" ? (response.body as Record<string, unknown>).session_token : null;
    if (response.status >= 200 && response.status < 300 && typeof token === "string") sessionStorage.setItem(tokenKey, token);
    return response;
  },
  logout: async (baseUrl) => { await request(baseUrl, { path: "/api/auth/logout", method: "POST" }); sessionStorage.removeItem(tokenKey); },
  apiRequest: (baseUrl, input) => request(baseUrl, input),
  uploadResource: async () => ({ status: 501, body: { detail: "浏览器管理台不支持本地文件上传。" } }),
  chooseContextFiles: async () => [],
  pathForDroppedFile: () => "",
  openPath: async () => "",
  openExternal: async (url) => { window.open(url, "_blank", "noopener,noreferrer"); },
  downloadArtifact: async ({ baseUrl, artifactId }) => {
    // Web builds have no local cache; hand the authorized URL to a new tab.
    const downloadUrl = `${baseUrl}/local/artifacts/${encodeURIComponent(artifactId)}/download`;
    window.open(downloadUrl, "_blank", "noopener,noreferrer");
    return { cached: false, path: downloadUrl };
  }
};

export function installWebBridge(): void {
  window.assistantDesktop = webApi;
}
