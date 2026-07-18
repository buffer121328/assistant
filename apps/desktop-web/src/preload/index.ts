import { contextBridge, ipcRenderer } from "electron";

export type DesktopSettings = {
  apiBaseUrl: string;
  userId: string;
  defaultWorkdir: string;
  defaultModelClass: "light" | "standard";
  approvalPolicy: "ask" | "require_high_risk" | "read_only";
};

const desktopApi = {
  loadSettings: (): Promise<DesktopSettings> => ipcRenderer.invoke("settings:load"),
  saveSettings: (settings: DesktopSettings): Promise<DesktopSettings> =>
    ipcRenderer.invoke("settings:save", settings),
  openPath: (path: string): Promise<string> => ipcRenderer.invoke("path:open", path),
  openExternal: (url: string): Promise<void> => ipcRenderer.invoke("external:open", url)
};

contextBridge.exposeInMainWorld("assistantDesktop", desktopApi);

export type AssistantDesktopApi = typeof desktopApi;
