import { Link } from "react-router-dom";
import { useProfiles, useSessions } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export function Dashboard() {
  const profiles = useProfiles();
  const sessions = useSessions();
  const recent = (sessions.data?.sessions ?? [])
    .slice()
    .sort((a, b) => (b.stopped_at ?? "").localeCompare(a.stopped_at ?? ""))
    .slice(0, 8);

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-center mb-6 gap-4">
        <h2 className="text-base font-medium">Dashboard</h2>
        <Link to="/new" className="ml-auto">
          <Button>New engagement</Button>
        </Link>
      </div>

      <Card>
        <CardHeader><CardTitle>Recent sessions</CardTitle></CardHeader>
        <CardContent className="text-xs space-y-1 font-mono">
          {recent.length === 0 && <p className="text-neutral-500">no sessions yet</p>}
          {recent.map((s) => (
            <Link key={s.id} to={`/session/${s.id}`}
                  className="flex gap-3 py-1 hover:bg-neutral-800 rounded px-2 transition-colors">
              <span className={
                s.state === "active" ? "text-green-400" :
                s.state === "stopped" ? "text-amber-400" :
                s.state === "completed" ? "text-blue-400" : "text-neutral-500"
              }>● {s.state}</span>
              <span className="text-neutral-300 truncate">{s.target}</span>
              <span className="text-neutral-500">· {s.profile}</span>
              <span className="text-neutral-500 ml-auto">{s.stopped_at ?? "—"}</span>
            </Link>
          ))}
        </CardContent>
      </Card>

      <div className="mt-6">
        <h3 className="text-sm font-medium mb-3">Profiles ({profiles.data?.profiles.length ?? 0})</h3>
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          {profiles.data?.profiles.map((p) => (
            <Card key={p.key}>
              <CardHeader>
                <CardTitle>{p.name}</CardTitle>
                <div className="text-[10px] uppercase tracking-wide text-neutral-500 mt-0.5">{p.key}</div>
              </CardHeader>
              <CardContent className="text-xs">
                <p className="text-neutral-400 line-clamp-3">{p.description || "—"}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
