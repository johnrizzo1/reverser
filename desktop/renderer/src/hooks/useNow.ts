import { useEffect, useState } from "react";

/** Re-renders the caller every `intervalMs` and returns the current epoch ms.
 *  Used to recompute "idle for N min" displays without a new data frame. */
export function useNow(intervalMs = 15_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
