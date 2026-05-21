import { useEffect, useState } from "react";
import type { SessionRow } from "@/api/client";
import { useBackends, useProfiles, useUpdateSessionConfig } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

type FormState = {
  backend: string;
  model: string;
  api_base: string;
  profile: string;
  budget: string;
  max_turns: string;
};

function _fromRow(row: SessionRow): FormState {
  return {
    backend: row.backend,
    model: row.model ?? "",
    api_base: row.api_base ?? "",
    profile: row.profile,
    budget: String(row.budget),
    max_turns: String(row.max_turns),
  };
}

function _diff(form: FormState, row: SessionRow): {
  backend?: string;
  model?: string | null;
  api_base?: string | null;
  profile?: string;
  budget?: number;
  max_turns?: number;
} {
  const out: ReturnType<typeof _diff> = {};
  if (form.backend !== row.backend) out.backend = form.backend;
  const formModel = form.model.trim() === "" ? null : form.model;
  if (formModel !== row.model) out.model = formModel;
  const formApiBase = form.api_base.trim() === "" ? null : form.api_base;
  if (formApiBase !== row.api_base) out.api_base = formApiBase;
  if (form.profile !== row.profile) out.profile = form.profile;
  const formBudget = parseFloat(form.budget);
  if (!isNaN(formBudget) && formBudget !== row.budget) out.budget = formBudget;
  const formMaxTurns = parseInt(form.max_turns, 10);
  if (!isNaN(formMaxTurns) && formMaxTurns !== row.max_turns) {
    out.max_turns = formMaxTurns;
  }
  return out;
}

function _invalidFields(form: FormState): Set<keyof FormState> {
  const out = new Set<keyof FormState>();
  if (form.budget.trim() === "" || isNaN(parseFloat(form.budget))) {
    out.add("budget");
  }
  if (form.max_turns.trim() === "" || isNaN(parseInt(form.max_turns, 10))) {
    out.add("max_turns");
  }
  return out;
}

export function SessionConfigPanel({ session }: { session: SessionRow }) {
  const editable = session.state === "stopped";
  const profiles = useProfiles();
  const backends = useBackends();
  const update = useUpdateSessionConfig();

  const [form, setForm] = useState<FormState>(() => _fromRow(session));
  const [saveError, setSaveError] = useState<string | null>(null);

  // Reset form whenever the row's underlying values change (after save or
  // when the user switches between sessions).
  useEffect(() => {
    setForm(_fromRow(session));
  }, [session.id, session.backend, session.model, session.api_base,
      session.profile, session.budget, session.max_turns]);

  const diff = _diff(form, session);
  const dirty = Object.keys(diff).length > 0;
  const invalid = _invalidFields(form);
  const profileOrBackendChanged =
    diff.profile !== undefined || diff.backend !== undefined || diff.model !== undefined;

  async function onSave() {
    if (!dirty) return;
    setSaveError(null);
    try {
      await update.mutateAsync({
        sessionId: session.id, target: session.target, body: diff,
      });
    } catch (e) {
      setSaveError((e as Error).message);
    }
  }

  function _row(label: string, child: React.ReactNode) {
    return (
      <div className="flex items-center gap-3 text-xs">
        <label className="w-24 text-neutral-500 shrink-0">{label}</label>
        <div className="flex-1 min-w-0">{child}</div>
      </div>
    );
  }

  return (
    <div className="border-b border-neutral-800 bg-neutral-950/60 px-3 py-3 space-y-2">
      {_row("backend", editable ? (
        <Select
          value={form.backend}
          onChange={(e) => setForm({ ...form, backend: e.target.value })}
          className="h-7 text-xs"
        >
          {backends.data?.backends.map((b) => (
            <option key={b.key} value={b.key}>{b.name}</option>
          ))}
        </Select>
      ) : <span className="text-neutral-300 font-mono">{session.backend}</span>)}

      {_row("model", editable ? (
        <Input
          value={form.model}
          onChange={(e) => setForm({ ...form, model: e.target.value })}
          placeholder="(optional)"
          className="h-7 text-xs"
        />
      ) : <span className="text-neutral-300 font-mono">{session.model ?? "—"}</span>)}

      {_row("api_base", editable ? (
        <Input
          value={form.api_base}
          onChange={(e) => setForm({ ...form, api_base: e.target.value })}
          placeholder="(optional)"
          className="h-7 text-xs"
        />
      ) : <span className="text-neutral-300 font-mono">{session.api_base ?? "—"}</span>)}

      {_row("profile", editable ? (
        <Select
          value={form.profile}
          onChange={(e) => setForm({ ...form, profile: e.target.value })}
          className="h-7 text-xs"
        >
          {profiles.data?.profiles.map((p) => (
            <option key={p.key} value={p.key}>{p.name} · {p.key}</option>
          ))}
        </Select>
      ) : <span className="text-neutral-300 font-mono">{session.profile}</span>)}

      {_row("budget", editable ? (
        <div>
          <Input
            type="number" step="0.1"
            value={form.budget}
            onChange={(e) => setForm({ ...form, budget: e.target.value })}
            className="h-7 text-xs"
          />
          {invalid.has("budget") && (
            <p className="text-[10px] text-red-400 mt-0.5">must be a positive number</p>
          )}
        </div>
      ) : <span className="text-neutral-300 font-mono">${session.budget.toFixed(2)}</span>)}

      {_row("max turns", editable ? (
        <div>
          <Input
            type="number"
            value={form.max_turns}
            onChange={(e) => setForm({ ...form, max_turns: e.target.value })}
            className="h-7 text-xs"
          />
          {invalid.has("max_turns") && (
            <p className="text-[10px] text-red-400 mt-0.5">must be a positive number</p>
          )}
        </div>
      ) : <span className="text-neutral-300 font-mono">{session.max_turns}</span>)}

      {editable && profileOrBackendChanged && (
        <p className="text-[11px] text-amber-400 mt-2 pl-24">
          Profile / backend / model changes apply on resume. The conversation
          history is preserved, but the system prompt and toolset shift mid-
          conversation.
        </p>
      )}

      {editable && saveError && (
        <p className="text-[11px] text-red-400 mt-2 pl-24">{saveError}</p>
      )}

      {editable && (
        <div className="flex items-center gap-2 justify-end pt-1">
          <Button
            size="sm" variant="ghost"
            disabled={!dirty || update.isPending}
            onClick={() => setForm(_fromRow(session))}
          >
            Discard
          </Button>
          <Button
            size="sm"
            disabled={!dirty || invalid.size > 0 || update.isPending}
            onClick={onSave}
          >
            {update.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      )}
    </div>
  );
}
