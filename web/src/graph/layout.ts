import type { GraphId } from "../replay/types";

/**
 * Hand-placed node centres (design §7.4), keyed by node id. The main path runs
 * left to right at y = 0, `report_failure` sits below it, and the loop edges
 * (`fail`, `changes`) curve above the main path.
 */
export const LAYOUT: Record<GraphId, Record<string, { x: number; y: number }>> = {
  multi: {
    START: { x: 0, y: 0 },
    fetch_issue: { x: 110, y: 0 },
    provision_sandbox: { x: 290, y: 0 },
    planner: { x: 480, y: 0 },
    route_plan: { x: 650, y: 0 },
    coder: { x: 810, y: 0 },
    collect_diff: { x: 1000, y: 0 },
    run_tests: { x: 1160, y: 0 },
    reviewer: { x: 1340, y: 0 },
    route_review: { x: 1510, y: 0 },
    deliver_patch: { x: 1670, y: 0 },
    report_failure: { x: 1160, y: 230 },
  },
  single: {
    START: { x: 0, y: 0 },
    fetch_issue: { x: 110, y: 0 },
    provision_sandbox: { x: 290, y: 0 },
    solo: { x: 480, y: 0 },
    route_solo: { x: 650, y: 0 },
    collect_diff: { x: 820, y: 0 },
    run_tests: { x: 990, y: 0 },
    deliver_patch: { x: 1160, y: 0 },
    report_failure: { x: 820, y: 230 },
  },
};
