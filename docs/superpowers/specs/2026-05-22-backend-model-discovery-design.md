# Backend Model Discovery — Design Spec

**Date:** 2026-05-22
**Status:** Approved for planning
**Scope:** When the configured backend is `lmstudio` or `ollama`, query the local server for available models and present them in a dropdown — both in the New Engagement form and in the per-session config panel — instead of requiring the operator to type the model name by hand.

## 1. Goals & non-goals

### Goals

- Operator picks a model from a dropdown when `backend ∈ {lmstudio, ollama}`. No more typo-fragile free-text entry.
- The dropdown reflects what's actually installed on the local server the session will hit.
- The feature degrades gracefully: if the server is unreachable, the UI falls back to the existing free-text input so the user can still save.
- The operator can manually refresh the model list after starting their local server, without having to switch backend and switch back.
- The saved model value is never silently mutated. If a session's saved model is no longer on the server, it stays selected with a "(not on server)" marker.

### Non-goals (this phase)

- Model discovery for other backends (`claude`, `openai`, `gemini`, etc.). They keep the existing text input. Their model namespace is large, mostly versioned, and not queryable from a single local endpoint.
- Showing model metadata (size, family, quantization). Just the id string.
- Persisting a "favorite models" list, or remembering recent selections across sessions.
- Validating model capability (context size, tool-use support) at selection time. The agent fails at first-turn time as today.
- A Python-side check of whether the saved model still exists when a session is resumed. (Future work; would need a non-blocking probe.)
- Combobox / "type to filter and also accept arbitrary values" behavior. The fallback handles the "model not in list" case.

## 2. Architecture

### 2.1 New gui_service endpoint

`GET /api/backends/{backend}/models?api_base=<optional>`

- `backend` path param: must be `lmstudio` or `ollama`. Any other value returns `404` with `{"error": "model discovery not supported for backend '<name>'"}`.
- `api_base` query param: optional. When omitted, the endpoint uses the default for that backend:
  - `lmstudio` → `http://localhost:1234/v1`
  - `ollama` → `http://localhost:11434/v1`
- The defaults currently live inline in `create_backend()` in `src/reverser/backends/__init__.py`. As part of this work, extract them into a module-level `DEFAULT_API_BASES: dict[str, str]` constant in that file and import it from both `create_backend()` and the new route, so the defaults stay in one place.
- The endpoint issues `GET <api_base>/models` server-side via `httpx.AsyncClient` with a **3-second timeout**.
- On success (200): returns `{"models": [{"id": "<model-id>"}, ...]}` with ids sorted alphabetically. Raw id strings are preserved (no munging).
- On network error / timeout / non-200 from the local server: returns `502` with `{"error": "unreachable", "detail": "<short message including the api_base attempted>"}`.

The endpoint does **not** require an active session — it's a stateless lookup. It does require the same bearer-token auth the rest of `/api/*` uses.

### 2.2 Why proxy through gui_service

- Matches the existing pattern (all backend traffic flows through gui_service; nothing in the renderer hits `localhost:1234` directly).
- Avoids CORS in the packaged Electron app — lmstudio and ollama don't set permissive `Access-Control-Allow-Origin` headers by default.
- Reusable from future surfaces (CLI, Python tooling, scheduled jobs) without re-implementing the protocol probing.

### 2.3 Frontend hook

In `desktop/renderer/src/queries.ts`:

```ts
export function useBackendModels(backend: string, apiBase: string) {
  const ready = useReady();
  const supports = backend === "lmstudio" || backend === "ollama";
  return useQuery({
    queryKey: ["backend-models", backend, apiBase],
    queryFn: () => api.get<ModelsResponse>(
      `/api/backends/${backend}/models${apiBase ? `?api_base=${encodeURIComponent(apiBase)}` : ""}`
    ),
    enabled: ready && supports,
    staleTime: 30_000,
    retry: false,
  });
}
```

- `retry: false` because the failure case is part of the UX (it routes to the text-input fallback) — silent retries would hide it.
- `staleTime: 30s` balances "fresh enough to reflect a just-installed model" against "don't hammer the local server while the user clicks around."

### 2.4 Shared ModelSelector component

New file: `desktop/renderer/src/components/ModelSelector.tsx`. Props:

```ts
type Props = {
  backend: string;
  apiBase: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
};
```

Internally:

- `apiBase` is wrapped in a small `useDebouncedValue(apiBase, 500)` so the query doesn't fire on every keystroke as the user edits the URL. The debounce helper lives next to the component (or in `lib/`).
- The component calls `useBackendModels(backend, debouncedApiBase)`.
- A small refresh icon button (using the existing `lucide-react` icon set) sits to the right of the select/input and calls `query.refetch()`. It's enabled even in the fallback case so the operator can retry after starting their local server.

#### Render states

| State | Render |
|-------|--------|
| `isLoading` | `<Select>` showing "Loading models…", disabled. |
| Success, list non-empty, value ∈ list | `<Select>` with sorted model ids; current value selected. |
| Success, list non-empty, value ∉ list, value non-empty | `<Select>` with sorted model ids, plus a leading option `<value> (not on server)` that is selected. Selecting it is a no-op (it's the current saved value). |
| Success, list empty | Plain `<Input>` fallback. Inline hint text below the field: `"no models installed on <api_base>"`. |
| Error | Plain `<Input>` fallback. Inline hint text below the field: `"couldn't reach <api_base> — enter model manually"`. |

The hook's success/error branches map to those rows as follows: a `200` from the gui_service endpoint is always "success" (even if `models` is empty — that's the empty-list row); any non-2xx (including the `502` for unreachable) lands in `query.isError` and renders the error row. The renderer never needs to inspect the response body to tell empty from unreachable.

When `backend` is not `lmstudio` or `ollama`, the parent forms don't render `ModelSelector` at all — they keep the existing `<Input>`.

### 2.5 Integration in the two forms

Both `desktop/renderer/src/pages/NewEngagement.tsx` and `desktop/renderer/src/layout/SessionConfigPanel.tsx` replace their existing model `<Input>` with:

```tsx
{(backend === "lmstudio" || backend === "ollama") ? (
  <ModelSelector
    backend={backend}
    apiBase={apiBase}
    value={model}
    onChange={setModel}
    disabled={readOnly}
  />
) : (
  <Input
    value={model}
    onChange={(e) => setModel(e.target.value)}
    disabled={readOnly}
    placeholder="e.g. qwen3.5:35b"
  />
)}
```

`readOnly` in `SessionConfigPanel` follows the existing rule (session must be `stopped`); in `NewEngagement` it's never read-only.

The existing form-state and diff/save plumbing in both files is untouched — `ModelSelector` is a drop-in for the model field.

## 3. Edge cases

- **User switches backend mid-form** (e.g., `claude` → `lmstudio`): the query becomes enabled, fires, dropdown appears. Switching back disables the query and re-renders the plain `Input`.
- **User edits `api_base` while typing**: 500 ms debounce; the last successful list stays visible until the new query resolves.
- **Saved model not in list**: prepended as the selected option labeled `(not on server)`. The user can pick something else or save as-is. The PATCH endpoint's existing validation (model non-null for backends with `requires_model`) is satisfied.
- **Server reachable but `/v1/models` returns 200 with `{"data": []}`**: treated as the "empty list" branch — text-input fallback with "no models installed" hint.
- **Server returns malformed JSON or unexpected shape**: treated as error branch (text-input fallback with the "couldn't reach" hint). The Python endpoint catches the parse error and returns `502`.
- **Auth on the local server**: lmstudio and ollama don't require auth on `/v1/models` in their default configurations. If the user has a non-default setup that does, the request will fail and the fallback kicks in — out of scope to support.

## 4. Testing

### Python (`tests/test_backend_models_route.py`)

Three tests, using a monkeypatched `httpx.AsyncClient` (no new test deps — `httpx` is already in `pyproject.toml`):

1. **Happy path**: mock returns `{"data": [{"id": "qwen3.5:35b"}, {"id": "llama3.1:8b"}]}`. Assert response is `200` with `models` sorted alphabetically.
2. **Unreachable**: mock raises `httpx.ConnectError`. Assert `502` with `error: "unreachable"` and the attempted api_base in `detail`.
3. **Unsupported backend**: `GET /api/backends/claude/models` → `404`.

The endpoint's auth wrapper is exercised by the existing test harness in the same way other `/api/*` routes are.

### Desktop frontend

No component-test framework is set up on the renderer (only Playwright e2e). Two complementary checks:

1. **Manual verification** documented in the implementation plan: run a local lmstudio and ollama, switch backends in both forms, confirm dropdowns populate, edit `api_base` to a wrong port and confirm the fallback kicks in, click refresh.
2. **Playwright e2e smoke** (`desktop/tests/e2e/model-selector.spec.ts`): follows the same pattern as the existing specs in that directory. Uses Playwright route interception (`page.route("**/api/backends/lmstudio/models*", ...)`) to serve a fixed payload, and confirms the dropdown appears with the seeded options. A second case routes the same URL to a 502 and asserts the fallback hint text is visible. Implementation plan should verify the route-intercept pattern works against the existing e2e harness before committing to the test shape; if the harness reaches the route before Playwright can intercept (e.g., gui_service is in-process and bypasses the renderer's network stack), drop the e2e and rely on manual verification alone — flag this in the plan.

## 5. Implementation order

Suggested sequence for the implementation plan:

1. Python: add the route + tests. Land this first; it's independently testable.
2. Frontend: add `useBackendModels` hook + the small `useDebouncedValue` helper.
3. Frontend: build `ModelSelector` component.
4. Frontend: wire `ModelSelector` into `NewEngagement.tsx`.
5. Frontend: wire `ModelSelector` into `SessionConfigPanel.tsx`.
6. Playwright smoke (if feasible) + manual verification pass.

Each step is independently shippable; an in-progress mid-stack state still leaves the existing free-text input working.

## 6. Open questions

None at the time of writing. All ambiguities surfaced during brainstorming were resolved.
