import * as React from "react";
import { cn } from "@/lib/utils";

export function Dialog({
  open,
  onOpenChange,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
}) {
  React.useEffect(() => {
    if (!open) return;
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") onOpenChange(false); };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [open, onOpenChange]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
         onClick={() => onOpenChange(false)}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-neutral-900 border border-neutral-700 rounded shadow-xl min-w-[400px] max-w-[640px] p-5"
      >
        {children}
      </div>
    </div>
  );
}

export function DialogHeader({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("mb-3", className)}>{children}</div>;
}

export function DialogTitle({ children, className }: { children: React.ReactNode; className?: string }) {
  return <h3 className={cn("text-sm font-medium text-neutral-100", className)}>{children}</h3>;
}

export function DialogDescription({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-neutral-400 mt-1">{children}</p>;
}

export function DialogFooter({ children }: { children: React.ReactNode }) {
  return <div className="flex justify-end gap-2 mt-5">{children}</div>;
}
