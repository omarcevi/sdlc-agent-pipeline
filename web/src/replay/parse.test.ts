import { describe, expect, it } from "vitest";
import { ReplayError, parseGraphs, parseIndex, parseReplay } from "./parse";
import { fixtureGraphs, makeIndex, makeReplay } from "../test/factories";

const graphs = fixtureGraphs();

// A plain-JSON copy that tests can mutate freely.
const raw = (which: "multi" | "single" = "multi") =>
  JSON.parse(JSON.stringify(makeReplay(which))) as Record<string, any>;

describe("parseReplay", () => {
  it("accepts the multi and single fixtures", () => {
    const multi = parseReplay(raw("multi"), graphs);
    const single = parseReplay(raw("single"), graphs);
    expect(multi.steps).toHaveLength(19);
    expect(single.run.graph).toBe("single");
    expect(parseGraphs(graphs).graphs.multi.nodes.length).toBeGreaterThan(0);
    expect(parseIndex(makeIndex()).replays).toHaveLength(2);
  });

  it("rejects an unsupported schema", () => {
    const r = raw();
    r.schema = 2;
    expect(() => parseReplay(r, graphs)).toThrow(new ReplayError("unsupported schema"));
  });

  it("rejects steps out of order", () => {
    const badIndex = raw();
    badIndex.steps[3].i = 7;
    expect(() => parseReplay(badIndex, graphs)).toThrow("steps out of order");
    const badTime = raw();
    badTime.steps[5].t = 0.1;
    expect(() => parseReplay(badTime, graphs)).toThrow("steps out of order");
  });

  it("rejects a node that is not in the graph", () => {
    const r = raw();
    r.steps[2].node = "no_such_node";
    expect(() => parseReplay(r, graphs)).toThrow("node not in graph");
    const single = raw("single");
    single.steps[2].node = "planner";
    expect(() => parseReplay(single, graphs)).toThrow("node not in graph");
  });

  it("keeps unknown step kinds", () => {
    const r = raw();
    r.steps[3] = { ...r.steps[3], kind: "telemetry", extra: 1 };
    const parsed = parseReplay(r, graphs);
    expect(parsed.steps).toHaveLength(19);
    const step = parsed.steps[3];
    expect(step.kind).toBe("other");
    if (step.kind === "other") expect(step.name).toBe("telemetry");
  });

  it("ignores unknown fields", () => {
    const r = raw();
    r.future = { a: 1 };
    r.run.future = true;
    r.steps[0].future = "x";
    expect(() => parseReplay(r, graphs)).not.toThrow();
  });

  it("reports the path of a missing field", () => {
    const r = raw();
    delete r.run.issue.title;
    expect(() => parseReplay(r, graphs)).toThrow("missing field: $.run.issue.title");
    const s = raw();
    delete s.steps[4].value.summary;
    expect(() => parseReplay(s, graphs)).toThrow("missing field: $.steps[4].value.summary");
    const c = raw();
    delete c.caps;
    try {
      parseReplay(c, graphs);
      throw new Error("expected a throw");
    } catch (e) {
      expect(e).toBeInstanceOf(ReplayError);
      expect((e as Error).message).toBe("missing field: $.caps");
    }
  });
});
