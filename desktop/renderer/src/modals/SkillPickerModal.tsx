import { Dialog, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useProfiles, useTriggerSkill, useSessions } from "@/api/queries";

export function SkillPickerModal({
  sessionId, open, onOpenChange,
}: {
  sessionId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const profiles = useProfiles();
  const sessions = useSessions();
  const row = sessions.data?.sessions.find((s) => s.id === sessionId);
  const profile = profiles.data?.profiles.find((p) => p.key === row?.profile);
  const trigger = useTriggerSkill(sessionId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Skills — {profile?.name}</DialogTitle>
      </DialogHeader>
      <div className="space-y-1 max-h-80 overflow-auto">
        {(profile?.skills ?? []).map((s) => (
          <button
            key={s.key}
            className="block w-full text-left p-2 rounded hover:bg-neutral-800"
            onClick={async () => {
              await trigger.mutateAsync(s.key);
              onOpenChange(false);
            }}
          >
            <div className="text-sm text-neutral-200">{s.name} · {s.key}</div>
            <div className="text-xs text-neutral-500">{s.description}</div>
          </button>
        ))}
      </div>
      <DialogFooter><Button variant="ghost" onClick={() => onOpenChange(false)}>Close</Button></DialogFooter>
    </Dialog>
  );
}
