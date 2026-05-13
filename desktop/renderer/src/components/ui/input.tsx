import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...p }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded border border-neutral-700 bg-neutral-950 px-2 text-sm text-neutral-100 placeholder:text-neutral-500 focus:outline-none focus:border-neutral-500",
        className
      )}
      {...p}
    />
  )
);
Input.displayName = "Input";
