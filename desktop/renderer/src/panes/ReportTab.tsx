import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useReport, useExportReport } from "@/api/queries";
import { Button } from "@/components/ui/button";

const PROSE_FALLBACK_CLASS =
  "text-sm text-neutral-200 max-w-none " +
  "[&_h1]:text-base [&_h1]:font-medium [&_h1]:mt-4 [&_h1]:mb-2 " +
  "[&_h2]:text-sm [&_h2]:font-medium [&_h2]:mt-3 [&_h2]:mb-1 " +
  "[&_p]:my-2 " +
  "[&_table]:text-xs [&_table]:border [&_table]:border-neutral-800 " +
  "[&_th]:px-2 [&_th]:py-1 [&_th]:border-b [&_th]:border-neutral-800 " +
  "[&_td]:px-2 [&_td]:py-1 [&_td]:border-b [&_td]:border-neutral-900 " +
  "[&_code]:font-mono [&_code]:text-amber-300 " +
  "[&_pre]:bg-neutral-900 [&_pre]:p-2 [&_pre]:rounded";

export function ReportTab({ target }: { target: string | null }) {
  const { data, isLoading, error } = useReport(target);
  const exportMutation = useExportReport(target ?? "");

  if (!target) return <p className="p-3 text-xs text-neutral-500">no target</p>;
  if (isLoading) return <p className="p-3 text-xs text-neutral-500">rendering report…</p>;
  if (error) return <p className="p-3 text-xs text-red-400">{String((error as Error).message)}</p>;
  if (!data) return null;

  const onExport = async () => {
    try {
      const res = await exportMutation.mutateAsync();
      alert(`Saved to ${res.path}`);
    } catch (e) {
      alert((e as Error).message);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-h-0 overflow-auto p-4">
        <article className={PROSE_FALLBACK_CLASS}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.markdown}</ReactMarkdown>
        </article>
      </div>
      <div className="border-t border-neutral-800 px-3 py-2 flex items-center text-[10px] text-neutral-500 font-mono">
        <span>Generated {data.generated_at} · {data.bytes} bytes</span>
        <Button
          size="sm" variant="outline"
          className="ml-auto"
          onClick={onExport}
          disabled={exportMutation.isPending}
        >
          {exportMutation.isPending ? "Saving…" : "Export to disk"}
        </Button>
      </div>
    </div>
  );
}
