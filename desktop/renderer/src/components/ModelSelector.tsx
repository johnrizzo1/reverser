import { RefreshCw } from "lucide-react";
import { useBackendModels } from "@/api/queries";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

type Props = {
  backend: string;
  apiBase: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  placeholder?: string;
};

export function ModelSelector({
  backend,
  apiBase,
  value,
  onChange,
  disabled,
  placeholder,
}: Props) {
  const debouncedApiBase = useDebouncedValue(apiBase, 500);
  const query = useBackendModels(backend, debouncedApiBase);

  const effectiveApiBase = apiBase || defaultApiBaseFor(backend);
  const models = query.data?.models ?? [];
  const isLoading = query.isFetching && !query.data;
  const isError = query.isError;
  const isEmptySuccess = query.isSuccess && models.length === 0;

  let field: React.ReactNode;
  let hint: string | null = null;

  if (isError) {
    field = (
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder={placeholder}
      />
    );
    hint = `couldn't reach ${effectiveApiBase} — enter model manually`;
  } else if (isEmptySuccess) {
    field = (
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder={placeholder}
      />
    );
    hint = `no models installed on ${effectiveApiBase}`;
  } else if (isLoading) {
    field = (
      <Select disabled value="">
        <option value="">Loading models…</option>
      </Select>
    );
  } else {
    const ids = models.map((m) => m.id);
    const savedNotInList = value && !ids.includes(value);
    field = (
      <Select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      >
        {!value && <option value="">Select a model…</option>}
        {savedNotInList && (
          <option value={value}>{value} (not on server)</option>
        )}
        {ids.map((id) => (
          <option key={id} value={id}>
            {id}
          </option>
        ))}
      </Select>
    );
  }

  return (
    <div>
      <div className="flex gap-2">
        <div className="flex-1">{field}</div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={disabled || query.isFetching}
          onClick={() => query.refetch()}
          title="Refresh model list"
          aria-label="Refresh model list"
        >
          <RefreshCw
            className={`h-4 w-4 ${query.isFetching ? "animate-spin" : ""}`}
          />
        </Button>
      </div>
      {hint && <p className="mt-1 text-xs text-neutral-400">{hint}</p>}
    </div>
  );
}

function defaultApiBaseFor(backend: string): string {
  if (backend === "lmstudio") return "http://localhost:1234/v1";
  if (backend === "ollama") return "http://localhost:11434/v1";
  return "";
}
