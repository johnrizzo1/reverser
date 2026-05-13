import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useBackends, useCreateSession, useProfiles, useResumeSession, useSessions } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { AuthGateModal } from "@/modals/AuthGateModal";
import { ApiError } from "@/api/client";

const NETWORK_PROFILES = new Set([
  "pentest", "webpentest", "webapi", "webrecon", "ad", "manager", "exploit",
]);

export function NewEngagement() {
  const navigate = useNavigate();
  const profiles = useProfiles();
  const backends = useBackends();
  const sessions = useSessions();
  const create = useCreateSession();
  const resume = useResumeSession();

  const [target, setTarget] = useState("");
  const [profile, setProfile] = useState("general");
  const [backend, setBackend] = useState("claude");
  const [model, setModel] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [budget, setBudget] = useState(5);
  const [maxTurns, setMaxTurns] = useState(50);
  const [authGateOpen, setAuthGateOpen] = useState(false);
  const [pendingSubmit, setPendingSubmit] = useState(false);

  const latestStoppedForTarget = sessions.data?.sessions
    .filter((s) => s.target === target && s.state === "stopped")
    .sort((a, b) => (b.stopped_at ?? "").localeCompare(a.stopped_at ?? ""))[0];

  async function submit() {
    setPendingSubmit(false);
    try {
      const res = await create.mutateAsync({
        target,
        profile,
        backend,
        model: model || null,
        api_base: apiBase || null,
        budget,
        max_turns: maxTurns,
      });
      navigate(`/session/${res.id}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setPendingSubmit(true);
        setAuthGateOpen(true);
      } else {
        alert((e as Error).message);
      }
    }
  }

  return (
    <div className="p-6 max-w-2xl">
      <h2 className="text-base font-medium mb-4">New engagement</h2>

      <Card>
        <CardHeader>
          <CardTitle>Target</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <label className="block text-xs text-neutral-500 mb-1">Path or URL</label>
            <Input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="/path/to/binary or https://example.com or 10.10.10.5"
            />
            {latestStoppedForTarget && (
              <button
                className="mt-1 text-xs text-blue-400 hover:underline"
                onClick={async () => {
                  const res = await resume.mutateAsync(latestStoppedForTarget.id);
                  navigate(`/session/${res.id}`);
                }}
              >
                resume {latestStoppedForTarget.id} (stopped {latestStoppedForTarget.stopped_at})
              </button>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Profile</label>
              <Select value={profile} onChange={(e) => setProfile(e.target.value)}>
                {profiles.data?.profiles.map((p) => (
                  <option key={p.key} value={p.key}>{p.name} · {p.key}</option>
                ))}
              </Select>
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Backend</label>
              <Select value={backend} onChange={(e) => setBackend(e.target.value)}>
                {backends.data?.backends.map((b) => (
                  <option key={b.key} value={b.key}>{b.name}</option>
                ))}
              </Select>
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Model (optional for Claude)</label>
              <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. qwen3.5:35b" />
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">API base URL (optional)</label>
              <Input value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder="http://localhost:11434/v1" />
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Budget (USD)</label>
              <Input type="number" step="0.1" value={budget} onChange={(e) => setBudget(parseFloat(e.target.value))} />
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Max turns</label>
              <Input type="number" value={maxTurns} onChange={(e) => setMaxTurns(parseInt(e.target.value, 10))} />
            </div>
          </div>

          <Button
            disabled={!target || create.isPending}
            onClick={submit}
          >
            {create.isPending ? "Starting…" : "Start engagement"}
          </Button>

          {NETWORK_PROFILES.has(profile) && (
            <p className="text-[11px] text-amber-400 mt-2">
              This profile touches the network. You must have written authorization to test the target.
            </p>
          )}
        </CardContent>
      </Card>

      <AuthGateModal
        open={authGateOpen}
        onOpenChange={setAuthGateOpen}
        onAcknowledged={() => { if (pendingSubmit) submit(); }}
      />
    </div>
  );
}
