import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

export function SessionsIndex() {
  return (
    <div className="h-full flex flex-col items-center justify-center gap-4">
      <p className="text-sm text-neutral-500">
        Select a session from the panel on the left, or start a new one.
      </p>
      <Link to="/new">
        <Button>New engagement</Button>
      </Link>
    </div>
  );
}
