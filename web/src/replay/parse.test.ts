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

  it("maps prototype-key kinds to other and never throws a TypeError", () => {
    for (const kind of ["__proto__", "constructor", "toString", "hasOwnProperty"]) {
      const r = raw();
      r.steps[3] = { ...r.steps[3], kind };
      const step = parseReplay(r, graphs).steps[3];
      expect(step.kind).toBe("other");
    }
    for (const graph of ["__proto__", "constructor", "toString", "nope"]) {
      const r = raw();
      r.run.graph = graph;
      expect(() => parseReplay(r, graphs)).toThrow(ReplayError);
    }
  });

  it("rejects a value outside an enum", () => {
    const verdict = raw();
    verdict.steps[16].value.verdict = "lgtm";
    expect(() => parseReplay(verdict, graphs)).toThrow("missing field: $.steps[16].value.verdict");
    const category = raw();
    category.run.category = "x";
    expect(() => parseReplay(category, graphs)).toThrow("missing field: $.run.category");
    const outcome = raw();
    outcome.outcome.failure_kind = "oops";
    expect(() => parseReplay(outcome, graphs)).toThrow("missing field: $.outcome.failure_kind");
  });

  it("validates a claim as exactly one of the two shapes", () => {
    const partial = raw();
    delete partial.steps[10].value.notes;
    expect(() => parseReplay(partial, graphs)).toThrow("missing field: $.steps[10].value.notes");
    const wrongAgent = raw();
    wrongAgent.steps[10].agent = "evil";
    expect(() => parseReplay(wrongAgent, graphs)).toThrow("missing field: $.steps[10].agent");
    const solo = raw("single");
    delete solo.steps[5].value.declined;
    expect(() => parseReplay(solo, graphs)).toThrow("missing field: $.steps[5].value.declined");
  });

  it("rejects non-object steps and steps that are not an array", () => {
    const notObj = raw();
    notObj.steps[2] = 5;
    expect(() => parseReplay(notObj, graphs)).toThrow("missing field: $.steps[2]");
    const nul = raw();
    nul.steps[2] = null;
    expect(() => parseReplay(nul, graphs)).toThrow(ReplayError);
    const notArr = raw();
    notArr.steps = { length: 0 };
    expect(() => parseReplay(notArr, graphs)).toThrow("missing field: $.steps");
  });
});

describe("parseIndex", () => {
  const idx = () => JSON.parse(JSON.stringify(makeIndex())) as Record<string, any>;

  it("rejects file names that are not plain file names", () => {
    for (const file of ["../x.json", "a/b.json", "https://x.test/a.json", "..", ".hidden", "", "a\\b.json"]) {
      const i = idx();
      i.replays[0].file = file;
      expect(() => parseIndex(i), file).toThrow(ReplayError);
    }
  });

  it("rejects duplicate run ids, bad enums and a bad allow list", () => {
    const dup = idx();
    dup.replays[1].run_id = dup.replays[0].run_id;
    expect(() => parseIndex(dup)).toThrow(ReplayError);
    const en = idx();
    en.replays[0].outcome = "weird";
    expect(() => parseIndex(en)).toThrow("missing field: $.replays[0].outcome");
    const allow = idx();
    allow.replays[0].allow = [{ path: "$" }];
    expect(() => parseIndex(allow)).toThrow("missing field: $.replays[0].allow[0].rule");
    const ok = idx();
    ok.replays[0].allow = [{ path: "$.x", rule: "email" }];
    expect(() => parseIndex(ok)).not.toThrow();
  });
});
