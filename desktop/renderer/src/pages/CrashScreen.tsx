import { Button } from "@/components/ui/button";
import { useConnection } from "@/state/connection";

export function CrashScreen() {
  const error = useConnection((s) => s.errorMessage);
  return (
    <div className="h-full flex items-center justify-center">
      <div className="max-w-lg text-center p-8 border border-red-900/60 rounded bg-red-950/30">
        <h2 className="text-base font-medium text-red-200 mb-2">Backend service exited</h2>
        <p className="text-xs text-neutral-400 mb-4">
          The Python service stopped unexpectedly. You can restart the app
          to bring it back. Phase 4 will add in-app restart.
        </p>
        {error && (
          <pre className="text-[10px] text-red-300 bg-black/40 rounded p-2 overflow-auto text-left mb-4">
            {error}
          </pre>
        )}
        <Button
          variant="outline"
          onClick={() => window.location.reload()}
        >
          Reload window
        </Button>
      </div>
    </div>
  );
}
