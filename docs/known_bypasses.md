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
| Two vLLM bug reports share an outer worker frame, hardware/topic word, benchmark scaffold or broad execution phase but fail for different reasons | in the bounded 1,001-Issue census, all 10 version proposals pointed at a different incident; the package-or-confirmation gate kept authorised actions at zero | open product-quality gap. Do not add another phrase list; a semantic or structured identity channel needs calibration against both censuses before it can authorise anything |

## Open — recall

| what | what it costs | status |
|---|---|---|
| `para-boot-graph-user-voice` | a rewrite sharing no vocabulary with the incident is missed | long-standing; needs a semantic channel, not a looser gate |
| A report whose identifying evidence is *only* inside a fence | abstains; the same trace indented under a neutral introduction still answers | deliberate. Asserted by `test_a_report_whose_only_evidence_is_fenced_abstains` |
| Feature requests and questions are treated as incident reports | in the 499-report census, `#1648 不支持打断模式吗` - a question about steering - was proposed against an unrelated bug; in the earlier 60-report adjudication four of eight wrong matches were wishes rather than failures | open, and the clearest lead: nothing yet asks whether a report describes a failure at all |

## Open — delivery

| what | what it costs | status |
|---|---|---|
| The `Live evaluation suite` CI step has never executed | `pytest -m live`, `evals/runner.py` and `evals/holdout.py` have only ever run on one machine | open. It syncs 200 discussions while every number is measured against 550, the eval cases name specific upstream artifacts a 200-sync may not contain, and whether `secrets.GITHUB_TOKEN` can read another repository's Discussions is untested. See `docs/status.md` §28 |

## Closed

Every adversarial phrasing reported in rounds 8-12 is closed and carried in the
public set in `tests/test_failclosed_identity.py`, which runs each of them
through the installed CLI and a freshly launched stdio MCP process. They are
regressions now, not blockers.
