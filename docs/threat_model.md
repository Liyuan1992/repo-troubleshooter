# Threat model, and what counts as done

Written 2026-09-04, after four review rounds in which every finding was real and
the supply of findings did not diminish.

## Who this tool is for

**Cooperative users describing their own problem.** Someone pastes an error,
maybe a stack trace, maybe a sentence about what they were doing, and wants to
know whether their version already contains a fix.

They may write badly, in any language, with quoted material, retractions, half
sentences and pasted logs. They are not trying to defeat the reader.

## What is in scope

* Misreadings of ordinary prose - a retraction the reader missed, a quoted
  ticket taken as the reporter's own, a role assigned to the wrong package.
* Stale, partial or absent evidence.
* Version and environment reasoning: containment is not proof of a fix, merged
  is not released, first reported in X is not introduced in X.
* Anything the tool asserts about itself being false - a documented claim that
  does not hold, a number that does not match the database.

## What is out of scope

**Adversarially constructed input.** A person who writes sentences specifically
to defeat the parser will succeed, given enough attempts. The reader is shallow
parsing over regular expressions; the writer is a person who knows English.
That contest cannot be won by adding rules, and treating it as the acceptance
criterion means the tool is never finished.

This is a scope decision, not a claim that such inputs are harmless. What makes
it defensible is the architecture that replaced the parsing argument:

* free text **finds** candidates and may **refuse** them; it does not authorise
  changing what anyone runs (§22 of `status.md`);
* the reading is **echoed back** and the user's agreement is what authorises a
  recommendation, so a misreading is visible before it can act (§23).

A misreading now costs a wrong *proposal* - which the reader sees and rejects -
instead of a wrong instruction. That is what moves adversarial phrasing from a
safety problem to a quality problem.

## What counts as done

Measured on **`evals/holdout.py`**: real reports sampled from the synced corpus,
each asked about with itself removed from the evidence.

| gate | current | threshold |
|---|---|---|
| `false_proposal_rate` - sampled reports that would propose acting on another incident | 0.00 (0/60) | **≤ 0.05** |
| `proposal_opportunity_count` - matches where a released fix existed to point at | 3 | the denominator; the rate above means nothing at 0 |
| positive control - a known released incident, asked the same way | reaches a proposal | must, or the run fails |
| `other_report_match_rate` - sampled reports matched to another report | 0.267 (16/60) | tracked, no threshold |
| `adjudicated_false_identity_rate` - the same, after reading each pair | 0.133 (8/60), upper bound 0.200 | tracked, no threshold |
| developer suite `evals/runner.py` | 70/71 | no regression |
| unsafe action on the committed adversarial set | 0 | no regression |

The match rate is machine-counted and includes genuine duplicates, where
matching is correct - which is why it is not called a false-identity rate. Only
a person can separate those, and the reading of every pair is recorded in
`evals/holdout_judgement.md` so the adjudicated number can be argued with.

Neither has a threshold: one sample of one repository is not a basis for setting
one, and a number chosen to be satisfied by today's measurement would be
decoration.

## Adversarial findings after this point

They remain valuable and stay in the suite as regressions. What changes is their
standing: **a new phrasing that gets past the reader is a defect, not a
blocker**, and it is recorded in `docs/known_bypasses.md` with what it costs. A
blocker is a path to a wrong *action*, which now requires the authorisation gate
to fail as well.
