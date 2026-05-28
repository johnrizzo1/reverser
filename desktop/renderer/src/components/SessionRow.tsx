import { useState } from "react";
import { Link } from "react-router-dom";
import { Archive, MoreHorizontal, RotateCcw, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SessionRow as SessionRowData } from "@/api/client";
import {
  useArchiveSession,
  useDeleteSession,
  useUnarchiveSession,
} from "@/api/queries";
import { ArchiveConfirmModal } from "@/modals/ArchiveConfirmModal";
import { DeleteConfirmModal } from "@/modals/DeleteConfirmModal";

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

function _formatArchivedDate(iso: string): string {
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : iso;
}

export function SessionRow({
  session,
  isActive = false,
}: {
  session: SessionRowData;
  isActive?: boolean;
}) {
  const t = _formatTime(session.stopped_at ?? null);
  const archived = session.archived_at !== null;
  const liveActive = session.state === "active";
  const displayTarget = session.target_name || session.target;
  const displayAddress = session.target_name && session.target_name !== session.target
    ? session.target
    : "";

  const archiveMutation = useArchiveSession();
  const unarchiveMutation = useUnarchiveSession();
  const deleteMutation = useDeleteSession();

  const [showArchive, setShowArchive] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className={cn("relative group", menuOpen && "z-20")}>
      <Link
        to={`/sessions/${session.id}`}
        className={cn(
          "block px-3 py-2 border-l-2 transition-colors",
          isActive
            ? "border-neutral-300 bg-neutral-800/60"
            : "border-transparent hover:bg-neutral-900",
          archived && "bg-neutral-950/60 opacity-75",
        )}
      >
        <div className="flex items-center gap-2 text-xs">
          <span className={STATE_DOT[session.state]}>
            {archived ? "■" : STATE_GLYPH[session.state]}
          </span>
          <span className="truncate font-medium text-neutral-100">{displayTarget}</span>
        </div>
        {displayAddress && (
          <div className="mt-0.5 truncate font-mono text-[10px] text-neutral-500">
            {displayAddress}
          </div>
        )}
        <div className="text-[10px] text-neutral-500 font-mono mt-0.5 flex gap-2">
          <span>{session.profile}</span>
          <span>·</span>
          <span>{t}</span>
          <span>·</span>
          <span>${session.total_cost.toFixed(2)}</span>
          {archived && (
            <>
              <span>·</span>
              <span className="text-neutral-400">
                archived {_formatArchivedDate(session.archived_at!)}
              </span>
            </>
          )}
        </div>
      </Link>

      {/* Hover-revealed row actions */}
      <div
        className={cn(
          "absolute right-2 top-1/2 -translate-y-1/2 z-10 flex items-center gap-1",
          "opacity-0 group-hover:opacity-100 transition-opacity",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {archived ? (
          <button
            title="Restore"
            className="p-1 rounded hover:bg-neutral-700 text-neutral-300"
            onClick={(e) => {
              e.preventDefault();
              unarchiveMutation.mutate({
                sessionId: session.id,
                target: session.target,
              });
            }}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        ) : (
          <button
            title={liveActive ? "Stop the session first" : "Archive"}
            disabled={liveActive}
            className={cn(
              "p-1 rounded text-neutral-300",
              liveActive
                ? "opacity-30 cursor-not-allowed"
                : "hover:bg-neutral-700",
            )}
            onClick={(e) => {
              e.preventDefault();
              if (!liveActive) setShowArchive(true);
            }}
          >
            <Archive className="h-3.5 w-3.5" />
          </button>
        )}

        <div className="relative">
          <button
            title="More"
            className="p-1 rounded hover:bg-neutral-700 text-neutral-300"
            onClick={(e) => {
              e.preventDefault();
              setMenuOpen((v) => !v);
            }}
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
          {menuOpen && (
            <div
              className="absolute right-0 top-full mt-1 z-10 min-w-[180px] rounded border border-neutral-700 bg-neutral-900 shadow-lg text-xs"
              onMouseLeave={() => setMenuOpen(false)}
            >
              <button
                disabled={liveActive}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left",
                  liveActive
                    ? "text-neutral-500 cursor-not-allowed"
                    : "text-red-400 hover:bg-neutral-800",
                )}
                onClick={(e) => {
                  e.preventDefault();
                  setMenuOpen(false);
                  if (!liveActive) setShowDelete(true);
                }}
                title={liveActive ? "Stop the session first" : undefined}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete permanently
              </button>
            </div>
          )}
        </div>
      </div>

      <ArchiveConfirmModal
        open={showArchive}
        onOpenChange={setShowArchive}
        title="Archive this session?"
        description="This hides it from the default view. You can find it in the 'archived' filter and restore at any time."
        onConfirm={() =>
          archiveMutation.mutateAsync({
            sessionId: session.id,
            target: session.target,
          })
        }
      />
      <DeleteConfirmModal
        open={showDelete}
        onOpenChange={setShowDelete}
        title="Delete this session permanently?"
        description="The snapshot and its log file will be removed from disk. This can't be undone."
        onConfirm={() =>
          deleteMutation.mutateAsync({
            sessionId: session.id,
            target: session.target,
          })
        }
      />
    </div>
  );
}
