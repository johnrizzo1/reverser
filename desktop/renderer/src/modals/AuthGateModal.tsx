import { Button } from "@/components/ui/button";
import {
  Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

const ACK = "I have written authorization to test this target.";

export function AuthGateModal({
  open, onOpenChange, onAcknowledged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAcknowledged: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Pentest authorization required</DialogTitle>
        <DialogDescription>
          Network-touching profiles (pentest, webpentest, ad, manager, exploit, …)
          require explicit authorization. Confirming below writes a
          {" "}<code className="font-mono">.reverser-authorized</code> marker file
          in this project root.
        </DialogDescription>
      </DialogHeader>

      <p className="text-xs text-neutral-300 my-3 leading-relaxed">
        By continuing you affirm: <em>{ACK}</em>
      </p>

      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button
          variant="destructive"
          onClick={async () => {
            await window.desktop.writeAuthMarker();
            onAcknowledged();
            onOpenChange(false);
          }}
        >
          I confirm
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
