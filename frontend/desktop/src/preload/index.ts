import { contextBridge, ipcRenderer, webUtils } from "electron";

export type DesktopSettings = {
  apiBaseUrl: string;
  defaultWorkdir: string;
  defaultModelClass: "light" | "standard";
  approvalPolicy: "ask" | "require_high_risk" | "read_only";
};

export type AuthenticatedSession = {
  expires_at: string;
  tenant_id: string;
  user_id: string;
  display_name: string;
  organization_ids: string[];
  organization_names?: string[];
  roles: string[];
  authority_revision: number;
};

type BackendResponse = { status: number; body: unknown };
type ApiRequest = { path: string; method?: string; body?: unknown };

const desktopApi = {
  // Settings contain only local display preferences; identity and session material stay in main.
  loadSettings: (): Promise<DesktopSettings> => ipcRenderer.invoke("settings:load"),
  saveSettings: (settings: DesktopSettings): Promise<DesktopSettings> =>
    ipcRenderer.invoke("settings:save", settings),
  /** Resolve current identity or first-run status without returning the opaque session token. */
  authState: (baseUrl: string): Promise<{ initializationRequired: boolean; session: AuthenticatedSession | null }> =>
    ipcRenderer.invoke("auth:state", baseUrl),
  /** Initialize the first administrator; the main process stores the returned session securely. */
  initialize: (args: { baseUrl: string; displayName: string; loginName: string; password: string }): Promise<BackendResponse> =>
    ipcRenderer.invoke("auth:initialize", args),
  /** Sign in an existing employee; raw credentials are used only for this IPC call. */
  login: (args: { baseUrl: string; loginName: string; password: string }): Promise<BackendResponse> =>
    ipcRenderer.invoke("auth:login", args),
  recoveryStatus: (baseUrl: string): Promise<BackendResponse> => ipcRenderer.invoke("auth:recovery-status", baseUrl),
  recoverPassword: (args: { baseUrl: string; loginName: string; answer: string; newPassword: string }): Promise<BackendResponse> =>
    ipcRenderer.invoke("auth:recover-password", args),
  /** Revoke the server session and clear the encrypted local session blob. */
  logout: (baseUrl: string): Promise<void> => ipcRenderer.invoke("auth:logout", baseUrl),
  /** Proxy authenticated JSON API requests through main; renderer never receives a session token. */
  apiRequest: (baseUrl: string, request: ApiRequest): Promise<BackendResponse> =>
    ipcRenderer.invoke("api:request", { baseUrl, request }),
  /** Upload an explicitly selected file through main so the session header is preserved. */
  uploadResource: (baseUrl: string, request: { path: string; userId: string; name: string; type: string; bytes: ArrayBuffer }): Promise<BackendResponse> =>
    ipcRenderer.invoke("api:upload", { baseUrl, request }),
  /** Open a native file-only picker; cancellation returns no local paths. */
  chooseContextFiles: (): Promise<string[]> => ipcRenderer.invoke("context:choose-files"),
  /** Resolve only a browser File created by an explicit desktop drop gesture. */
  pathForDroppedFile: (file: File): string => webUtils.getPathForFile(file),
  openPath: (path: string): Promise<string> => ipcRenderer.invoke("path:open", path),
  openExternal: (url: string): Promise<void> => ipcRenderer.invoke("external:open", url),
  /** Download an artifact through main into the local hash-addressed cache and open it. */
  downloadArtifact: (args: { baseUrl: string; artifactId: string; filename: string; contentHash: string }): Promise<{ cached: boolean; path: string }> =>
    ipcRenderer.invoke("artifacts:download", args)
};

contextBridge.exposeInMainWorld("assistantDesktop", desktopApi);

export type AssistantDesktopApi = typeof desktopApi;
