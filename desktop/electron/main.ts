import { app, BrowserWindow, ipcMain, shell, dialog } from "electron";
import path from "path";
import { IPC } from "./ipc";

let mainWindow: BrowserWindow | null = null;

let connectionInfo: {
  status: "starting" | "ready" | "error" | "exited";
  port: number | null;
  token: string | null;
  errorMessage: string | null;
} = {
  status: "starting",
  port: null,
  token: null,
  errorMessage: null,
};

function broadcastConnectionInfo() {
  mainWindow?.webContents.send(IPC.CONNECTION_STATUS_CHANGED, connectionInfo);
}

export function setConnectionInfo(
  next: Partial<typeof connectionInfo>
): void {
  connectionInfo = { ...connectionInfo, ...next };
  broadcastConnectionInfo();
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 600,
    backgroundColor: "#0a0a0a",
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    await mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    await mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

// IPC handlers
ipcMain.handle(IPC.GET_CONNECTION_INFO, () => connectionInfo);

ipcMain.handle(IPC.OPEN_EXTERNAL, async (_event, url: string) => {
  // Only allow http(s) and file:// — never arbitrary protocol handlers
  if (!/^(https?:|file:)/.test(url)) {
    throw new Error("refusing to open non-http/file URL");
  }
  await shell.openExternal(url);
});

ipcMain.handle(IPC.OPEN_FILE_DIALOG, async () => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openFile"],
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
});

app.whenReady().then(async () => {
  await createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
