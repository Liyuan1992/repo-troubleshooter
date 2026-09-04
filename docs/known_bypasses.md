# Known bypasses and recall gaps

Kept open on purpose. A phrasing that gets past the reader is recorded here with
what it costs, rather than closed by another rule or quietly dropped from the
suite.

Under the threat model (`threat_model.md`), a misread phrasing is a **defect**;
a path to a wrong *action* is a **blocker**, and that now needs the
authorisation gate to fail as well.

## Open — reading

| what | what it costs | status |
|---|---|---|
| Provenance marked in words not on the label list, in front of an indented block (`Seen in an unrelated 2023 thread:`) | the block reads as the reporter's own, so its paths and symbols can carry identity | open. Fences and `>` need no words; indentation does, and the list is small on purpose |
| A subject and its predicate separated by a clause of their own, beyond what shallow parsing follows | a claim read as belonging to the wrong subject, or not read | open, inherent to the approach |
| `unread_claims` in the echoed reading is capped at six entries | a very long report can be confirmed without every unread sentence being visible | open |

## Open — recall

| what | what it costs | status |
|---|---|---|
| `para-boot-graph-user-voice` | a rewrite sharing no vocabulary with the incident is missed | long-standing; needs a semantic channel, not a looser gate |
| A report whose identifying evidence is *only* inside a fence | abstains; the same trace indented under a neutral introduction still answers | deliberate. Asserted by `test_a_report_whose_only_evidence_is_fenced_abstains` |
| Feature requests and questions are treated as incident reports | five of the eight wrong matches in the holdout are wishes, not failures, matched to bug reports | open, and the clearest lead: nothing yet asks whether a report describes a failure at all |

## Closed

Every adversarial phrasing reported in rounds 8-12 is closed and carried in the
public set in `tests/test_failclosed_identity.py`, which runs each of them
through the installed CLI and a freshly launched stdio MCP process. They are
regressions now, not blockers.
