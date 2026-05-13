import { NavLink } from "react-router-dom";
import { Home, Heart, Settings } from "lucide-react";
import { cn } from "@/lib/utils";

const ICONS = [
  { to: "/", label: "Dashboard", icon: Home },
  { to: "/health", label: "Health", icon: Heart },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function ActivityBar() {
  return (
    <nav className="w-12 border-r border-neutral-800 bg-neutral-950 flex flex-col items-center py-2 gap-1">
      {ICONS.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === "/"}
          title={label}
          className={({ isActive }) =>
            cn(
              "w-9 h-9 flex items-center justify-center rounded transition-colors",
              isActive ? "bg-neutral-800 text-neutral-100" : "text-neutral-500 hover:text-neutral-200 hover:bg-neutral-900"
            )
          }
        >
          <Icon className="w-4 h-4" />
        </NavLink>
      ))}
    </nav>
  );
}
