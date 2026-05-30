import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import {
  CheckCircle2,
  FileText,
  FlaskConical,
  KeyRound,
  Layers3,
  ShieldAlert,
  SlidersHorizontal,
  Square,
  UserCog,
} from "lucide-react";
import { useSessionStream } from "@/hooks/useSessionStream";
import { SessionStatusBar } from "./SessionStatusBar";
import { ChatPane } from "@/panes/ChatPane";
import { KBPane } from "@/panes/KBPane";
import { FindingsPane } from "@/panes/FindingsPane";
import { HypothesesPane } from "@/panes/HypothesesPane";
import { Button } from "@/components/ui/button";
import { useSessions, useResumeSession, useSessionLogReplay } from "@/api/queries";
import { SkillPickerModal } from "@/modals/SkillPickerModal";
import { SudoModal } from "@/modals/SudoModal";
import { StopModal } from "@/modals/StopModal";
import { DoneModal } from "@/modals/DoneModal";
import { ProfilePickerModal } from "@/modals/ProfilePickerModal";
import { getSessionStore } from "@/state/session-store";
import { cn } from "@/lib/utils";

const RIGHT_TABS = [
  { key: "hypotheses", label: "Hypotheses", icon: FlaskConical },
  { key: "findings", label: "Findings", icon: FileText },
  { key: "kb", label: "KB", icon: Layers3 },
] as const;

export function SessionLayout() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const sessions = useSessions();
  const row = sessions.data?.sessions.find((s) => s.id === id);
  const target = row?.target ?? null;
  const isActive = row?.state === "active";
  const isStopped = row?.state === "stopped";

  // WebSocket only when active.
  useSessionStream(isActive ? id ?? null : null);

  // Read-only: replay session log once on mount.
  const sessionLog = useSessionLogReplay(!isActive ? id ?? null : null, target);
  useEffect(() => {
    if (!id || isActive || !sessionLog.data) return;
    getSessionStore(id).getState().seedFromSessionLog(sessionLog.data.events);
  }, [id, isActive, sessionLog.data]);

  const [rightTab, setRightTab] = useState<"hypotheses" | "findings" | "kb">("hypotheses");
  const [skillOpen, setSkillOpen] = useState(false);
  const [sudoOpen, setSudoOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [doneOpen, setDoneOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);

  useEffect(() => {
    if (!isActive) return;
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "d") {
        e.preventDefault(); setDoneOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isActive]);

  const resume = useResumeSession();
  const onResume = async () => {
    if (!id) return;
    const res = await resume.mutateAsync(id);
    navigate(`/sessions/${res.id}`);
  };

  if (!id) return null;

  return (
    <div className="h-full flex flex-col bg-neutral-950/75 text-neutral-100">
      <SessionStatusBar sessionId={id} />

      {isStopped && (
        <div className="border-b border-emerald-900/60 bg-emerald-950/30 px-3 py-1.5 flex items-center gap-3 text-xs">
          <span className="text-emerald-400">●</span>
          <span className="text-neutral-300">
            This engagement is stopped. Resume to continue where you left off.
          </span>
          <Button
            size="sm" variant="outline"
            className="ml-auto"
            onClick={onResume}
            disabled={resume.isPending}
          >
            {resume.isPending ? "Resuming…" : "Resume engagement"}
          </Button>
        </div>
      )}

      <div className="flex-1 min-h-0 px-2 pb-2 pt-2">
        <PanelGroup direction="horizontal">
          <Panel defaultSize={68} minSize={40}>
            <div className="h-full overflow-hidden rounded-md border border-neutral-800/90 bg-neutral-950/70 shadow-[0_0_0_1px_rgba(255,255,255,0.02)]">
              <ChatPane sessionId={id} readOnly={!isActive} />
            </div>
          </Panel>
          <PanelResizeHandle className="w-2 bg-transparent transition-colors hover:bg-cyan-400/10" />
          <Panel defaultSize={32} minSize={20}>
            <div className="flex h-full flex-col overflow-hidden rounded-md border border-neutral-800/90 bg-neutral-950/70 shadow-[0_0_0_1px_rgba(255,255,255,0.02)]">
              <div className="border-b border-neutral-800 bg-neutral-900/50 px-2 py-2">
                <div className="grid grid-cols-3 gap-1 rounded-md bg-neutral-900/70 p-1">
                  {RIGHT_TABS.map(({ key, label, icon: Icon }) => (
                    <button key={key}
                      className={cn(
                        "inline-flex min-w-0 items-center justify-center gap-1.5 rounded px-2 py-1 text-[11px] font-medium text-neutral-500 transition-colors",
                        key === rightTab
                          ? "bg-neutral-800 text-neutral-100 shadow-sm"
                          : "hover:bg-neutral-800/50 hover:text-neutral-300",
                      )}
                      onClick={() => setRightTab(key)}
                      title={label}
                      aria-label={label}
                    >
                      <Icon className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate">{label}</span>
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex min-h-0 items-center gap-2 border-b border-neutral-800/70 bg-neutral-950/55 px-3 py-2 text-xs">
                {RIGHT_TABS.map(({ key, label, icon: Icon }) => (
                  key === rightTab ? (
                    <div key={key} className="flex min-w-0 items-center gap-2">
                      <Icon className="h-3.5 w-3.5 shrink-0 text-cyan-300/80" />
                      <span className="truncate font-medium text-neutral-200">{label}</span>
                    </div>
                  ) : null
                ))}
                <span className="ml-auto font-mono text-[10px] uppercase tracking-wide text-neutral-600">
                  live session context
                </span>
              </div>
              <div className="flex-1 min-h-0 overflow-auto bg-neutral-950/40">
                {rightTab === "hypotheses" && <HypothesesPane sessionId={id} />}
                {rightTab === "findings" && <FindingsPane sessionId={id} />}
                {rightTab === "kb" && <KBPane target={target} />}
              </div>
            </div>
          </Panel>
        </PanelGroup>
      </div>

      {isActive ? (
        <div className="h-11 border-t border-neutral-800 bg-neutral-950/90 px-3 flex items-center gap-2 shadow-[0_-1px_0_rgba(255,255,255,0.02)]">
          <div className="flex items-center gap-1 rounded-md border border-neutral-800 bg-neutral-900/50 p-1">
            <Button size="sm" variant="ghost" className="h-7 px-2" onClick={() => setSkillOpen(true)} title="Skills">
              <SlidersHorizontal className="mr-1.5 h-3.5 w-3.5" /> Skills
            </Button>
            <Button size="sm" variant="ghost" className="h-7 px-2" onClick={() => setProfileOpen(true)} title="Profile">
              <UserCog className="mr-1.5 h-3.5 w-3.5" /> Profile
            </Button>
          </div>
          <div className="flex items-center gap-1 rounded-md border border-neutral-800 bg-neutral-900/50 p-1">
            <Button size="sm" variant="ghost" className="h-7 px-2" onClick={() => setSudoOpen(true)} title="Sudo">
              <KeyRound className="mr-1.5 h-3.5 w-3.5" /> Sudo
            </Button>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-red-200/80 hover:text-red-100" onClick={() => setStopOpen(true)} title="Stop">
              <Square className="mr-1.5 h-3.5 w-3.5" /> Stop
            </Button>
          </div>
          <Button size="sm" variant="ghost" className="h-8 px-3 text-emerald-200/90 hover:text-emerald-100" onClick={() => setDoneOpen(true)} title="Mark done">
            <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" /> Mark done
          </Button>
          <span className="ml-auto hidden items-center gap-1.5 text-[11px] text-neutral-500 sm:inline-flex">
            <ShieldAlert className="h-3.5 w-3.5" />
            active engagement
          </span>
        </div>
      ) : (
        <div className="h-9 border-t border-neutral-800 bg-neutral-950/80 px-3 flex items-center text-xs text-neutral-500">
          <span>view-only mode · session id: {id}</span>
        </div>
      )}

      {isActive && (
        <>
          <SkillPickerModal sessionId={id} open={skillOpen} onOpenChange={setSkillOpen} />
          <SudoModal sessionId={id} open={sudoOpen} onOpenChange={setSudoOpen} />
          <StopModal sessionId={id} open={stopOpen} onOpenChange={setStopOpen} />
          <DoneModal sessionId={id} open={doneOpen} onOpenChange={setDoneOpen} />
          <ProfilePickerModal
            open={profileOpen}
            onClose={() => setProfileOpen(false)}
            sessionId={id}
            target={target ?? ""}
            currentProfile={row?.profile ?? ""}
            sessionRunning={isActive}
          />
        </>
      )}
    </div>
  );
}
