// desktop/renderer/src/panes/UserBubble.tsx
export function UserBubble({ text }: { text: string }) {
  return (
    <div className="max-w-[75%] ml-auto bg-neutral-800 text-neutral-100 rounded px-3 py-2 text-sm whitespace-pre-wrap">
      {text}
    </div>
  );
}
