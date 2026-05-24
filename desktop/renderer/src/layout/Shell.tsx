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
    <div className="h-full w-full flex flex-col bg-neutral-950 text-neutral-100">
      <StatusBar />
      <div className="flex-1 flex min-h-0">
        <ActivityBar />
        {sidePanel && (
          <div className="w-60 min-w-0 shrink-0">{sidePanel}</div>
        )}
        <main className="flex-1 min-w-0 overflow-hidden">
          <Outlet />
        </main>
      </div>
      <Footer />
    </div>
  );
}
