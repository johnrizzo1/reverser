/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { useSessionStream } from "./useSessionStream";

const invalidateQueries = vi.fn();

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries }),
}));

vi.mock("@/state/connection", () => ({
  useConnection: (selector: (state: {
    port: number;
    token: string;
    status: string;
  }) => unknown) =>
    selector({ port: 60343, token: "test-token", status: "ready" }),
}));

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  close() {}

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
}

function StreamHarness() {
  useSessionStream("s1");
  return null;
}

describe("useSessionStream", () => {
  const originalWebSocket = globalThis.WebSocket;

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    FakeWebSocket.instances = [];
    invalidateQueries.mockReset();
  });

  it("invalidates KB queries when a generic KB frame arrives", async () => {
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
    render(<StreamHarness />);

    await waitFor(() => {
      expect(FakeWebSocket.instances.length).toBe(1);
    });
    FakeWebSocket.instances[0].emit({
      type: "kb",
      target: "reactor",
      tables: ["services", "credentials", "artifacts"],
    });

    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["kb"] });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["target-summary", "reactor"],
    });
  });
});
