import { useState } from "react";
import { useTargetKB } from "@/api/queries";
import { FindingRow } from "@/components/FindingRow";
import { ScreenshotLightboxModal } from "@/modals/ScreenshotLightboxModal";

export function FindingsPane({ target }: { target: string | null }) {
  const { data, isLoading } = useTargetKB(target);
  const [lightbox, setLightbox] = useState<{ findingId: string; startIndex: number } | null>(null);

  if (!target) return <p className="p-3 text-xs text-neutral-500">no target</p>;
  if (isLoading) return <p className="p-3 text-xs text-neutral-500">loading…</p>;

  const findings = (data?.findings ?? []) as Array<Record<string, unknown>>;
  if (findings.length === 0) return <p className="p-3 text-xs text-neutral-500">no findings yet</p>;

  return (
    <>
      <div className="p-2 space-y-2 text-xs">
        {findings.map((f, i) => (
          <FindingRow
            key={i}
            target={target}
            finding={f}
            onClickEvidence={(findingId, startIndex) =>
              setLightbox({ findingId, startIndex })
            }
          />
        ))}
      </div>
      {lightbox && (
        <ScreenshotLightboxModal
          target={target}
          findingId={lightbox.findingId}
          startIndex={lightbox.startIndex}
          open={true}
          onOpenChange={(open) => { if (!open) setLightbox(null); }}
        />
      )}
    </>
  );
}
