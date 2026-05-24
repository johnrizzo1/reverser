import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useProfiles, useUpdateSessionConfig } from "@/api/queries";

export function ProfilePickerModal({
  open,
  onClose,
  sessionId,
  target,
  currentProfile,
  sessionRunning,
}: {
  open: boolean;
  onClose: () => void;
  sessionId: string;
  target: string;
  currentProfile: string;
  sessionRunning: boolean;
}) {
  const profiles = useProfiles();
  const [selected, setSelected] = useState<string>(currentProfile);
  const [error, setError] = useState<string | null>(null);

  const update = useUpdateSessionConfig();

  const patch = {
    isPending: update.isPending,
    mutate: (key: string) => {
      update.mutate(
        { sessionId, target, body: { profile: key } },
        {
          onSuccess: () => onClose(),
          onError: (e: Error) => setError(e.message),
        },
      );
    },
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-neutral-900 border border-neutral-800 rounded p-4 w-[420px] space-y-3">
        <h2 className="text-sm font-semibold">Switch profile</h2>
        {sessionRunning && (
          <p className="text-xs text-amber-400">Pause the session to apply.</p>
        )}
        <div className="space-y-1 max-h-72 overflow-auto">
          {(profiles.data?.profiles ?? []).map((p) => (
            <button
              key={p.key}
              onClick={() => setSelected(p.key)}
              className={`w-full text-left text-sm px-2 py-1 rounded hover:bg-neutral-800 ${
                selected === p.key ? "bg-neutral-800" : ""
              }`}
            >
              <span className="text-neutral-500 inline-block w-4">
                {p.key === currentProfile ? "✓" : ""}
              </span>
              {p.name}
            </button>
          ))}
        </div>
        {error && <p className="text-xs text-red-400">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            disabled={sessionRunning || selected === currentProfile || patch.isPending}
            onClick={() => patch.mutate(selected)}
          >
            {patch.isPending ? "Applying…" : "Apply"}
          </Button>
        </div>
      </div>
    </div>
  );
}
