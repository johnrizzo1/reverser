// desktop/renderer/src/panes/UserBubble.tsx
export function UserBubble({ text }: { text: string }) {
  return (
    <div className="ml-auto max-w-[78%] rounded-md border border-cyan-400/20 bg-cyan-950/30 px-3 py-2 text-sm text-neutral-100 shadow-sm">
      <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-cyan-300/70">
        Analyst
      </div>
      <div className="whitespace-pre-wrap">{text}</div>
    </div>
  );
}
