import { contextBridge, ipcRenderer } from "electron";

// Inline channel names so the sandboxed preload needs no require('./ipc').
const IPC = {
  GET_CONNECTION_INFO: "connection:get-info",
  OPEN_EXTERNAL: "shell:open-external",
  OPEN_FILE_DIALOG: "dialog:open-file",
  CONNECTION_STATUS_CHANGED: "connection:status-changed",
  PYTHON_LOG_LINE: "python:log-line",
  WRITE_AUTH_MARKER: "authz:write-marker",
} as const;

export type ConnectionInfo = {
  status: "starting" | "ready" | "error" | "exited";
  port: number | null;
  token: string | null;
  errorMessage: string | null;
};

contextBridge.exposeInMainWorld("desktop", {
  getConnectionInfo: (): Promise<ConnectionInfo> =>
    ipcRenderer.invoke(IPC.GET_CONNECTION_INFO),
  onConnectionStatusChanged: (cb: (info: ConnectionInfo) => void) => {
    const listener = (_: unknown, info: ConnectionInfo) => cb(info);
    ipcRenderer.on(IPC.CONNECTION_STATUS_CHANGED, listener);
    return () => ipcRenderer.removeListener(IPC.CONNECTION_STATUS_CHANGED, listener);
  },
  onPythonLogLine: (cb: (line: string) => void) => {
    const listener = (_: unknown, line: string) => cb(line);
    ipcRenderer.on(IPC.PYTHON_LOG_LINE, listener);
    return () => ipcRenderer.removeListener(IPC.PYTHON_LOG_LINE, listener);
  },
  openExternal: (url: string): Promise<void> =>
    ipcRenderer.invoke(IPC.OPEN_EXTERNAL, url),
  openFileDialog: (): Promise<string | null> =>
    ipcRenderer.invoke(IPC.OPEN_FILE_DIALOG),
  writeAuthMarker: (): Promise<string> =>
    ipcRenderer.invoke(IPC.WRITE_AUTH_MARKER),
});

declare global {
  interface Window {
    desktop: {
      getConnectionInfo: () => Promise<ConnectionInfo>;
      onConnectionStatusChanged: (cb: (info: ConnectionInfo) => void) => () => void;
      onPythonLogLine: (cb: (line: string) => void) => () => void;
      openExternal: (url: string) => Promise<void>;
      openFileDialog: () => Promise<string | null>;
      writeAuthMarker: () => Promise<string>;
    };
  }
}
