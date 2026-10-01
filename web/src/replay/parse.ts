import {
  SUPPORTED_SCHEMA,
  type GraphId,
  type Graphs,
  type Replay,
  type ReplayIndex,
  type Step,
} from "./types";

export class ReplayError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReplayError";
  }
}

// Field specs. A spec is a type tag, "string?" / "number?" (nullable), or a nested spec.
type Spec = string | { [field: string]: Spec } | [Spec];

const missing = (path: string): never => {
  throw new ReplayError(`missing field: ${path}`);
};

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function check(value: unknown, spec: Spec, path: string): void {
  if (Array.isArray(spec)) {
    if (!Array.isArray(value)) return missing(path);
    value.forEach((item, n) => check(item, spec[0], `${path}[${n}]`));
    return;
  }
  if (typeof spec === "object") {
    if (!isObject(value)) return missing(path);
    for (const [key, sub] of Object.entries(spec)) check(value[key], sub, `${path}.${key}`);
    return;
  }
  const nullable = spec.endsWith("?");
  const tag = nullable ? spec.slice(0, -1) : spec;
  if (nullable && value === null) return;
  switch (tag) {
    case "string":
    case "number":
    case "boolean":
      if (typeof value !== tag) missing(path);
      return;
    case "object":
      if (!isObject(value)) missing(path);
      return;
    case "array":
      if (!Array.isArray(value)) missing(path);
      return;
    case "any":
      if (value === undefined) missing(path);
      return;
  }
}

const REPLAY_SPEC: Spec = {
  schema: "number",
  run: {
    run_id: "string",
    task_id: "string",
    repo: "string",
    category: "string",
    difficulty: "string",
    tempting: "boolean",
    levers: ["string"],
    system: "string",
    graph: "string",
    preset: "string",
    models: "object",
    prompt_version: "string",
    mode: "string",
    recorded_at: "string",
    issue: { title: "string", body: "string" },
  },
  caps: { cost_usd: "number", tool_calls: "number", wall_clock_s: "number" },
  outcome: {
    outcome: "string",
    failure_kind: "string",
    reason: "string",
    resolved: "boolean",
    cost_usd: "number",
    tool_calls: "number",
    tokens_in: "number",
    tokens_out: "number",
    duration_s: "number",
    test_attempts: "number",
    review_rounds: "number",
    audit: ["string"],
  },
  steps: "array",
};

const STEP_BASE: Spec = {
  i: "number",
  t: "number",
  node: "string",
  visit: "number",
  cost_usd: "number",
  tool_calls: "number",
  kind: "string",
};

const STEP_FIELDS: Record<string, Spec> = {
  node: { from: "string?", via: "string?", message: "string?" },
  model_call: {
    agent: "string",
    model: "string",
    tokens_in: "number",
    tokens_out: "number",
    text: "string?",
    calls: [{ id: "string", tool: "string", label: "string", args: "object" }],
  },
  tool_result: { call: "string", tool: "string", error: "string?", result: "object" },
  plan: {
    value: {
      actionable: "boolean",
      decline_reason: "string?",
      summary: "string",
      files_to_inspect: ["string"],
      steps: ["string"],
      test_strategy: "string",
    },
  },
  claim: { agent: "string", value: { summary: "string", files_changed: ["string"] } },
  diff: {
    value: {
      files: ["string"],
      insertions: "number",
      deletions: "number",
      unified_diff: "string",
      cut: "boolean",
    },
  },
  tests: {
    value: {
      passed: "boolean",
      exit_code: "number",
      failed_tests: ["string"],
      output_tail: "string",
      duration_s: "number",
    },
  },
  review: {
    value: {
      verdict: "string",
      comments: [{ file: "string", line: "number?", severity: "string", issue: "string" }],
      must_fix: ["string"],
    },
  },
  stop: { text: "string" },
  outcome: {},
};

const GRAPH_IDS: GraphId[] = ["multi", "single"];

export function parseGraphs(json: unknown): Graphs {
  const graphDef: Spec = {
    source: "string",
    nodes: [{ id: "string", kind: "string" }],
    edges: [{ from: "string", to: "string", route: "string?" }],
  };
  check(json, { schema: "number", graphs: { multi: graphDef, single: graphDef } }, "$");
  const graphs = json as Graphs;
  if (graphs.schema !== SUPPORTED_SCHEMA) throw new ReplayError("unsupported schema");
  return graphs;
}

export function parseIndex(json: unknown): ReplayIndex {
  check(
    json,
    {
      schema: "number",
      note: "string",
      replays: [
        {
          run_id: "string",
          file: "string",
          caption: "string",
          pair: "string?",
          task_id: "string",
          issue_title: "string",
          repo: "string",
          category: "string",
          system: "string",
          preset: "string",
          outcome: "string",
          failure_kind: "string",
          resolved: "boolean",
          cost_usd: "number",
          tool_calls: "number",
          duration_s: "number",
          recorded_at: "string",
        },
      ],
    },
    "$",
  );
  const index = json as ReplayIndex;
  if (index.schema !== SUPPORTED_SCHEMA) throw new ReplayError("unsupported schema");
  return index;
}

export function parseReplay(json: unknown, graphs: Graphs): Replay {
  // The schema is checked before anything else, so a newer file reports it and not a missing field.
  if (isObject(json) && typeof json.schema === "number" && json.schema !== SUPPORTED_SCHEMA) {
    throw new ReplayError("unsupported schema");
  }
  check(json, REPLAY_SPEC, "$");
  const replay = json as unknown as Replay;
  if (replay.schema !== SUPPORTED_SCHEMA) throw new ReplayError("unsupported schema");
  const graph = graphs.graphs[replay.run.graph];
  if (!graph) return missing("$.run.graph");
  const nodeIds = new Set(graph.nodes.map((n) => n.id));

  let lastT = -Infinity;
  const raw = replay.steps as unknown[];
  const steps: Step[] = raw.map((item, n) => {
    const path = `$.steps[${n}]`;
    check(item, STEP_BASE, path);
    const step = item as Record<string, unknown>;
    const fields = STEP_FIELDS[step.kind as string];
    if (fields) check(step, fields, path);
    if (step.i !== n || (step.t as number) < lastT) throw new ReplayError("steps out of order");
    lastT = step.t as number;
    if (!nodeIds.has(step.node as string)) throw new ReplayError("node not in graph");
    if (!fields) return { ...step, kind: "other", name: step.kind } as unknown as Step;
    return step as unknown as Step;
  });
  return { ...replay, steps };
}
