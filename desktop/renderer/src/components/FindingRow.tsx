import { Camera } from "lucide-react";
import { useScreenshots } from "@/api/queries";
import { cn } from "@/lib/utils";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "text-red-300",
  high: "text-red-400",
  medium: "text-amber-400",
  low: "text-blue-400",
  info: "text-neutral-500",
};

type FindingLike = Record<string, unknown> & {
  id?: string | number;
  severity?: string;
  title?: string;
  description?: string;
};

export function FindingRow({
  target,
  finding,
  onClickEvidence,
}: {
  /** Used to look up screenshots for the badge. Pass null to disable the badge. */
  target: string | null;
  finding: FindingLike;
  onClickEvidence?: (findingId: string, startIndex: number) => void;
}) {
  const findingId = String(finding.id ?? "");
  const screenshots = useScreenshots(target, findingId || null);
  const screenshotCount = screenshots.data?.screenshots.length ?? 0;
  const sev = String(finding.severity ?? "info").toLowerCase();

  return (
    <div className="border border-neutral-800 rounded p-2 bg-neutral-950">
      <div className="flex items-center gap-2">
        <span className={SEVERITY_COLOR[sev] ?? "text-neutral-500"}>● {sev}</span>
        <span className="text-neutral-200">{String(finding.title ?? "—")}</span>
        {screenshotCount > 0 && onClickEvidence && findingId && (
          <button
            onClick={() => onClickEvidence(findingId, 1)}
            className={cn(
              "ml-auto inline-flex items-center gap-1 text-[10px]",
              "px-1.5 py-0.5 rounded border border-neutral-700 hover:bg-neutral-800",
              "text-neutral-300 hover:text-neutral-100 transition-colors",
            )}
            title={`${screenshotCount} screenshot${screenshotCount === 1 ? "" : "s"}`}
          >
            <Camera className="w-3 h-3" />
            {screenshotCount}
          </button>
        )}
      </div>
      {!!finding.description && (
        <p className="text-neutral-400 text-xs mt-1 line-clamp-3">
          {String(finding.description)}
        </p>
      )}
    </div>
  );
}
