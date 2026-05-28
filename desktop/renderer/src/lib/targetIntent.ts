export type TargetIntent = "binary" | "web" | "network" | "unknown";

const WEB_PROFILES = new Set(["webpentest", "webapi", "webrecon"]);
const NETWORK_PROFILES = new Set(["pentest", "manager", "ad", "exploit"]);

export function classifyTargetIntent(value: string): TargetIntent {
  const target = value.trim();
  if (!target) return "unknown";

  if (/^https?:\/\//i.test(target)) return "web";
  if (target.startsWith("/") || target.startsWith("./") || target.startsWith("../")) {
    return "binary";
  }
  if (/^[A-Za-z]:[\\/]/.test(target)) return "binary";
  if (/^\d{1,3}(?:\.\d{1,3}){3}(?:\/\d{1,2})?$/.test(target)) return "network";
  if (/^[a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+$/i.test(target)) {
    return "network";
  }

  return "unknown";
}

export function recommendedProfileForIntent(intent: TargetIntent): string {
  if (intent === "web") return "webpentest";
  if (intent === "network") return "manager";
  if (intent === "binary") return "general";
  return "general";
}

export function profileMatchesIntent(
  profile: string,
  intent: TargetIntent,
  profileDomain?: string,
): boolean {
  if (intent === "unknown") return true;
  if (profileDomain === "web") return intent === "web";
  if (profileDomain === "network") return intent === "network";
  if (profileDomain === "binary") return intent === "binary";
  if (intent === "web") return WEB_PROFILES.has(profile);
  if (intent === "network") return NETWORK_PROFILES.has(profile);
  if (intent === "binary") {
    return !WEB_PROFILES.has(profile) && !NETWORK_PROFILES.has(profile);
  }
  return true;
}

export function intentLabel(intent: TargetIntent): string {
  if (intent === "web") return "web application";
  if (intent === "network") return "network host or engagement";
  if (intent === "binary") return "binary or local file";
  return "unknown target type";
}
