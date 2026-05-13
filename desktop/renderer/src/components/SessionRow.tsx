import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";
import type { SessionRow as SessionRowData } from "@/api/client";

const STATE_DOT: Record<SessionRowData["state"], string> = {
  active: "text-green-400",
  stopped: "text-amber-400",
  completed: "text-blue-400",
  abandoned: "text-neutral-500",
};

const STATE_GLYPH: Record<SessionRowData["state"], string> = {
  active: "●",
  stopped: "⏸",
  completed: "✓",
  abandoned: "—",
};

function _formatTime(iso: string | null): string {
  if (!iso) return "—";
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  if (!m) return iso;
  return `${m[1].slice(5)} ${m[2]}`;
}

export function SessionRow({
  session,
  isActive = false,
}: {
  session: SessionRowData;
  isActive?: boolean;
}) {
  const t = _formatTime(session.stopped_at ?? null);
  return (
    <Link
      to={`/sessions/${session.id}`}
      className={cn(
        "block px-3 py-2 border-l-2 transition-colors",
        isActive
          ? "border-neutral-300 bg-neutral-800/60"
          : "border-transparent hover:bg-neutral-900",
      )}
    >
      <div className="flex items-center gap-2 text-xs">
        <span className={STATE_DOT[session.state]}>{STATE_GLYPH[session.state]}</span>
        <span className="text-neutral-200 truncate">{session.target}</span>
      </div>
      <div className="text-[10px] text-neutral-500 font-mono mt-0.5 flex gap-2">
        <span>{session.profile}</span>
        <span>·</span>
        <span>{t}</span>
        <span>·</span>
        <span>${session.total_cost.toFixed(2)}</span>
      </div>
    </Link>
  );
}
