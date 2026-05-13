import { Link } from "react-router-dom";
import { useProfiles } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export function Dashboard() {
  const profiles = useProfiles();

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-center mb-6 gap-4">
        <h2 className="text-base font-medium">Dashboard</h2>
        <Link to="/new" className="ml-auto">
          <Button>New engagement</Button>
        </Link>
      </div>

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
