import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { PendingMessage } from "@/state/session-store";

export function PendingMessageBubble({
  message,
  onDelete,
  deleting = false,
}: {
  message: PendingMessage;
  onDelete: (id: string) => void;
  deleting?: boolean;
}) {
  return (
    <div className="ml-auto max-w-[78%] rounded-md border border-amber-500/25 bg-amber-950/20 px-3 py-2 text-sm text-neutral-100 shadow-sm">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-[10px] font-medium uppercase tracking-wide text-amber-300/80">
          Queued for next turn
        </span>
        <Button
          size="icon"
          variant="ghost"
          className="ml-auto h-6 w-6 text-neutral-500 hover:text-red-200"
          onClick={() => onDelete(message.id)}
          disabled={deleting}
          title="Delete queued message"
          aria-label="Delete queued message"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="whitespace-pre-wrap">{message.text}</div>
    </div>
  );
}
