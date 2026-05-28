import { describe, expect, it } from "vitest";
import {
  classifyTargetIntent,
  recommendedProfileForIntent,
  profileMatchesIntent,
} from "./targetIntent";

describe("target intent guidance", () => {
  it("classifies URLs as web targets", () => {
    expect(classifyTargetIntent("https://example.com")).toBe("web");
    expect(recommendedProfileForIntent("web")).toBe("webpentest");
  });

  it("classifies IP addresses and hostnames as network targets", () => {
    expect(classifyTargetIntent("10.10.10.5")).toBe("network");
    expect(classifyTargetIntent("dc1.corp.local")).toBe("network");
    expect(recommendedProfileForIntent("network")).toBe("manager");
  });

  it("classifies obvious filesystem paths as binary targets", () => {
    expect(classifyTargetIntent("/tmp/sample.bin")).toBe("binary");
    expect(classifyTargetIntent("./cracks/boring/boring")).toBe("binary");
    expect(recommendedProfileForIntent("binary")).toBe("general");
  });

  it("flags mismatched profiles", () => {
    expect(profileMatchesIntent("general", "web")).toBe(false);
    expect(profileMatchesIntent("custom-web", "web", "web")).toBe(true);
    expect(profileMatchesIntent("custom-net", "network", "network")).toBe(true);
    expect(profileMatchesIntent("webpentest", "web")).toBe(true);
    expect(profileMatchesIntent("manager", "network")).toBe(true);
    expect(profileMatchesIntent("general", "binary")).toBe(true);
  });
});
