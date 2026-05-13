import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { useSessionStream } from "@/hooks/useSessionStream";
import { SessionStatusBar } from "./SessionStatusBar";
import { ChatPane } from "@/panes/ChatPane";
import { ToolTimelinePane } from "@/panes/ToolTimelinePane";
import { KBPane } from "@/panes/KBPane";
import { FindingsPane } from "@/panes/FindingsPane";
import { HypothesesPane } from "@/panes/HypothesesPane";
import { Footer } from "./Footer";
import { Button } from "@/components/ui/button";
import { useSessions, useConversation, useResumeSession, useSessionLogReplay } from "@/api/queries";
import { SkillPickerModal } from "@/modals/SkillPickerModal";
import { SudoModal } from "@/modals/SudoModal";
import { StopModal } from "@/modals/StopModal";
import { DoneModal } from "@/modals/DoneModal";
import { getSessionStore } from "@/state/session-store";

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

  // Read-only: fetch and inject conversation history once on mount.
  const conversation = useConversation(!isActive ? id ?? null : null, target);
  useEffect(() => {
    if (!id || isActive || !conversation.data) return;
    getSessionStore(id).getState().seedConversation(conversation.data.conversation);
  }, [id, isActive, conversation.data]);

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

  useEffect(() => {
    if (!isActive) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "F1") { e.preventDefault(); setSkillOpen(true); }
      if (e.key === "F4") { e.preventDefault(); setSudoOpen(true); }
      if (e.key === "F6") { e.preventDefault(); setStopOpen(true); }
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
    <div className="h-full flex flex-col bg-neutral-950 text-neutral-100">
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

      <div className="flex-1 min-h-0">
        <PanelGroup direction="vertical">
          <Panel defaultSize={70} minSize={30}>
            <PanelGroup direction="horizontal">
              <Panel defaultSize={68} minSize={40}>
                <ChatPane sessionId={id} readOnly={!isActive} />
              </Panel>
              <PanelResizeHandle className="w-px bg-neutral-800 hover:bg-neutral-700" />
              <Panel defaultSize={32} minSize={20}>
                <div className="flex flex-col h-full">
                  <div className="flex gap-3 px-3 border-b border-neutral-800 text-[10px] uppercase tracking-wide text-neutral-500 h-7 items-center">
                    {(["hypotheses", "findings", "kb"] as const).map((t) => (
                      <button key={t}
                        className={t === rightTab ? "text-neutral-200" : "hover:text-neutral-300"}
                        onClick={() => setRightTab(t)}>{t}</button>
                    ))}
                  </div>
                  <div className="flex-1 min-h-0 overflow-auto">
                    {rightTab === "hypotheses" && <HypothesesPane sessionId={id} />}
                    {rightTab === "findings" && <FindingsPane target={target} />}
                    {rightTab === "kb" && <KBPane target={target} />}
                  </div>
                </div>
              </Panel>
            </PanelGroup>
          </Panel>
          <PanelResizeHandle className="h-px bg-neutral-800 hover:bg-neutral-700" />
          <Panel defaultSize={30} minSize={10}>
            <ToolTimelinePane sessionId={id} readOnly={!isActive} />
          </Panel>
        </PanelGroup>
      </div>

      {isActive ? (
        <div className="h-9 border-t border-neutral-800 bg-neutral-950/80 px-3 flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => setSkillOpen(true)}>Skills (F1)</Button>
          <Button size="sm" variant="ghost" onClick={() => setSudoOpen(true)}>Sudo (F4)</Button>
          <Button size="sm" variant="ghost" onClick={() => setStopOpen(true)}>Stop (F6)</Button>
          <Button size="sm" variant="ghost" onClick={() => setDoneOpen(true)}>Mark done</Button>
          <div className="ml-auto"><Footer /></div>
        </div>
      ) : (
        <div className="h-9 border-t border-neutral-800 bg-neutral-950/80 px-3 flex items-center text-xs text-neutral-500">
          <span>view-only mode · session id: {id}</span>
          <div className="ml-auto"><Footer /></div>
        </div>
      )}

      {isActive && (
        <>
          <SkillPickerModal sessionId={id} open={skillOpen} onOpenChange={setSkillOpen} />
          <SudoModal sessionId={id} open={sudoOpen} onOpenChange={setSudoOpen} />
          <StopModal sessionId={id} open={stopOpen} onOpenChange={setStopOpen} />
          <DoneModal sessionId={id} open={doneOpen} onOpenChange={setDoneOpen} />
        </>
      )}
    </div>
  );
}
