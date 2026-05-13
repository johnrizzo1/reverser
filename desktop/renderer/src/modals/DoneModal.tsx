import { useNavigate } from "react-router-dom";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useMarkDone } from "@/api/queries";

export function DoneModal({
  sessionId, open, onOpenChange,
}: { sessionId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const done = useMarkDone();
  const navigate = useNavigate();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Mark engagement done?</DialogTitle>
        <DialogDescription>
          This is terminal — the session can no longer be resumed.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button
          onClick={async () => {
            await done.mutateAsync(sessionId);
            onOpenChange(false);
            navigate("/");
          }}
        >Mark done</Button>
      </DialogFooter>
    </Dialog>
  );
}
