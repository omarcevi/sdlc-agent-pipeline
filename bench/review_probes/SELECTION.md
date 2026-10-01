# Reviewer probe set: selection record

Week 2C, Task 10, step 4 (decision 8B). This file records how the twelve probes in this
directory were chosen. The 2C live log does not exist yet, so this file is the selection
record; Task 7 copies a pointer to it into the live log.

The rule was fixed before any probe run, and it was applied mechanically. No probe has
been run against the reviewer, and no probe may change after a reviewer result is seen.

## Inputs

- Week 2B single-agent results: `results/20261001T084838Z-single-flash.json` (45 rows,
  all dev split, all `system: single`).
- Patches: `runs/<run-id>/patch.diff` from the same comparison.
- Base + plant of each task, from `app.task_store.materialize`.
- Multi-agent patches are not used: the reviewer has already judged them.

Target: 6 bad and 6 good probes, over at least 4 tasks, covering both `mdlite` and
`stockroom`. Probe ids are `rp-01` upwards in the order the probes were created.

## Candidates

`uv run python -m bench.probes candidates results/20261001T084838Z-single-flash.json --runs-dir runs`
printed 30 rows, every one of them `resolved`:

```
md-001-single-flash-r1-20261001T084838Z md-001 resolved
md-001-single-flash-r2-20261001T084838Z md-001 resolved
md-001-single-flash-r3-20261001T084838Z md-001 resolved
md-002-single-flash-r1-20261001T084838Z md-002 resolved
md-002-single-flash-r2-20261001T084838Z md-002 resolved
md-002-single-flash-r3-20261001T084838Z md-002 resolved
md-003-single-flash-r1-20261001T084838Z md-003 resolved
md-003-single-flash-r2-20261001T084838Z md-003 resolved
md-003-single-flash-r3-20261001T084838Z md-003 resolved
sr-001-single-flash-r1-20261001T084838Z sr-001 resolved
sr-001-single-flash-r2-20261001T084838Z sr-001 resolved
sr-001-single-flash-r3-20261001T084838Z sr-001 resolved
sr-002-single-flash-r1-20261001T084838Z sr-002 resolved
sr-002-single-flash-r2-20261001T084838Z sr-002 resolved
sr-002-single-flash-r3-20261001T084838Z sr-002 resolved
sr-005-single-flash-r1-20261001T084838Z sr-005 resolved
sr-005-single-flash-r2-20261001T084838Z sr-005 resolved
sr-005-single-flash-r3-20261001T084838Z sr-005 resolved
tc-001-single-flash-r1-20261001T084838Z tc-001 resolved
tc-001-single-flash-r2-20261001T084838Z tc-001 resolved
tc-001-single-flash-r3-20261001T084838Z tc-001 resolved
tc-002-single-flash-r1-20261001T084838Z tc-002 resolved
tc-002-single-flash-r2-20261001T084838Z tc-002 resolved
tc-002-single-flash-r3-20261001T084838Z tc-002 resolved
tc-003-single-flash-r1-20261001T084838Z tc-003 resolved
tc-003-single-flash-r2-20261001T084838Z tc-003 resolved
tc-003-single-flash-r3-20261001T084838Z tc-003 resolved
tc-004-single-flash-r1-20261001T084838Z tc-004 resolved
tc-004-single-flash-r2-20261001T084838Z tc-004 resolved
tc-004-single-flash-r3-20261001T084838Z tc-004 resolved
```

The other 15 rows wrote no patch, so they are not candidates: `md-004`, `sr-003` and
`sr-004` failed in all three repeats, and the trap tasks `md-005` and `tc-005` were
declined in all three.

## Bad probes

**Step 1: the two shortcut overlays.**

| probe | command |
|---|---|
| rp-01 | `uv run python -m bench.probes from-overlay --task md-002 --id rp-01` |
| rp-02 | `uv run python -m bench.probes from-overlay --task sr-002 --id rp-02` |

In fix round 1 (below) each overlay patch was extended with one new test file. The overlay
part of each `patch.diff` is still byte for byte what `from-overlay` wrote, and both keep
`source: shortcut`.

**Step 2: unresolved single-agent candidates.** The list is empty: the candidates list has
no `unresolved` row, because every single-agent run that wrote a patch resolved its task.
Nothing was taken, and 4 bad probes were still needed.

**Step 3: hand-written patches, one per task not yet covered.** The tasks were taken in
task-id order from the non-trap dev tasks of `mdlite` and `stockroom` not covered by
steps 1 and 2: `md-001`, `md-003`, `md-004`, `sr-001`, `sr-003`, `sr-004`, `sr-005`
(`md-002` and `sr-002` are covered by step 1; `md-005` is a trap task).

| order | task | result |
|---|---|---|
| 1 | md-001 | rp-03 |
| 2 | md-003 | rp-04 |
| 3 | md-004 | rp-05 |
| 4 | sr-001 | skipped (below) |
| 5 | sr-003 | rp-06 |

Six bad probes were reached at `sr-003`, so `sr-004` and `sr-005` were not needed.

Each hand-written patch was written against base + plant of its task (materialised with
`app.task_store.materialize`) as a `git diff` of the changed files, in the form the
pipeline produces. Since fix round 1 each one also adds a new test file (below). Each
`probe.yaml` has `kind: bad`, `source: hand-written` and a one-line `note` on what the
patch gets wrong. Why a hurried developer would write each one, and what it misses:

- **rp-03 (md-001).** The issue shows the symptom in autolinks, so a hurried developer
  widens the scheme pattern right in the autolink parser and shows such addresses as text,
  which fixes every example in the issue; it misses that the cause is the shared
  `url_scheme` in `escape.py` (the one place for scheme rules, per R16 and that module's
  docstring), so a Markdown link or image whose url uses a scheme with `+`, `.` or `-` is
  still treated as scheme-less and still gets an `href` or `src`.
- **rp-04 (md-003).** Checking that `max_level` is a whole number from 1 to 6 and then
  skipping deeper headings at the top of the loop is the obvious way to add `max_level`;
  it misses that duplicate numbering runs over every heading in the document (R19), so
  once a heading below the cut-off shares its text with a listed one, the listed slug no
  longer matches the id `to_html` writes, although the issue says the slugs are used as
  link targets.
- **rp-05 (md-004).** Parsing a trailing `{#id}` and using it in place of the generated
  slug, in both the renderer and `toc`, gets the issue's example and its near misses right;
  it misses the issue's rule that headings without an explicit id must not reuse an id
  already taken earlier in the document, explicit ids included, because the explicit id is
  never registered with the slugger.
- **rp-06 (sr-003).** Reading the whole file first, reusing the products import's
  field-count check, accepting only ASCII digits as a count, and letting the existing
  catalog and inventory errors reject unknown SKUs and counts below the reserved units
  covers most of the issue's bad-row cases with little new code; the one case that needs
  its own check, a SKU counted again on a later line, is not checked, so the later count
  silently wins instead of being reported.

**Skip: sr-001.** No plausible narrow patch passes the visible tests and fails the hidden
ones. The planted defect is one wrong field in the line that computes `stock_value`, and
the issue points straight at it. Every narrow change that produces the issue's expected
row (valuing the units on hand, or available plus reserved, which is the same number)
values every other case correctly too. The only variants that would fail, such as also
changing the `available` column or counting reserved units twice, contradict the expected
row printed in the issue, so they would be deliberately silly, not hurried.

## Good probes

Rule: one `resolved` single-agent candidate per task, in run-id order, until there are 6.
Each `patch.diff` is a verbatim copy of the run's `runs/<run-id>/patch.diff` (checked
byte for byte with `cmp`).

| probe | task | source run |
|---|---|---|
| rp-07 | md-001 | md-001-single-flash-r1-20261001T084838Z |
| rp-08 | md-002 | md-002-single-flash-r1-20261001T084838Z |
| rp-09 | md-003 | md-003-single-flash-r1-20261001T084838Z |
| rp-10 | sr-001 | sr-001-single-flash-r1-20261001T084838Z |
| rp-11 | sr-002 | sr-002-single-flash-r1-20261001T084838Z |
| rp-12 | sr-005 | sr-005-single-flash-r1-20261001T084838Z |

Six were reached before the first `tc-*` candidate, so no task's `solution/` overlay was
needed and every other candidate (the `r2` and `r3` repeats, and `tc-001` to `tc-004`) is
unused. The brief writes the source of these probes as `source: run <run-id>`; `ProbeSpec`
accepts only `shortcut`, `bench-run` or `hand-written`, so it is recorded as
`source: bench-run` with the run id in `source_run`.

## The set

| probe | task | repo | kind | source |
|---|---|---|---|---|
| rp-01 | md-002 | mdlite | bad | shortcut (`from-overlay`) |
| rp-02 | sr-002 | stockroom | bad | shortcut (`from-overlay`) |
| rp-03 | md-001 | mdlite | bad | hand-written |
| rp-04 | md-003 | mdlite | bad | hand-written |
| rp-05 | md-004 | mdlite | bad | hand-written |
| rp-06 | sr-003 | stockroom | bad | hand-written |
| rp-07 | md-001 | mdlite | good | bench-run |
| rp-08 | md-002 | mdlite | good | bench-run |
| rp-09 | md-003 | mdlite | good | bench-run |
| rp-10 | sr-001 | stockroom | good | bench-run |
| rp-11 | sr-002 | stockroom | good | bench-run |
| rp-12 | sr-005 | stockroom | good | bench-run |

Eight tasks (`md-001` to `md-004`, `sr-001`, `sr-002`, `sr-003`, `sr-005`), both repos.
Four tasks (`md-001`, `md-002`, `md-003`, `sr-002`) have both a bad and a good probe.

## Fix round 1

This round followed the review of the first version of the set. No reviewer result had
been seen for any probe, so changing probes was still within the rule.

**Why.** The review's Important finding was that new test files told good from bad
perfectly. Every good probe added a `tests/test_*.py` file and no bad probe did. Yet every
patch the pipeline's coder writes adds tests, and in a probe run the reviewer sees the
planner's plan, test strategy included, next to the diff. A reviewer that rejected every
patch without the planned tests would have scored a catch on every bad probe without
finding a single flaw. Ruling: every bad probe gets a new test file, the way a hurried
developer would write it.

**What changed.** Each bad probe now adds one new test file. It holds pytest functions
over the issue's own examples, through the public API the issue uses. The file is new:
no existing test file is edited. Its tests pass with the flawed change and do not
exercise the missed case. It contains no label word and no comment hinting at the flaw,
and nothing in it is copied from `hidden_tests/`. Two probes also lost a second,
legitimate reason to reject (the review's Minor finding 1), so each bad probe now carries
one flaw only.

| probe | task | new test file | tests | other change |
|---|---|---|---|---|
| rp-01 | md-002 | `tests/test_image_alt_text.py` | 4 | appended to the unchanged overlay patch; the note says so |
| rp-02 | sr-002 | `tests/test_import_tier_ladder.py` | 3 | appended to the unchanged overlay patch; the note says so |
| rp-03 | md-001 | `tests/test_autolink_brackets.py` | 6 | none |
| rp-04 | md-003 | `tests/test_toc_sidebar_levels.py` | 9 | `toc` now raises `ValueError` unless `max_level` is a whole number from 1 to 6, as the good probe rp-09 does |
| rp-05 | md-004 | `tests/test_explicit_heading_ids.py` | 7 | none |
| rp-06 | sr-003 | `tests/test_import_stocktake.py` | 5 | a count must match `[0-9]+` (ASCII digits only; `str.isdecimal()` accepted other scripts' digits), and rows go through the products import's field-count check, now shared as `csvio.row_fields`, so a row with extra or missing fields is reported |

Test counts are pytest items, parametrised cases included. Each new file passes at
base + plant + patch, and so does the whole visible suite. Each bad probe still fails the
hidden tests on its intended flaw only, as in the first version. The good probes
(rp-07..rp-12) did not change.

## Reading rule for Task 12

Fixed before any probe run:

- A bad-probe run counts as a catch by its verdict alone, as `bench.review_probe`
  computes it.
- The Task 12 report also states, for each bad-probe run, whether any `must_fix` item
  names the probe's flaw (compare it with the probe's `note`), or whether the items only
  ask for tests or for other changes.
- rp-03 is reported separately. Its flaw can only be found through the README rule (R16)
  and the `escape.py` docstring, and the patch leaves behaviour that predates it, so an
  approve there is partly defensible. A miss on rp-03 is not read like a miss on rp-05 or
  rp-06, which omit a requirement the issue lists as a bullet.
- The report also shows the catch rate and the false-alarm rate on the four paired tasks
  alone (`md-001`, `md-002`, `md-003`, `sr-002`: 4 bad and 4 good probes). Per-repo
  numbers stay out of the conclusions (only 2 bad probes are stockroom).

## Validation

`uv run python -m bench.probes validate` printed `ok` for all twelve, both in the first
version and after fix round 1. For each probe:

- the patch applies to base + plant;
- it touches no protected test file and nothing under `.github/`;
- no added line holds a label word;
- the visible tests pass, the new test files included.

The six bad probes fail the hidden tests, and the six good probes pass them.

The `note` field is for people only. In the probe graph, `app.review_probe.load_probe`
reads `probe.yaml` for the task id alone; `bench.review_probe` reads the kind for the
result row; nothing puts the note or the kind in state, messages or the sandbox
(`test_agents_never_see_the_probe_kind` pins this).
