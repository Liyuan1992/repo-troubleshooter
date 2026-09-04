# Holdout judgement

`evals/holdout.py` asks about real reports with the report itself removed from
the evidence. The machine counts matches and proposals; **whether two reports are
the same incident is a reading**, so nothing the machine reports is called an
error. The adjudication is here, by hand, and can be argued with — one verdict
below has already been overturned that way.

Corpus: `deepseek-ai/deepseek-harness`, 550 discussions, **499 eligible**
(≥200 characters of body), 16,218 signature rows, extractor 16.
Numbers from a different corpus are not comparable: CI syncs 200 discussions and
will produce different ones from the same code.

## The baseline is a census, not a sample

All 499 eligible reports, so **there is no sampling error left within this
repository**. Seeded sub-samples remain useful as fast regressions; they are no
longer the basis for any claim.

| run | reports | matched another | opportunities | proposals | overall | given opportunity |
|---|---|---|---|---|---|---|
| **census (all 499)** | 499 | 102 | 32 | 5 | **0.0100** | **0.1562** |
| fixture, seed 20260904 | 60 | 16 | 3 | 0 | 0.000 | 0.000 |
| wide, seed 20260904 | 300 | 55 | 17 | 2 | 0.0067 | 0.118 |
| seed 11 / 22 / 33 | 100 each | 14 / 24 / 23 | 4 / 10 / 7 | 1 / 3 / 2 | 0.01 / 0.03 / 0.02 | 0.25 / 0.30 / 0.286 |
| seed 44 (independent) | 100 | — | 8 | 2 | 0.02 | 0.25 |

The 60-report fixture's 0/60 had three opportunities. It is a regression
fixture, not an estimate, and the census is what any claim rests on.

## Every proposal in the census, read

| report | matched to | verdict |
|---|---|---|
| #4954 Python SDK cannot resume a persisted session (id collision) | #4066 Plugin-registered commands execute but render nothing | **wrong** |
| #1648 不支持打断模式吗 | #1507 --host 0.0.0.0 长任务后历史加载失败 | **wrong** — and #1648 is a question about steering, not a failure report |
| #4967 Programmatic agent creation silently no-ops ({{model}} unset) | #4666 Spawn-backed subagents drop reasoningEffort | **wrong** — same area, different root cause |
| #4563 SubAgent 打开历史经常 timeout（listChildren 每次全库扫盘） | #4167 删除/修改 Agent Preset 后会话在宿主重启后无法恢复 | **wrong** — both touch sessions, different causes |
| #5222 思考提示 "Error: unknown tool"（build 报 `thinking.budget` missing） | #5144 一更新 build 整个环境坏掉（`isJsonValue` is not exported） | borderline — both a build breaking after an update, different missing symbols |

### #46 was judged wrong before, and that was my error

I first called #46 → #1916 the **same** incident because both bodies contain
`failed to apply loader entry`. Reading further:

* #46: `failed to apply loader entry 9dafb658 (@deepseek-ai/cordis-plugin-hmr):
  --expose-internals is required for HMR service`
* #1916: `failed to apply loader entry webserver (@deepseek-ai/dsh-host-webserver):
  invalid config: $.host expected "127.0.0.1" | "0.0.0.0" but got "100.90.80.70"`

Two different failures sharing a generic outer wrapper. Judging them the same on
that wrapper is the exact error this project spends its time refusing to make in
code, made by hand in the judgement of it. It was corrected to **wrong** in that
baseline. The extractor-16 rerun no longer forms a proposal for #46, so it is
retained here as judgement history but is not in the current proposal table.

## Adjudicated, on the census

| | value |
|---|---|
| `adjudicated_false_proposal_rate` overall | 4/499 = **0.0080** |
| upper bound, borderline counted wrong | 5/499 = 0.0100 |
| `adjudicated_false_proposal_rate` given opportunity | 4/32 = **0.125** |
| upper bound, given opportunity | 5/32 = 0.1562 |
| 95% interval (Wilson) on 4/32 | **0.050 – 0.281** |

**Read the conditional rate, and read its interval.** The overall rate is small
mainly because a version action is rarely reachable — 32 opportunities across
the whole corpus. When one is reachable, roughly **one proposal in eight points
at a different incident**, and 32 trials cannot narrow that below a range from
about 5% to about 28%.

No threshold was set from this repository alone, and not because the number is
unflattering: a census of one repository has no sampling error but says nothing
about generalisation, and 32 trials is a small basis for a product threshold.
The second-repository census now exists in `vllm_holdout_judgement.md`; its
conditional adjudicated rate is materially worse, which confirms that this
DeepSeek figure does not transfer as a repository-independent property.

Neither figure is an action rate. None of these could become a recommendation
without the user naming the failing package or confirming the echoed reading,
which is the hard gate. The current census records
`authorized_action_count = 0` directly and fails if it ever becomes non-zero.

## What the wrong ones keep having in common

#1648 is a question about how to steer a running agent, proposed against an
unrelated bug. In the earlier 60-report adjudication, four of eight wrong
matches were feature requests or questions. Nothing in the pipeline asks whether
a report describes a failure at all.

That is the next phase's product-quality target. It is not to be approached by
adding verbs, package names or sentence-shape rules; those are frozen until a
product metric exists to move.

## Method notes

* Leave-one-out: the sampled report is deleted inside a transaction that is
  rolled back, so the corpus is unchanged by the measurement — verified by the
  suite's database guard.
* The query is title + body + comments, matching how signatures are mined.
  Eligibility is measured on the body alone, so the population is stable across
  revisions.
* A positive control — a report known to be a released incident — is asked the
  same way in every run and must reach a proposal, or the run fails.
* The arithmetic behind these numbers is tested in
  `tests/test_holdout_metrics.py`: which denominator each rate is over, what the
  conditional rate is when there were no opportunities, pooling, provenance, and
  the threshold's exit code.
