import { useHealth } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Check, X } from "lucide-react";

export function Health() {
  const { data, isLoading, error } = useHealth();

  return (
    <div className="p-6 max-w-3xl">
      <h2 className="text-base font-medium mb-1">Backend health</h2>
      <p className="text-xs text-neutral-500 mb-4">
        These checks run every 10 s. None of them block service startup —
        but a red entry usually means a feature won't work.
      </p>

      {isLoading && <p className="text-sm text-neutral-500">loading…</p>}
      {error && <p className="text-sm text-red-400">{String((error as Error).message)}</p>}

      <Card>
        <CardHeader>
          <CardTitle>checks · service v{data?.version ?? "?"}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 font-mono text-xs">
          {data &&
            Object.entries(data.checks).map(([k, v]) => (
              <div key={k} className="flex items-start gap-3">
                {v.ok ? (
                  <Check className="w-4 h-4 mt-0.5 text-green-400 shrink-0" />
                ) : (
                  <X className="w-4 h-4 mt-0.5 text-red-400 shrink-0" />
                )}
                <div className="min-w-0">
                  <div className="text-neutral-200">{k}</div>
                  <div className="text-neutral-500 truncate">{v.detail ?? "—"}</div>
                </div>
              </div>
            ))}
        </CardContent>
      </Card>
    </div>
  );
}
