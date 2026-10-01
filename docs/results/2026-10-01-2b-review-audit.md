# Week 2B: review audit of the multi-agent runs

Each reviewer verdict in the multi-agent runs, paired with the diff it reviewed. A diff is good if it passes the hidden tests, bad if not.

| task | bad caught | bad approved (missed) | good sent back (false alarm) | good approved | rounds reviewed | distinct diffs |
|---|---|---|---|---|---|---|
| md-001 | 0 | 0 | 0 | 3 | 3 | 3 |
| md-002 | 0 | 0 | 0 | 3 | 3 | 3 |
| md-003 | 0 | 0 | 0 | 3 | 3 | 3 |
| md-004 | 0 | 0 | 0 | 0 | 0 | 0 |
| md-005 | 0 | 0 | 0 | 0 | 0 | 0 |
| sr-001 | 0 | 0 | 0 | 3 | 3 | 3 |
| sr-002 | 0 | 0 | 0 | 3 | 3 | 3 |
| sr-003 | 0 | 0 | 0 | 0 | 0 | 0 |
| sr-004 | 0 | 0 | 0 | 0 | 0 | 0 |
| sr-005 | 0 | 0 | 0 | 3 | 3 | 3 |
| tc-001 | 0 | 0 | 0 | 2 | 2 | 2 |
| tc-002 | 0 | 0 | 0 | 2 | 2 | 2 |
| tc-003 | 0 | 0 | 0 | 3 | 3 | 3 |
| tc-004 | 0 | 0 | 0 | 3 | 3 | 3 |
| tc-005 | 0 | 0 | 0 | 0 | 0 | 0 |
| **total** | 0 | 0 | 0 | 28 | 28 | 28 |

- Good diffs sent back: 0 of 28.
- Rows skipped: 0 not multi-agent, 0 not in the dev split (not opened), 0 without an event log.

## Caveats

- A catch counts the verdict, not its reasons: the reviewer may have sent a bad diff back for the wrong reason.
- Only diffs that passed the visible tests reach the reviewer. "Bad" means passed the visible tests and failed the hidden ones; "good" means passed both.
