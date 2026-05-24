import { NavLink, useLocation } from "react-router-dom";
import { Layers, Target, Heart, Settings } from "lucide-react";
import { cn } from "@/lib/utils";

const ICONS = [
  { to: "/targets", label: "Targets", icon: Target,
    match: (p: string) => p.startsWith("/targets") || p.startsWith("/target/") },
  { to: "/sessions", label: "Sessions", icon: Layers,
    match: (p: string) => p === "/" || p.startsWith("/sessions") || p.startsWith("/session/") },
  { to: "/health", label: "Backend Health", icon: Heart, match: (p: string) => p === "/health" },
  { to: "/settings", label: "Settings", icon: Settings, match: (p: string) => p === "/settings" },
];

export function ActivityBar() {
  const { pathname } = useLocation();
  return (
    <nav className="w-12 border-r border-neutral-800 bg-neutral-950 flex flex-col items-center py-2 gap-1">
      {ICONS.map(({ to, label, icon: Icon, match }) => {
        const lit = match(pathname);
        return (
          <NavLink
            key={to}
            to={to}
            title={label}
            className={cn(
              "w-9 h-9 flex items-center justify-center rounded transition-colors",
              lit
                ? "bg-neutral-800 text-neutral-100"
                : "text-neutral-500 hover:text-neutral-200 hover:bg-neutral-900",
            )}
          >
            <Icon className="w-4 h-4" />
          </NavLink>
        );
      })}
    </nav>
  );
}
