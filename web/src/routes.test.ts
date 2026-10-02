import { describe, expect, it } from "vitest";
import { parseHash, runHash } from "./routes";

describe("routes", () => {
  it("parses the list, run and t forms", () => {
    expect(parseHash("")).toEqual({ name: "list" });
    expect(parseHash("#")).toEqual({ name: "list" });
    expect(parseHash("#/")).toEqual({ name: "list" });
    expect(parseHash("#/run/md-001-multi")).toEqual({ name: "run", runId: "md-001-multi", t: null });
    expect(parseHash("#/run/abc?t=12.5")).toEqual({ name: "run", runId: "abc", t: 12.5 });
    expect(parseHash("#/run/abc?t=0")).toEqual({ name: "run", runId: "abc", t: 0 });
  });

  it("treats every other hash as not found", () => {
    for (const h of [
      "#/run/",
      "#/run/a_b",
      "#/run/a b",
      "#/run/abc?t=-1",
      "#/run/abc?t=x",
      "#/run/abc?t=",
      "#/run/abc?x=1",
      "#/run/abc/extra",
      "#/runs/abc",
      "#/other",
      "#nope",
      "/run/abc",
    ]) {
      expect(parseHash(h), h).toEqual({ name: "notFound" });
    }
  });

  it("runHash round-trips", () => {
    expect(runHash("abc")).toBe("#/run/abc");
    expect(runHash("abc", 3)).toBe("#/run/abc?t=3.00");
    expect(parseHash(runHash("a-1", 7.126))).toEqual({ name: "run", runId: "a-1", t: 7.13 });
    expect(parseHash(runHash("a-1"))).toEqual({ name: "run", runId: "a-1", t: null });
  });
});
