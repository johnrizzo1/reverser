import { useEffect, useState } from "react";
import {
  Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useScope, useUpdateScope } from "@/api/queries";
import { ApiError } from "@/api/client";

export function ScopeEditorModal({
  target,
  open,
  onOpenChange,
}: {
  target: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const scope = useScope(target);
  const update = useUpdateScope(target);

  const [cidrsText, setCidrsText] = useState("");
  const [ipsText, setIpsText] = useState("");
  const [hours, setHours] = useState("");
  const [noDos, setNoDos] = useState(false);
  const [noLockout, setNoLockout] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Seed form when modal opens or scope data changes.
  useEffect(() => {
    if (!open || !scope.data) return;
    setCidrsText(scope.data.in_scope_cidrs.join("\n"));
    setIpsText(scope.data.out_of_scope_ips.join("\n"));
    setHours(scope.data.allowed_hours ?? "");
    setNoDos(scope.data.no_dos);
    setNoLockout(scope.data.no_account_lockout);
    setErrors({});
  }, [open, scope.data]);

  const submit = async () => {
    setErrors({});
    const body = {
      in_scope_cidrs: cidrsText.split("\n").map((s) => s.trim()).filter(Boolean),
      out_of_scope_ips: ipsText.split("\n").map((s) => s.trim()).filter(Boolean),
      allowed_hours: hours.trim() || null,
      no_dos: noDos,
      no_account_lockout: noLockout,
    };
    try {
      await update.mutateAsync(body);
      onOpenChange(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 400) {
        const body = e.body as { errors?: Record<string, string> } | null;
        setErrors(body?.errors ?? {});
      } else {
        alert((e as Error).message);
      }
    }
  };

  const cidrErrors = Object.entries(errors).filter(([k]) => k.startsWith("in_scope_cidrs"));
  const ipErrors = Object.entries(errors).filter(([k]) => k.startsWith("out_of_scope_ips"));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Edit scope · {target}</DialogTitle>
        <DialogDescription>
          Constrains every offensive tool. Writes <code className="font-mono">scope.toml</code> to the target directory.
          CIDRs and IPs are validated server-side.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4 my-3">
        <div>
          <label className="block text-xs text-neutral-500 mb-1">in_scope_cidrs (one per line)</label>
          <Textarea
            value={cidrsText}
            onChange={(e) => setCidrsText(e.target.value)}
            rows={3}
            placeholder="10.10.10.0/24"
          />
          {cidrErrors.map(([k, v]) => (
            <p key={k} className="text-[10px] text-red-400 mt-1 font-mono">{k}: {v}</p>
          ))}
        </div>
        <div>
          <label className="block text-xs text-neutral-500 mb-1">out_of_scope_ips (one per line)</label>
          <Textarea
            value={ipsText}
            onChange={(e) => setIpsText(e.target.value)}
            rows={3}
            placeholder="10.10.10.99"
          />
          {ipErrors.map(([k, v]) => (
            <p key={k} className="text-[10px] text-red-400 mt-1 font-mono">{k}: {v}</p>
          ))}
        </div>
        <div>
          <label className="block text-xs text-neutral-500 mb-1">allowed_hours (freeform string)</label>
          <Input
            value={hours}
            onChange={(e) => setHours(e.target.value)}
            placeholder="09:00-17:00 UTC"
          />
        </div>
        <div className="flex gap-4 text-xs">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={noDos} onChange={(e) => setNoDos(e.target.checked)} />
            <span>no_dos</span>
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={noLockout} onChange={(e) => setNoLockout(e.target.checked)} />
            <span>no_account_lockout</span>
          </label>
        </div>
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button onClick={submit} disabled={update.isPending}>
          {update.isPending ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
