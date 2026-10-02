# ADR 2: Deterministic routing on real diffs and test exit codes

Date: 2026-10-02. Status: accepted.
Sources: [`AGENTS.md`](../../AGENTS.md) hard rule 4; [parent spec](../superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md) §5.1, §6.1 and §19 (2026-09-29 item 3; 2026-09-30, Week 1 review item 2 and Week 2A Task 1 item 1); [`app/nodes/verify.py`](../../app/nodes/verify.py), [`app/nodes/routing.py`](../../app/nodes/routing.py), [`app/nodes/gate.py`](../../app/nodes/gate.py); [Week 2B review audit](../results/2026-10-01-2b-review-audit.md), [Week 2B comparison](../results/2026-10-01-2b-comparison.md).

## Context

A coding agent ends its turn with a claim: "fixed, all tests pass". The claim can be wrong in ways the model does not notice or does not report: the tests never ran, they failed and the summary says otherwise, nothing was changed, or a test file was edited instead of the code. A graph that routes on that text sends a wrong claim straight to review or to a pull request.

## Decision

A claim about the repository never routes a run. Function nodes measure the repository in the sandbox and route on what they measure.

- **The coder's summary is not read for routing.** Its `PatchResult` (summary, files changed, tests added) is informational. The edge after the coder always leads to `collect_diff`.
- **`collect_diff`** runs `git add -A` and `git diff --cached --no-renames --binary` against the baseline commit that `provision_sandbox` recorded, with the pipeline's git directory outside the worktree, so a commit made by the coder cannot empty the diff. Files and line counts come from `--numstat`.
- **`run_tests`** routes on the exit code: `pass` only when `python -m pytest` exits 0 and did not time out. An empty diff, or one that touches an existing test file or `.github/`, fails the attempt before any test runs; the node restores those paths from the baseline and tells the coder why.
- **The reviewer gets the evidence**, not an account of it: the diff `collect_diff` took (cut to 20,000 characters, head and tail kept) and the report `run_tests` produced.
- **Live delivery checks the patch, not the approval text.** `route_approval` sends a run to `open_pr` only when the decision's `approved` is a strict `true` and its hash equals the SHA-256 of the patch in session state; `open_pr` checks the hash again before publishing.

Two routes still follow a model's answer, because they are judgments rather than facts: the planner's `actionable` flag and the reviewer's verdict. The routers read them from a validated schema, the loop counters bound how often a verdict can send work back, and a missing or malformed answer is an agent failure, never a pass.

## Consequences

- A wrong claim from the coder changes nothing: the tests run anyway, and a failing attempt counts against the three returns.
- New tests go in new files. Because existing test files are read-only, the coder cannot fix a test that is itself wrong.
- The failure routes are covered by unit tests with a fake model and fake sandboxes (empty diff, protected file, three failed returns, review rounds exhausted). Real runs have not needed them yet: in the Week 2A and 2B comparisons every patch that reached the tests passed first time, and no run entered the test-fix or the review loop.
- The review audit paired each reviewer verdict in the Week 2B multi-agent runs with the diff it reviewed and scored that diff with the hidden tests: 28 diffs, all good, all approved. On these tasks every diff that passed the visible tests also passed the hidden ones, so the reviewer has not yet been shown a bad diff in a real run, and its value is unmeasured. Twelve reviewer probes (six bad patches, six good) exist to measure it; they validate on both sandbox backends, and no model run of them is reported yet.
- Passing visible tests is necessary, not sufficient. The benchmark scores with hidden tests the pipeline never sees, and its tempting tasks target exactly this gap ([ADR 5](0005-hidden-test-benchmark.md)).
