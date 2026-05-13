import { useEffect, useState } from "react";
import type { ConnectionInfo } from "../../electron/preload";

export default function App() {
  const [info, setInfo] = useState<ConnectionInfo | null>(null);
  const [logs, setLogs] = useState<string[]>([]);

  useEffect(() => {
    window.desktop.getConnectionInfo().then(setInfo);
    const off1 = window.desktop.onConnectionStatusChanged(setInfo);
    const off2 = window.desktop.onPythonLogLine((line) =>
      setLogs((prev) => [...prev.slice(-199), line])
    );
    return () => { off1(); off2(); };
  }, []);

  return (
    <div className="h-full w-full p-6 flex flex-col gap-4">
      <h1 className="text-lg font-medium">reverser desktop</h1>
      <div className="rounded border border-neutral-800 p-4 text-sm">
        <div>status: <span className="font-mono">{info?.status ?? "?"}</span></div>
        <div>port: <span className="font-mono">{info?.port ?? "—"}</span></div>
        <div>token: <span className="font-mono">{info?.token ? `${info.token.slice(0, 8)}…` : "—"}</span></div>
        {info?.errorMessage && (
          <div className="text-red-400 mt-2">{info.errorMessage}</div>
        )}
      </div>
      <div className="flex-1 rounded border border-neutral-800 p-3 overflow-auto text-xs font-mono text-neutral-400">
        {logs.length === 0 ? <em>no python logs yet</em> : logs.map((l, i) => (
          <div key={i}>{l}</div>
        ))}
      </div>
    </div>
  );
}
