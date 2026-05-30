import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useSessions, useTargetSummary } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SessionRow } from "@/components/SessionRow";
import { KBTabbedView } from "@/components/KBTabbedView";
import { ScopeEditorModal } from "@/modals/ScopeEditorModal";
import { ScreenshotLightboxModal } from "@/modals/ScreenshotLightboxModal";

export function TargetOverview() {
  const { name: rawName } = useParams<{ name: string }>();
  const name = rawName ? decodeURIComponent(rawName) : null;
  const summary = useTargetSummary(name);
  const sessions = useSessions();
  const targetSessions = (sessions.data?.sessions ?? []).filter(
    (s) => s.target === name || s.target_name === name,
  );
  const [scopeOpen, setScopeOpen] = useState(false);
  const [lightbox, setLightbox] = useState<{ findingId: string; startIndex: number } | null>(null);

  if (!name) return null;

  return (
    <div className="p-6 h-full overflow-auto">
      <div className="flex items-center mb-4 gap-3">
        <h2 className="text-base font-medium text-neutral-100">{name}</h2>
        <Button size="sm" variant="outline" onClick={() => setScopeOpen(true)}>
          Edit scope
        </Button>
        <Link to={`/new?target=${encodeURIComponent(name)}`} className="ml-auto">
          <Button size="sm">New engagement</Button>
        </Link>
      </div>

      <Card className="mb-4">
        <CardHeader><CardTitle>Summary</CardTitle></CardHeader>
        <CardContent className="text-xs font-mono">
          {summary.isLoading && <p className="text-neutral-500">loading…</p>}
          {summary.error && (
            <p className="text-red-400">
              {String((summary.error as Error).message)}
            </p>
          )}
          {summary.data && (
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              <div>
                <span className="text-neutral-500">sessions: </span>
                <span className="text-neutral-200">
                  {summary.data.sessions.total}
                </span>
                <span className="text-neutral-500">
                  {" "}({summary.data.sessions.by_state.active} active ·{" "}
                  {summary.data.sessions.by_state.stopped} stopped ·{" "}
                  {summary.data.sessions.by_state.completed} done)
                </span>
              </div>
              <div>
                <span className="text-neutral-500">total spend: </span>
                <span className="text-neutral-200">
                  ${summary.data.spend.total_usd.toFixed(2)}
                </span>
              </div>
              <div>
                <span className="text-neutral-500">first activity: </span>
                <span className="text-neutral-200">
                  {summary.data.first_activity ?? "—"}
                </span>
              </div>
              <div>
                <span className="text-neutral-500">last activity: </span>
                <span className="text-neutral-200">
                  {summary.data.last_activity ?? "—"}
                </span>
              </div>
              <div className="col-span-2">
                <span className="text-neutral-500">profiles used: </span>
                <span className="text-neutral-200">
                  {summary.data.profiles_used.join(", ") || "—"}
                </span>
              </div>
              <div className="col-span-2">
                <span className="text-neutral-500">KB: </span>
                <span className="text-neutral-200">
                  {summary.data.kb_counts.hosts} hosts ·{" "}
                  {summary.data.kb_counts.services} services ·{" "}
                  {summary.data.kb_counts.credentials} creds ·{" "}
                  {summary.data.kb_counts.findings} findings ·{" "}
                  {summary.data.kb_counts.hypotheses} hypotheses
                </span>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle>Sessions</CardTitle></CardHeader>
          <CardContent className="p-0">
            {targetSessions.length === 0 ? (
              <p className="p-3 text-xs text-neutral-500">no sessions for this target</p>
            ) : (
              targetSessions.map((s) => <SessionRow key={s.id} session={s} />)
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Knowledge base</CardTitle></CardHeader>
          <CardContent className="p-0 h-[480px]">
            <KBTabbedView
              target={name}
              onClickEvidence={(findingId, startIndex) =>
                setLightbox({ findingId, startIndex })
              }
            />
          </CardContent>
        </Card>
      </div>

      <ScopeEditorModal target={name} open={scopeOpen} onOpenChange={setScopeOpen} />
      {lightbox && (
        <ScreenshotLightboxModal
          target={name}
          findingId={lightbox.findingId}
          startIndex={lightbox.startIndex}
          open={true}
          onOpenChange={(open) => { if (!open) setLightbox(null); }}
        />
      )}
    </div>
  );
}
