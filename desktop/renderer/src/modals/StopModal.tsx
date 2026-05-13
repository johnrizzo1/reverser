import { useNavigate } from "react-router-dom";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useStopSession } from "@/api/queries";

export function StopModal({
  sessionId, open, onOpenChange,
}: { sessionId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const stop = useStopSession();
  const navigate = useNavigate();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Stop engagement?</DialogTitle>
        <DialogDescription>
          The session is snapshotted and can be resumed later from the
          New-engagement page.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button
          variant="destructive"
          onClick={async () => {
            await stop.mutateAsync(sessionId);
            onOpenChange(false);
            navigate("/");
          }}
        >Stop</Button>
      </DialogFooter>
    </Dialog>
  );
}
