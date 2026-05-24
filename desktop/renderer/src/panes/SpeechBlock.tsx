// desktop/renderer/src/panes/SpeechBlock.tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function SpeechBlock({ deltas }: { deltas: string[] }) {
  const text = deltas.join("");
  if (!text.trim()) return null;
  return (
    <div className="prose prose-invert prose-sm max-w-none text-neutral-200">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
