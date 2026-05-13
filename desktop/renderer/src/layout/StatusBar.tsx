import { useConnection } from "@/state/connection";

export function StatusBar() {
  const status = useConnection((s) => s.status);
  const port = useConnection((s) => s.port);

  return (
    <header className="h-9 border-b border-neutral-800 bg-neutral-950/80 flex items-center px-3 text-xs text-neutral-400 gap-4 font-mono">
      <span className="text-neutral-200">reverser</span>
      <span>·</span>
      <span>
        backend:{" "}
        <span className={status === "ready" ? "text-green-400" : status === "exited" ? "text-red-400" : "text-amber-400"}>
          {status}
        </span>
      </span>
      <span>·</span>
      <span>:{port ?? "—"}</span>
      <span className="ml-auto text-neutral-500">no active engagement</span>
    </header>
  );
}
