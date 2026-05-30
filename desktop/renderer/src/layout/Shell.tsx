import { Outlet, useLocation } from "react-router-dom";
import { StatusBar } from "./StatusBar";
import { ActivityBar } from "./ActivityBar";
import { Footer } from "./Footer";
import { SessionsPanel } from "./SessionsPanel";
import { TargetsPanel } from "./TargetsPanel";

/** Routes that get a 240px side panel slot. Pages not listed here
 *  (Settings, Health, NewEngagement) render full-width main content. */
function _sidePanelFor(pathname: string) {
  if (pathname === "/" || pathname.startsWith("/sessions") || pathname.startsWith("/session/")) {
    return <SessionsPanel />;
  }
  if (pathname.startsWith("/targets") || pathname.startsWith("/target/")) {
    return <TargetsPanel />;
  }
  return null;
}

export function Shell() {
  const { pathname } = useLocation();
  const sidePanel = _sidePanelFor(pathname);
  return (
    <div className="h-full w-full flex flex-col bg-[radial-gradient(circle_at_18%_0%,rgba(20,184,166,0.08),transparent_30%),linear-gradient(180deg,#09090b_0%,#0a0a0c_48%,#070708_100%)] text-neutral-100">
      <StatusBar />
      <div className="flex-1 flex min-h-0">
        <ActivityBar />
        {sidePanel && (
          <div className="w-60 min-w-0 shrink-0 border-r border-neutral-800/80 bg-neutral-950/55 backdrop-blur-sm">
            {sidePanel}
          </div>
        )}
        <main className="flex-1 min-w-0 overflow-hidden bg-neutral-950/35">
          <Outlet />
        </main>
      </div>
      <Footer />
    </div>
  );
}
