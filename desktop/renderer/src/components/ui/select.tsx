import * as React from "react";
import { cn } from "@/lib/utils";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...p }, ref) => (
    <select
      ref={ref}
      className={cn(
        "h-9 w-full rounded border border-neutral-700 bg-neutral-950 px-2 text-sm text-neutral-100 focus:outline-none focus:border-neutral-500",
        className
      )}
      {...p}
    />
  )
);
Select.displayName = "Select";
