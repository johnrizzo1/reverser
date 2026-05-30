/// <reference types="@testing-library/jest-dom" />
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ChatPane } from "./ChatPane";
import { clearSessionStore, getSessionStore } from "@/state/session-store";

const sendMutateAsync = vi.fn();
const queueMutateAsync = vi.fn();
const deleteMutateAsync = vi.fn();

vi.mock("@/api/queries", () => ({
  useSendMessage: () => ({ isPending: true, mutateAsync: sendMutateAsync }),
  useQueuePendingMessage: () => ({ isPending: false, mutateAsync: queueMutateAsync }),
  useDeletePendingMessage: () => ({ isPending: false, mutateAsync: deleteMutateAsync }),
}));

describe("ChatPane queued messages", () => {
  beforeAll(() => {
    Element.prototype.scrollTo = vi.fn();
  });

  afterEach(() => {
    clearSessionStore("chat-queue-test");
    sendMutateAsync.mockClear();
    queueMutateAsync.mockClear();
    deleteMutateAsync.mockClear();
  });

  it("allows queueing while the normal send mutation is still pending", async () => {
    const store = getSessionStore("chat-queue-test");
    store.getState().ingest({ type: "status", phase: "running", turns: 1 });
    render(<ChatPane sessionId="chat-queue-test" />);

    const textarea = screen.getByPlaceholderText(/type a message/i);
    fireEvent.change(textarea, { target: { value: "change direction" } });

    const queueButton = screen.getByRole("button", { name: /queue/i });
    expect(queueButton).toBeEnabled();
    fireEvent.click(queueButton);

    expect(queueMutateAsync).toHaveBeenCalledWith("change direction");
    expect(sendMutateAsync).not.toHaveBeenCalled();
  });

  it("queues with Cmd+Enter while the normal send mutation is still pending", async () => {
    const store = getSessionStore("chat-queue-test");
    store.getState().ingest({ type: "status", phase: "running", turns: 1 });
    render(<ChatPane sessionId="chat-queue-test" />);

    const textarea = screen.getByPlaceholderText(/type a message/i);
    fireEvent.change(textarea, { target: { value: "queue from keyboard" } });
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true });

    expect(queueMutateAsync).toHaveBeenCalledWith("queue from keyboard");
    expect(sendMutateAsync).not.toHaveBeenCalled();
  });
});
