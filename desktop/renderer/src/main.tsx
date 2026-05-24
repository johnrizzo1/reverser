import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";
import { getSessionStore } from "@/state/session-store";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false },
  },
});

// E2E test hook: expose helpers on window so Playwright specs can drive the
// in-process store without a real backend. Mirrors the __reverserConnection
// pattern in state/connection.ts — harmless in production.
if (typeof window !== "undefined") {
  (window as unknown as Record<string, unknown>).__getSessionStore =
    getSessionStore;
  (window as unknown as Record<string, unknown>).__getQueryClient =
    () => queryClient;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
