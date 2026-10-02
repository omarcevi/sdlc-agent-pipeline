export const SUPPORTED_SCHEMA = 1;
export type GraphId = "multi" | "single";
export type OutcomeName =
  | "patch_written"
  | "declined"
  | "failed"
  | "pr_opened"
  | "rejected"
  | "refused";
export type FailureKind = "agent" | "budget" | "infra" | "none";
export interface Caps {
  cost_usd: number;
  tool_calls: number;
  wall_clock_s: number;
}
export interface RunInfo {
  run_id: string;
  task_id: string;
  repo: string;
  category: "bug" | "feature" | "refactor" | "trap";
  difficulty: "easy" | "medium" | "hard";
  tempting: boolean;
  levers: string[];
  system: GraphId;
  graph: GraphId;
  preset: string;
  models: Record<string, string>;
  prompt_version: string;
  mode: "bench";
  recorded_at: string;
  issue: { title: string; body: string };
}
export interface RunOutcome {
  outcome: OutcomeName;
  failure_kind: FailureKind;
  reason: string;
  resolved: boolean;
  cost_usd: number;
  tool_calls: number;
  tokens_in: number;
  tokens_out: number;
  duration_s: number;
  test_attempts: number;
  review_rounds: number;
  audit: string[];
}
interface StepBase {
  i: number;
  t: number;
  node: string;
  visit: number;
  cost_usd: number;
  tool_calls: number;
}
export interface ToolCall {
  id: string;
  tool: string;
  label: string;
  args: Record<string, unknown>;
}
export interface NodeStep extends StepBase {
  kind: "node";
  from: string | null;
  via: string | null;
  message: string | null;
}
export interface ModelCallStep extends StepBase {
  kind: "model_call";
  agent: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  text: string | null;
  calls: ToolCall[];
}
export interface ToolResultStep extends StepBase {
  kind: "tool_result";
  call: string;
  tool: string;
  error: string | null;
  result: Record<string, unknown>;
}
export interface PlanValue {
  actionable: boolean;
  decline_reason: string | null;
  summary: string;
  files_to_inspect: string[];
  steps: string[];
  test_strategy: string;
}
export interface PatchClaim {
  summary: string;
  files_changed: string[];
  tests_added: string[];
  notes: string;
}
export interface SoloClaim {
  declined: boolean;
  decline_reason: string | null;
  summary: string;
  files_changed: string[];
}
export interface DiffValue {
  files: string[];
  insertions: number;
  deletions: number;
  unified_diff: string;
  cut: boolean;
}
export interface TestsValue {
  passed: boolean;
  exit_code: number;
  failed_tests: string[];
  output_tail: string;
  duration_s: number;
}
export interface ReviewComment {
  file: string;
  line: number | null;
  severity: "blocker" | "major" | "minor" | "nit";
  issue: string;
}
export interface ReviewValue {
  verdict: "approve" | "request_changes";
  comments: ReviewComment[];
  must_fix: string[];
}
export interface PlanStep extends StepBase {
  kind: "plan";
  value: PlanValue;
}
export interface ClaimStep extends StepBase {
  kind: "claim";
  agent: "coder" | "solo";
  value: PatchClaim | SoloClaim;
}
export interface DiffStep extends StepBase {
  kind: "diff";
  value: DiffValue;
}
export interface TestsStep extends StepBase {
  kind: "tests";
  value: TestsValue;
}
export interface ReviewStep extends StepBase {
  kind: "review";
  value: ReviewValue;
}
export interface StopStep extends StepBase {
  kind: "stop";
  text: string;
}
export interface OutcomeStep extends StepBase {
  kind: "outcome";
}
/** A step kind this viewer does not know; `name` holds the kind found in the file (F12). */
export interface OtherStep extends StepBase {
  kind: "other";
  name: string;
}
export type Step =
  | NodeStep
  | ModelCallStep
  | ToolResultStep
  | PlanStep
  | ClaimStep
  | DiffStep
  | TestsStep
  | ReviewStep
  | StopStep
  | OutcomeStep
  | OtherStep;
export interface Replay {
  schema: number;
  run: RunInfo;
  caps: Caps;
  outcome: RunOutcome;
  steps: Step[];
}
export interface IndexEntry {
  run_id: string;
  file: string;
  caption: string;
  pair: string | null;
  task_id: string;
  issue_title: string;
  repo: string;
  category: string;
  system: GraphId;
  preset: string;
  outcome: OutcomeName;
  failure_kind: FailureKind;
  resolved: boolean;
  cost_usd: number;
  tool_calls: number;
  duration_s: number;
  recorded_at: string;
  allow?: { path: string; rule: string }[];
}
export interface ReplayIndex {
  schema: number;
  note: string;
  replays: IndexEntry[];
}
export interface GraphNode {
  id: string;
  kind: "start" | "llm" | "function" | "router";
}
export interface GraphEdge {
  from: string;
  to: string;
  route: string | null;
}
export interface GraphDef {
  source: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}
export interface Graphs {
  schema: number;
  graphs: Record<GraphId, GraphDef>;
}
export type NodeState = "idle" | "active" | "done" | "stopped" | "end";
export type FeedItem =
  | { kind: "separator"; step: number; t: number; node: string; visit: number; via: string | null }
  | { kind: "call"; step: number; t: number; agent: string; call: ToolCall }
  | { kind: "result"; step: number; t: number; result: ToolResultStep }
  | { kind: "wait"; step: number; t: number; seconds: number }
  | { kind: "stop"; step: number; t: number; text: string }
  | { kind: "other"; step: number; t: number; label: string };
export interface ReplayState {
  t: number;
  stepIndex: number;
  activeNode: string | null;
  activeVisit: number;
  visits: Record<string, number>;
  nodeStates: Record<string, NodeState>;
  takenEdges: GraphEdge[];
  plans: PlanStep[];
  claims: ClaimStep[];
  diffs: DiffStep[];
  tests: TestsStep[];
  reviews: ReviewStep[];
  costUsd: number;
  toolCalls: number;
  tokensIn: number;
  tokensOut: number;
  feed: FeedItem[];
  stopped: StopStep | null;
  finished: boolean;
}
