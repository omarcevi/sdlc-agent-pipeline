# Week 3A parity: Docker sandboxes

Caveat: every configuration ran the same 3 tasks with 1 repeat each. Within a repeat, one task is 33.3 points of resolve rate (100 / 3). Repeats rerun the same tasks, so they show run-to-run variation and do not make the sample of tasks bigger.

The resolve rate's denominator (the counted column) holds only runs that are neither infra failures nor crashed; those runs are shown in their own columns. Crashed runs are not also counted under infra. Every run is in exactly one of resolved, unresolved, agent, budget, infra or crashed, and those six columns sum to runs. Unresolved is a counted run that was not resolved and has no failure kind: a wrong patch, a wrong decline, or a trap that was patched.

## Results

| system | models | tasks | repeats | runs | counted | resolved | resolve rate (mean, min-max) | audit-clean resolved | unresolved | agent | budget | infra | crashed | $/run | total $ | $/resolved | median time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| multi (flash) | gemini-3.8-flash | 3 | 1 | 3 | 3 | 2 | 67% | 67% | 0 | 0 | 1 | 0 | 0 | 0.399 | 1.196 | 0.598 | 203s |

$/run is the mean cost over all runs of the configuration, total $ the sum, and $/resolved the total divided by resolved runs. Cost includes reruns after infra failures; crashed runs may record less than they spent.

## By category

Cells are resolved / counted, all repeats pooled; infra and crashed runs excluded.

| category | multi (flash) |
|---|---|
| bug | 1 / 2 |
| feature | 1 / 1 |

## By repo

Cells are resolved / counted, all repeats pooled; infra and crashed runs excluded.

| repo | multi (flash) |
|---|---|
| mdlite | 0 / 1 |
| stockroom | 1 / 1 |
| taskcli | 1 / 1 |

## By difficulty

Cells are resolved / counted, all repeats pooled; infra and crashed runs excluded.

| difficulty | multi (flash) |
|---|---|
| easy | 1 / 1 |
| medium | 0 / 1 |
| hard | 1 / 1 |

## Runs that did not resolve

### multi (flash)

- md-001-multi-flash-r1-20261002T055112Z [budget] run exceeded 75 tool calls

## Budget failures by cap

| configuration | reason | count |
|---|---|---|
| multi (flash) | run exceeded 75 tool calls | 1 |

## Model calls that stalled twice

None.

## Flagged patches

None.

## Sources

- 20261002T055112Z-multi-flash.json
