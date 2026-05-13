import type { Config } from "tailwindcss";

export default {
  darkMode: ["class"],
  content: ["renderer/index.html", "renderer/src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Severity (from spec section 4)
        severity: {
          high: "#ef4444",
          medium: "#f59e0b",
          low: "#3b82f6",
          info: "#6b7280",
        },
        // Hypothesis status
        status: {
          confirmed: "#22c55e",
          testing: "#f59e0b",
          proposed: "#6b7280",
          refuted: "#ef4444",
          abandoned: "#4b5563",
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
