import { useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "@/layout/Shell";
import { Dashboard } from "@/pages/Dashboard";
import { Health } from "@/pages/Health";
import { Settings } from "@/pages/Settings";
import { CrashScreen } from "@/pages/CrashScreen";
import { NewEngagement } from "@/pages/NewEngagement";
import { SessionLayout } from "@/layout/SessionLayout";
import { useConnection } from "@/state/connection";

export default function App() {
  const status = useConnection((s) => s.status);
  const setInfo = useConnection((s) => s.setInfo);

  useEffect(() => {
    window.desktop.getConnectionInfo().then(setInfo);
    return window.desktop.onConnectionStatusChanged(setInfo);
  }, [setInfo]);

  if (status === "exited") return <CrashScreen />;
  if (status === "starting") {
    return (
      <div className="h-full flex items-center justify-center text-sm text-neutral-500">
        starting backend…
      </div>
    );
  }

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Dashboard />} />
        <Route path="/new" element={<NewEngagement />} />
        <Route path="/health" element={<Health />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
      <Route path="/session/:id" element={<SessionLayout />} />
    </Routes>
  );
}
