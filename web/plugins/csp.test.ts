import { describe, expect, it } from "vitest";
import type { Plugin } from "vite";
import { CSP, cspMeta } from "./csp";

const EXACT =
  "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'";

describe("cspMeta", () => {
  it("adds the exact policy at build time only", () => {
    const plugin: Plugin = cspMeta();
    expect(CSP).toBe(EXACT);
    expect(plugin.apply).toBe("build");
    const hook = plugin.transformIndexHtml;
    const fn = typeof hook === "function" ? hook : hook?.handler;
    const tags = (fn as unknown as () => { tag: string; attrs: Record<string, string> }[])();
    expect(tags).toHaveLength(1);
    expect(tags[0].tag).toBe("meta");
    expect(tags[0].attrs["http-equiv"]).toBe("Content-Security-Policy");
    expect(tags[0].attrs.content).toBe(EXACT);
    expect(CSP).not.toContain("unsafe-inline");
  });
});
