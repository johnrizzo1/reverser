import { useProfiles } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function Settings() {
  const profiles = useProfiles();

  return (
    <div className="p-6 max-w-5xl overflow-auto h-full">
      <h2 className="text-base font-medium mb-6">Settings</h2>

      <h3 className="text-sm font-medium mb-3">
        Profiles ({profiles.data?.profiles.length ?? 0})
      </h3>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        {profiles.data?.profiles.map((p) => (
          <Card key={p.key}>
            <CardHeader>
              <CardTitle>{p.name}</CardTitle>
              <div className="text-[10px] uppercase tracking-wide text-neutral-500 mt-0.5">
                {p.key}
              </div>
            </CardHeader>
            <CardContent className="text-xs">
              <p className="text-neutral-400 line-clamp-3">{p.description || "—"}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
