import { Outlet } from "react-router-dom";
import { StatusBar } from "./StatusBar";
import { ActivityBar } from "./ActivityBar";
import { Footer } from "./Footer";

export function Shell() {
  return (
    <div className="h-full w-full flex flex-col bg-neutral-950 text-neutral-100">
      <StatusBar />
      <div className="flex-1 flex min-h-0">
        <ActivityBar />
        <main className="flex-1 min-w-0 overflow-auto">
          <Outlet />
        </main>
      </div>
      <Footer />
    </div>
  );
}
