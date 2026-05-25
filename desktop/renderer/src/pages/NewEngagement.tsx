import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  useBackends,
  useCreateSession,
  useProfiles,
  useResumeSession,
  useSessions,
} from "@/api/queries";
import { useTargetsSummary, useTarget } from "@/state/targets-store";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ModelSelector } from "@/components/ModelSelector";
import { AuthGateModal } from "@/modals/AuthGateModal";
import { ApiError } from "@/api/client";
import { cn } from "@/lib/utils";

const NETWORK_PROFILES = new Set([
  "pentest", "webpentest", "webapi", "webrecon", "ad", "manager", "exploit",
]);

type TargetMode = "new" | "existing";

export function NewEngagement() {
  const navigate = useNavigate();
  const profiles = useProfiles();
  const backends = useBackends();
  const sessions = useSessions();
  const create = useCreateSession();
  const resume = useResumeSession();

  // ----- target mode -----
  const [targetMode, setTargetMode] = useState<TargetMode>("new");

  // "new" mode fields
  const [target, setTarget] = useState("");
  const [targetNameOverride, setTargetNameOverride] = useState("");

  // "existing" mode fields
  const [selectedTargetName, setSelectedTargetName] = useState<string>("");
  const [useOverride, setUseOverride] = useState(false);
  const [overrideAddress, setOverrideAddress] = useState("");

  const { data: existingTargets } = useTargetsSummary();
  const { data: selectedDetail } = useTarget(
    targetMode === "existing" ? selectedTargetName || null : null,
  );

  // ----- session config -----
  const [profile, setProfile] = useState("general");
  const [backend, setBackend] = useState("claude");
  const [model, setModel] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [budget, setBudget] = useState(5);
  const [maxTurns, setMaxTurns] = useState(50);

  const [authGateOpen, setAuthGateOpen] = useState(false);
  const [pendingSubmit, setPendingSubmit] = useState(false);

  // Resume button for "new" mode
  const latestStoppedForTarget = sessions.data?.sessions
    .filter((s) => s.target === target && s.state === "stopped")
    .sort((a, b) => (b.stopped_at ?? "").localeCompare(a.stopped_at ?? ""))[0];

  const canSubmit = targetMode === "new"
    ? !!target
    : !!selectedTargetName;

  async function submit() {
    setPendingSubmit(false);
    try {
      const shared = {
        profile,
        backend,
        model: model || null,
        api_base: apiBase || null,
        budget,
        max_turns: maxTurns,
      };

      const payload = targetMode === "existing"
        ? {
            target_name: selectedTargetName,
            address: useOverride && overrideAddress ? overrideAddress : undefined,
            ...shared,
          }
        : {
            // For new mode: pass target_name (name or address) + legacy target
            target_name: targetNameOverride || target,
            target,
            ...shared,
          };

      const res = await create.mutateAsync(payload);
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

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>Target</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Mode toggle */}
          <div className="flex gap-4 text-xs">
            {(["new", "existing"] as TargetMode[]).map((m) => (
              <label key={m} className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="radio"
                  name="targetMode"
                  value={m}
                  checked={targetMode === m}
                  onChange={() => setTargetMode(m)}
                  className="accent-neutral-300"
                />
                <span className={cn(
                  "capitalize",
                  targetMode === m ? "text-neutral-100" : "text-neutral-500",
                )}>
                  {m === "new" ? "New target" : "Existing target"}
                </span>
              </label>
            ))}
          </div>

          {/* New target fields */}
          {targetMode === "new" && (
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-neutral-500 mb-1">
                  Address / path
                </label>
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
                    resume {latestStoppedForTarget.id} (stopped{" "}
                    {latestStoppedForTarget.stopped_at})
                  </button>
                )}
              </div>
              <div>
                <label className="block text-xs text-neutral-500 mb-1">
                  Target name{" "}
                  <span className="text-neutral-600">(optional — defaults to address)</span>
                </label>
                <Input
                  value={targetNameOverride}
                  onChange={(e) => setTargetNameOverride(e.target.value)}
                  placeholder={target || "e.g. dc1-corp"}
                />
              </div>
            </div>
          )}

          {/* Existing target fields */}
          {targetMode === "existing" && (
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-neutral-500 mb-1">Target</label>
                <Select
                  value={selectedTargetName}
                  onChange={(e) => {
                    setSelectedTargetName(e.target.value);
                    setUseOverride(false);
                    setOverrideAddress("");
                  }}
                >
                  <option value="">— pick a target —</option>
                  {(existingTargets ?? [])
                    .filter((t) => !t.archived)
                    .map((t) => (
                      <option key={t.name} value={t.name}>
                        {t.name}
                        {t.primary_address ? ` (${t.primary_address})` : ""}
                      </option>
                    ))}
                </Select>
              </div>

              {selectedDetail && (
                <div className="text-xs text-neutral-500">
                  Primary:{" "}
                  <span className="text-neutral-300 font-mono">
                    {selectedDetail.addresses.find(
                      (a) => a.id === selectedDetail.primary_address_id,
                    )?.value ?? "(unknown)"}
                  </span>
                  {" "}· {selectedDetail.addresses.length} address
                  {selectedDetail.addresses.length !== 1 ? "es" : ""}
                </div>
              )}

              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  checked={useOverride}
                  onChange={(e) => setUseOverride(e.target.checked)}
                  className="accent-neutral-300"
                />
                <span className="text-neutral-400">
                  Use a different address for this session
                </span>
              </label>

              {useOverride && (
                <div>
                  <label className="block text-xs text-neutral-500 mb-1">
                    Address override
                  </label>
                  <Input
                    value={overrideAddress}
                    onChange={(e) => setOverrideAddress(e.target.value)}
                    placeholder="e.g. 10.0.0.42"
                  />
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Session config</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Profile</label>
              <Select value={profile} onChange={(e) => setProfile(e.target.value)}>
                {profiles.data?.profiles.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.name} · {p.key}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Backend</label>
              <Select value={backend} onChange={(e) => setBackend(e.target.value)}>
                {backends.data?.backends.map((b) => (
                  <option key={b.key} value={b.key}>
                    {b.name}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">
                Model (optional for Claude)
              </label>
              {backend === "lmstudio" || backend === "ollama" ? (
                <ModelSelector
                  backend={backend}
                  apiBase={apiBase}
                  value={model}
                  onChange={setModel}
                  placeholder="e.g. qwen3.5:35b"
                />
              ) : (
                <Input
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="e.g. qwen3.5:35b"
                />
              )}
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">
                API base URL (optional)
              </label>
              <Input
                value={apiBase}
                onChange={(e) => setApiBase(e.target.value)}
                placeholder="http://localhost:11434/v1"
              />
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Budget (USD)</label>
              <Input
                type="number"
                step="0.1"
                value={budget}
                onChange={(e) => setBudget(parseFloat(e.target.value))}
              />
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Max turns</label>
              <Input
                type="number"
                value={maxTurns}
                onChange={(e) => setMaxTurns(parseInt(e.target.value, 10))}
              />
            </div>
          </div>

          <Button disabled={!canSubmit || create.isPending} onClick={submit}>
            {create.isPending ? "Starting…" : "Start engagement"}
          </Button>

          {NETWORK_PROFILES.has(profile) && (
            <p className="text-[11px] text-amber-400 mt-2">
              This profile touches the network. You must have written authorization to test
              the target.
            </p>
          )}
        </CardContent>
      </Card>

      <AuthGateModal
        open={authGateOpen}
        onOpenChange={setAuthGateOpen}
        onAcknowledged={() => {
          if (pendingSubmit) submit();
        }}
      />
    </div>
  );
}
