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

Every run records its provenance - seed, sample size, eligible population,
corpus size, `data_as_of`, and the numbers of the reports sampled. Two runs are
comparable only when those match: CI syncs 200 discussions and this corpus holds
550, so CI's numbers are a regression signal for CI and cannot be read against
the ones below.

### The hard gate

**No wrong action, ever, without authorisation.** An action that changes what
someone runs requires either a stated package that the incident names as what
failed, or an explicit confirmation bound to the digest of the reading being
shown. This is asserted at zero in the committed suite and now directly counted
by both real-report censuses; `evals/holdout.py` exits non-zero if a report with
no package assertion and no confirmation digest crosses the gate.

Everything below this line is *proposal* quality. A proposal is what the reader
sees and can reject; it is not an action.

### Measured quality

Machine-counted, from `evals/holdout.py`. Note what the machine may and may not
call things: it counts proposals pointing at **another report**, and some of
those are genuine duplicates where proposing is correct. Only a person can
separate them, so nothing machine-counted is called "false".

The first baseline is a **census of all 499 eligible DeepSeek reports**, so there
is no sampling error left inside that stored corpus. The second is a census of
the bounded 1,001-Issue vLLM corpus. vLLM has much larger upstream history, so
that census removes sampling error only inside the stored latest-1,000-plus-seed
slice; it does not remove coverage bias. Seeded sub-samples stay as fast
regressions and are not the basis of either claim.

| metric | census (499) | fixture (60) | threshold |
|---|---|---|---|
| `other_report_proposal_rate_overall` | 0.0100 (5/499) | 0.000 (0/60) | **≤ 0.05** |
| `other_report_proposal_rate_given_opportunity` | 0.1562 (5/32) | 0.000 (0/3) | tracked, none |
| `proposal_opportunity_count` | 32 | 3 | — |
| `other_report_match_rate` | 0.2044 (102/499) | 0.267 | tracked, none |
| positive control | reaches a proposal | reaches a proposal | must, or the run fails |

Adjudicated in `evals/holdout_judgement.md` - the only place a number may be
called false:

| metric | census (499) | upper bound |
|---|---|---|
| `adjudicated_false_proposal_rate` overall | **0.0080** (4/499) | 0.0100 (5/499) |
| `adjudicated_false_proposal_rate` given opportunity | **0.125** (4/32) | 0.1562 (5/32) |
| 95% interval (Wilson) on 4/32 | **0.050 - 0.281** | |

The structurally different vLLM result is adjudicated separately in
`evals/vllm_holdout_judgement.md`:

| metric | bounded-corpus census (1,001 Issues) |
|---|---:|
| reports with a template-bound current version | 230/1,001 |
| `other_report_match_rate` | 0.0959 (96/1,001) |
| `proposal_opportunity_count` | 10 |
| `other_report_proposal_rate_overall` | 0.0100 (10/1,001) |
| `other_report_proposal_rate_given_opportunity` | 1.000 (10/10) |
| adjudicated false-proposal rate overall | **0.0100** (10/1,001) |
| adjudicated false-proposal rate given opportunity | **1.000** (10/10) |
| Wilson 95% interval on 10/10 | **0.722 - 1.000** |
| authorised version actions | **0/1,001** |

**Two denominators, on purpose.** The overall rate is the product rate: what a
user of this corpus meets. The conditional rate is the quality rate: of the
times a version action was reachable, how often it pointed elsewhere.
`proposal_opportunity_count` is the denominator of the conditional rate only -
never of the overall one.

**No threshold has been chosen for the conditional rate.** The second population
now exists and makes the tradeoff visible: the Issue/PR evidence architecture
transfers, but proposal precision does not. DeepSeek has 32 opportunities;
vLLM has only 10 and is a recent bounded slice, with a 0.722-1.000 interval.
Choosing whether that blocks release, merely labels proposals low-confidence,
or justifies funding a calibrated semantic/structured identity channel is the
project owner's product decision. The number is not picked after the fact to be
satisfied today.

**The fixture is a regression, not an estimate.** Its 0/60 had three
opportunities.

| other gates | current | threshold |
|---|---|---|
| developer suite `evals/runner.py` | 70/71 | no regression |
| unsafe action on the committed adversarial set | 0 | no regression |

## Adversarial findings after this point

They remain valuable and stay in the suite as regressions. What changes is their
standing: **a new phrasing that gets past the reader is a defect, not a
blocker**, and it is recorded in `docs/known_bypasses.md` with what it costs. A
blocker is a path to a wrong *action*, which now requires the authorisation gate
to fail as well.
