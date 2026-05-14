import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useScreenshots } from "@/api/queries";
import { useConnection } from "@/state/connection";

export function ScreenshotLightboxModal({
  target,
  findingId,
  startIndex = 1,
  open,
  onOpenChange,
}: {
  target: string;
  findingId: string;
  startIndex?: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const screenshots = useScreenshots(target, findingId);
  const [currentIndex, setCurrentIndex] = useState(startIndex);

  const port = useConnection((s) => s.port);
  const token = useConnection((s) => s.token);

  useEffect(() => {
    if (open) setCurrentIndex(startIndex);
  }, [open, startIndex]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
      if (e.key === "ArrowLeft") setCurrentIndex((i) => Math.max(1, i - 1));
      if (e.key === "ArrowRight") {
        const max = screenshots.data?.screenshots.length ?? 1;
        setCurrentIndex((i) => Math.min(max, i + 1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange, screenshots.data]);

  const entries = screenshots.data?.screenshots ?? [];
  const total = entries.length;
  const safeIndex = Math.min(Math.max(currentIndex, 1), Math.max(total, 1));

  const imgUrl = port && token && target && findingId
    ? `http://127.0.0.1:${port}/api/targets/${encodeURIComponent(target)}/findings/${encodeURIComponent(findingId)}/screenshots/${safeIndex}`
    : null;
  const [imgBlobUrl, setImgBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !imgUrl || !token) { setImgBlobUrl(null); return; }
    let cancelled = false;
    let createdUrl: string | null = null;
    fetch(imgUrl, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => r.blob())
      .then((b) => {
        if (cancelled) return;
        createdUrl = URL.createObjectURL(b);
        setImgBlobUrl(createdUrl);
      })
      .catch(() => { if (!cancelled) setImgBlobUrl(null); });
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [open, imgUrl, token]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/90"
      onClick={() => onOpenChange(false)}
    >
      <button
        className="absolute top-4 right-4 text-neutral-300 hover:text-white"
        onClick={() => onOpenChange(false)}
      >
        <X className="w-6 h-6" />
      </button>

      <div className="absolute top-4 left-4 text-xs text-neutral-400 font-mono">
        screenshot {safeIndex} of {total} · finding {findingId}
      </div>

      <div className="flex items-center justify-center gap-4 w-full max-w-[95vw] max-h-[90vh]">
        <button
          disabled={safeIndex <= 1}
          onClick={(e) => { e.stopPropagation(); setCurrentIndex((i) => Math.max(1, i - 1)); }}
          className="text-neutral-400 hover:text-white disabled:opacity-30"
        >
          <ChevronLeft className="w-8 h-8" />
        </button>
        <div
          className="max-w-[80vw] max-h-[85vh] flex items-center justify-center"
          onClick={(e) => e.stopPropagation()}
        >
          {imgBlobUrl ? (
            <img
              src={imgBlobUrl}
              alt={`Screenshot ${safeIndex}`}
              className="max-w-full max-h-full object-contain"
            />
          ) : (
            <p className="text-neutral-500 text-sm">loading…</p>
          )}
        </div>
        <button
          disabled={safeIndex >= total}
          onClick={(e) => { e.stopPropagation(); setCurrentIndex((i) => Math.min(total, i + 1)); }}
          className="text-neutral-400 hover:text-white disabled:opacity-30"
        >
          <ChevronRight className="w-8 h-8" />
        </button>
      </div>
    </div>
  );
}
