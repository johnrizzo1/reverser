import * as React from "react";
import { cn } from "@/lib/utils";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...p }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "w-full rounded-md border border-neutral-700 bg-neutral-950/70 px-2.5 py-1.5 text-sm text-neutral-100 placeholder:text-neutral-500 focus:outline-none focus:border-cyan-300/70 focus:ring-1 focus:ring-cyan-300/30 resize-none",
        className
      )}
      {...p}
    />
  )
);
Textarea.displayName = "Textarea";
