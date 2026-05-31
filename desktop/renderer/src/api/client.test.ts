import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the connection store so `request()` sees a ready connection.
vi.mock("@/state/connection", () => ({
  useConnection: {
    getState: () => ({
      status: "ready",
      port: 9090,
      token: "test-token",
    }),
  },
}));

import { refocusTarget } from "./client";

describe("refocusTarget", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("POSTs new_ip to the refocus endpoint and returns the result", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        target: "box",
        old_ip: "10.0.0.1",
        new_ip: "10.0.0.2",
        rows_remapped: { hosts: 2, services: 1, cred_results: 0 },
        hostname_updated: false,
        scope_warning: null,
        session_refocused: false,
        new_address_id: "addr-abc",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const res = await refocusTarget("box", { new_ip: "10.0.0.2" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:9090/api/targets/box/refocus");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body as string)).toMatchObject({ new_ip: "10.0.0.2" });
    expect(opts.headers).toMatchObject({ Authorization: "Bearer test-token" });
    expect(res.new_ip).toBe("10.0.0.2");
    expect(res.old_ip).toBe("10.0.0.1");
    expect(res.rows_remapped.hosts).toBe(2);
  });

  it("URL-encodes the target name", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        target: "my box",
        old_ip: "1.1.1.1",
        new_ip: "2.2.2.2",
        rows_remapped: { hosts: 0, services: 0, cred_results: 0 },
        hostname_updated: false,
        scope_warning: null,
        session_refocused: false,
        new_address_id: "addr-xyz",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await refocusTarget("my box", { new_ip: "2.2.2.2" });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/targets/my%20box/refocus");
  });

  it("passes optional fields when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        target: "box",
        old_ip: "10.0.0.1",
        new_ip: "10.0.0.3",
        rows_remapped: { hosts: 1, services: 0, cred_results: 0 },
        hostname_updated: true,
        scope_warning: null,
        session_refocused: true,
        new_address_id: "addr-def",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await refocusTarget("box", {
      new_ip: "10.0.0.3",
      hostname: "myhost.local",
      update_etc_hosts: true,
      force_scope: false,
    });

    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(opts.body as string)).toMatchObject({
      new_ip: "10.0.0.3",
      hostname: "myhost.local",
      update_etc_hosts: true,
      force_scope: false,
    });
  });
});
