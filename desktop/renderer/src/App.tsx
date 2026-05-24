import { useEffect } from "react";
import { Routes, Route, Navigate, useParams } from "react-router-dom";
import { Shell } from "@/layout/Shell";
import { Health } from "@/pages/Health";
import { Settings } from "@/pages/Settings";
import { CrashScreen } from "@/pages/CrashScreen";
import { NewEngagement } from "@/pages/NewEngagement";
import { SessionLayout } from "@/layout/SessionLayout";
import { SessionsIndex } from "@/pages/SessionsIndex";
import { TargetsIndex } from "@/pages/TargetsIndex";
import { TargetOverview } from "@/pages/TargetOverview";
import { useConnection } from "@/state/connection";

function LegacySessionRedirect() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/sessions/${id}`} replace />;
}

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
        <Route index element={<Navigate to="/sessions" replace />} />
        <Route path="/new" element={<NewEngagement />} />
        <Route path="/sessions" element={<SessionsIndex />} />
        <Route path="/sessions/:id" element={<SessionLayout />} />
        <Route path="/targets" element={<TargetsIndex />} />
        <Route path="/target/:name" element={<TargetOverview />} />
        <Route path="/health" element={<Health />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
      <Route path="/session/:id" element={<LegacySessionRedirect />} />
    </Routes>
  );
}
