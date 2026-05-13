import { useEffect } from "react";
import { useConnection } from "@/state/connection";
import { getSessionStore, type WSFrame } from "@/state/session-store";

/**
 * Open exactly one WebSocket per session_id. The connection lives for as
 * long as the hook is mounted. Reconnect on disconnect is intentionally
 * NOT implemented in Phase 1 — the session ends with the UI per spec
 * lifecycle A. (Phase 4 may add reconnect for crash-recovery UX.)
 */
export function useSessionStream(sessionId: string | null) {
  const port = useConnection((s) => s.port);
  const token = useConnection((s) => s.token);
  const ready = useConnection((s) => s.status === "ready");

  useEffect(() => {
    if (!sessionId || !ready || !port || !token) return;
    const url = `ws://127.0.0.1:${port}/ws/sessions/${sessionId}?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    const store = getSessionStore(sessionId);
    ws.onmessage = (ev) => {
      try {
        const frame = JSON.parse(ev.data) as WSFrame;
        store.getState().ingest(frame);
      } catch {
        store.getState().ingest({ type: "log", level: "warn", msg: "non-JSON WS frame" });
      }
    };
    ws.onclose = () => {
      store.getState().ingest({ type: "log", level: "info", msg: "websocket closed" });
    };
    return () => {
      try { ws.close(); } catch { /* ignore */ }
    };
  }, [sessionId, ready, port, token]);
}
