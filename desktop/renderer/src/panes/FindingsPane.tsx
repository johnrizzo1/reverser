import { useEffect, useMemo, useState } from "react";
import { useStore } from "zustand";
import { useTargetKB, useSessions } from "@/api/queries";
import { getSessionStore, type FindingRow } from "@/state/session-store";
import { FindingRow as FindingRowComponent } from "@/components/FindingRow";
import { ScreenshotLightboxModal } from "@/modals/ScreenshotLightboxModal";
import { FileText } from "lucide-react";

export function FindingsPane({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const findingsMap = useStore(store, (s) => s.findings);
  const seedFindings = useStore(store, (s) => s.seedFindings);
  const sessions = useSessions();
  const session = sessions.data?.sessions.find((s) => s.id === sessionId);
  const target = session?.target ?? null;
  const kb = useTargetKB(target);
  const [lightbox, setLightbox] = useState<{ findingId: string; startIndex: number } | null>(null);

  useEffect(() => {
    const rows = (kb.data?.findings ?? []) as FindingRow[];
    if (rows.length > 0) seedFindings(rows);
  }, [kb.data, seedFindings]);

  const rows = useMemo(
    () => Array.from(findingsMap.values()).sort((a, b) => (a.id ?? 0) - (b.id ?? 0)),
    [findingsMap],
  );

  if (rows.length === 0) {
    return (
      <div className="flex h-full min-h-[14rem] items-center justify-center p-4 text-center">
        <div className="max-w-xs">
          <FileText className="mx-auto mb-2 h-5 w-5 text-cyan-300/70" />
          <p className="text-sm font-medium text-neutral-200">No findings yet</p>
          <p className="mt-1 text-xs leading-5 text-neutral-500">
            Confirmed security observations and evidence will collect here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="p-2 space-y-2 text-xs">
        {rows.map((f) => (
          <FindingRowComponent
            key={`finding-${f.id}`}
            target={target}
            finding={{ ...f, severity: f.severity ?? undefined }}
            onClickEvidence={(findingId, startIndex) =>
              setLightbox({ findingId, startIndex })
            }
          />
        ))}
      </div>
      {lightbox && (
        <ScreenshotLightboxModal
          target={target ?? ""}
          findingId={lightbox.findingId}
          startIndex={lightbox.startIndex}
          open={true}
          onOpenChange={(open) => { if (!open) setLightbox(null); }}
        />
      )}
    </>
  );
}
