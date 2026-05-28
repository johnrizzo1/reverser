import * as React from "react";
import { cn } from "@/lib/utils";

export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...p }, ref) => (
    <div
      ref={ref}
      className={cn(
        "rounded-md border border-[rgb(var(--rv-border))] bg-[rgb(var(--rv-panel)/0.72)] shadow-sm",
        className,
      )}
      {...p}
    />
  )
);
Card.displayName = "Card";

export const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...p }, ref) => (
    <div
      ref={ref}
      className={cn("p-4 border-b border-[rgb(var(--rv-border))]", className)}
      {...p}
    />
  )
);
CardHeader.displayName = "CardHeader";

export const CardTitle = React.forwardRef<HTMLHeadingElement, React.HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...p }, ref) => (
    <h3 ref={ref} className={cn("text-sm font-medium text-neutral-100", className)} {...p} />
  )
);
CardTitle.displayName = "CardTitle";

export const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...p }, ref) => (
    <div ref={ref} className={cn("p-4 text-sm text-neutral-300", className)} {...p} />
  )
);
CardContent.displayName = "CardContent";
