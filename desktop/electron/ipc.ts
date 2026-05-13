/**
 * IPC channel names shared between main and preload.
 * Keeping them in one file prevents typo-driven channel mismatches.
 */
export const IPC = {
  // Renderer → Main (invoke/reply)
  GET_CONNECTION_INFO: "connection:get-info",
  OPEN_EXTERNAL: "shell:open-external",
  OPEN_FILE_DIALOG: "dialog:open-file",

  // Main → Renderer (send)
  CONNECTION_STATUS_CHANGED: "connection:status-changed",
  PYTHON_LOG_LINE: "python:log-line",
} as const;
