import { create } from "zustand";
import type { ConnectionInfo } from "../../../electron/preload";

type ConnectionState = ConnectionInfo & {
  setInfo: (info: ConnectionInfo) => void;
};

export const useConnection = create<ConnectionState>((set) => ({
  status: "starting",
  port: null,
  token: null,
  errorMessage: null,
  setInfo: (info) => set(info),
}));

/** Mount-once hook: subscribe to main-process connection updates. */
export function useConnectionSubscription() {
  // Note: caller is responsible for calling this exactly once at app root.
  // Subsequent components read state via useConnection() selectors.
}

// Test-only handle: expose the store on `window` so Playwright e2e specs can
// read the live port + token without parsing UI text. Cheap and harmless in
// production (it's just a reference to the existing store).
if (typeof window !== "undefined") {
  (window as unknown as Record<string, unknown>).__reverserConnection = useConnection;
}
