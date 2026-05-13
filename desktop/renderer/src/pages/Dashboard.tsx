import { useProfiles } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function Dashboard() {
  const { data, isLoading, error } = useProfiles();

  return (
    <div className="p-6 max-w-5xl">
      <h2 className="text-base font-medium mb-1">Profiles</h2>
      <p className="text-xs text-neutral-500 mb-4">
        Engagement profiles registered in the backend. Plan-3 will turn each
        into a "New engagement" entry point.
      </p>

      {isLoading && <p className="text-sm text-neutral-500">loading…</p>}
      {error && (
        <p className="text-sm text-red-400">
          failed to load profiles: {String((error as Error).message)}
        </p>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        {data?.profiles.map((p) => (
          <Card key={p.key}>
            <CardHeader>
              <CardTitle>{p.name}</CardTitle>
              <div className="text-[10px] uppercase tracking-wide text-neutral-500 mt-0.5">
                {p.key}
              </div>
            </CardHeader>
            <CardContent className="text-xs">
              <p className="text-neutral-400 line-clamp-3">{p.description || "—"}</p>
              {p.skills.length > 0 && (
                <p className="text-neutral-500 mt-2">
                  {p.skills.length} skill{p.skills.length === 1 ? "" : "s"}
                </p>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
