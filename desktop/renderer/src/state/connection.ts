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
