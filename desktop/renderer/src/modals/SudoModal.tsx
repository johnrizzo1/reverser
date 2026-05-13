import { useState } from "react";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSetSudo } from "@/api/queries";

export function SudoModal({
  sessionId, open, onOpenChange,
}: { sessionId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const [password, setPassword] = useState("");
  const setSudo = useSetSudo(sessionId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Sudo password</DialogTitle>
        <DialogDescription>
          Stored in memory on the Python service only — never written to disk.
          Required for nmap/netexec privileged scans.
        </DialogDescription>
      </DialogHeader>
      <Input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="••••••"
        autoFocus
      />
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button
          onClick={async () => {
            await setSudo.mutateAsync(password);
            setPassword("");
            onOpenChange(false);
          }}
        >Save</Button>
      </DialogFooter>
    </Dialog>
  );
}
