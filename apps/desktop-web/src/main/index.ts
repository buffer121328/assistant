import { app, BrowserWindow, Menu, nativeImage, shell, Tray, ipcMain } from "electron";
import { join } from "node:path";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";

type DesktopSettings = {
  apiBaseUrl: string;
  userId: string;
  defaultWorkdir: string;
  defaultModelClass: "light" | "standard";
  approvalPolicy: "ask" | "require_high_risk" | "read_only";
};

const DEFAULT_SETTINGS: DesktopSettings = {
  apiBaseUrl: "http://127.0.0.1:8000",
  userId: "",
  defaultWorkdir: "",
  defaultModelClass: "standard",
  approvalPolicy: "ask"
};

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;

function settingsPath(): string {
  return join(app.getPath("userData"), "desktop-settings.json");
}

async function loadSettings(): Promise<DesktopSettings> {
  const path = settingsPath();
  if (!existsSync(path)) {
    return DEFAULT_SETTINGS;
  }
  try {
    const raw = await readFile(path, "utf8");
    return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

async function saveSettings(next: DesktopSettings): Promise<DesktopSettings> {
  const normalized = { ...DEFAULT_SETTINGS, ...next };
  await mkdir(app.getPath("userData"), { recursive: true });
  await writeFile(settingsPath(), JSON.stringify(normalized, null, 2), "utf8");
  return normalized;
}

function createMenu(): void {
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: "Assistant",
        submenu: [
          { role: "about" },
          { type: "separator" },
          { role: "quit" }
        ]
      },
      {
        label: "View",
        submenu: [
          { role: "reload" },
          { role: "toggleDevTools" },
          { type: "separator" },
          { role: "resetZoom" },
          { role: "zoomIn" },
          { role: "zoomOut" }
        ]
      }
    ])
  );
}

function createTray(): void {
  const image = nativeImage.createEmpty();
  tray = new Tray(image);
  tray.setToolTip("Assistant");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: "Show Assistant",
        click: () => mainWindow?.show()
      },
      {
        label: "Quit",
        click: () => app.quit()
      }
    ])
  );
}

async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 980,
    minHeight: 640,
    show: false,
    title: "Assistant",
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow?.show();
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });

  if (process.env.ELECTRON_RENDERER_URL) {
    await mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL);
  } else {
    await mainWindow.loadFile(join(__dirname, "../renderer/index.html"));
  }
}

ipcMain.handle("settings:load", async () => loadSettings());
ipcMain.handle("settings:save", async (_event, next: DesktopSettings) => saveSettings(next));
ipcMain.handle("path:open", async (_event, path: string) => shell.openPath(path));
ipcMain.handle("external:open", async (_event, url: string) => shell.openExternal(url));

app.whenReady().then(async () => {
  createMenu();
  createTray();
  await createWindow();
});

app.on("activate", async () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    await createWindow();
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
