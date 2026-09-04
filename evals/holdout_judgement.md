# Holdout judgement

`evals/holdout.py` asks about real reports with the report itself removed from
the evidence. The machine counts matches and proposals; **whether two reports are
the same incident is a reading**, so nothing the machine reports is called an
error. The adjudication is here, by hand, and can be argued with.

Corpus: `deepseek-ai/deepseek-harness`, 550 discussions, 499 eligible
(≥200 characters of body), 15,682 signature rows, extractor 15.
Numbers from a different corpus are not comparable to these — CI syncs 200
discussions and will produce different ones from the same code.

## Runs

| run | reports | matched another | opportunities | proposals | overall | given opportunity |
|---|---|---|---|---|---|---|
| fixture, seed 20260904 | 60 | 16 | 3 | 0 | 0.000 | 0.000 |
| wide, seed 20260904 | 300 | 55 | 17 | 2 | 0.0067 | 0.118 |
| seed 11 | 100 | 14 | 4 | 1 | 0.010 | 0.250 |
| seed 22 | 100 | 24 | 10 | 3 | 0.030 | 0.300 |
| seed 33 | 100 | 23 | 7 | 2 | 0.020 | 0.286 |
| pooled 11/22/33 | 300 draws, 246 distinct | 61 | 21 | 6 | 0.020 | 0.286 |

**The fixture's 0/60 does not generalise.** It is a repeatable regression, and
it had three opportunities. The same code on wider samples proposes. Anyone
quoting 0.00 as a system error rate is quoting a sample too small to have found
one.

## Every proposal, read

| report | matched to | verdict |
|---|---|---|
| #46 无法启动 dsh // Cannot launch dsh | #1916 无法绑定非 127.0.0.1 的 IP 地址 | **same** — different titles, but both bodies fail with `plugin tree failed to load: failed to apply loader entry include (cordis:include)` |
| #5222 思考提示 "Error: unknown tool"（build 报 `thinking.budget` missing） | #5144 一更新 build 整个环境坏掉（`isJsonValue` is not exported） | borderline — both are a build breaking after an update, different missing symbols |
| #4954 Python SDK cannot resume a persisted session (id collision) | #4066 Plugin-registered commands execute but render nothing | **wrong** |
| #1648 不支持打断模式吗 | #1507 --host 0.0.0.0 长任务后历史加载失败 | **wrong** — and #1648 is a question about steering behaviour, not a failure report |
| #4967 Programmatic agent creation silently no-ops ({{model}} unset) | #4666 Spawn-backed subagents drop reasoningEffort | **wrong** — same area, different root cause |

Occurrences: #1648 in seeds 11 and 33; #4967, #46, #4954 in seed 22; #5222 in
seed 33; #4954 and #46 in the wide run.

## Adjudicated

| | pooled 11/22/33 | wide 300 |
|---|---|---|
| `adjudicated_false_proposal_rate` overall | 4/300 = **0.013** | 1/300 = **0.003** |
| upper bound, borderline counted wrong | 5/300 = 0.017 | 1/300 = 0.003 |
| `adjudicated_false_proposal_rate` given opportunity | 4/21 = **0.19** | 1/17 = **0.059** |
| upper bound, given opportunity | 5/21 = 0.238 | 1/17 = 0.059 |

**Read the conditional rate.** The overall rate is small mainly because a
version action is rarely reachable — 21 opportunities in 300 draws. When one
*is* reachable, roughly **one proposal in five points at a different incident**.
The overall number is what a user of this corpus meets; the conditional number
is what the identity model is actually worth.

Neither is an action rate. None of these could become a recommendation without
the user naming the failing package or confirming the echoed reading, which is
the safety gate and is separately asserted at zero.

## What the wrong ones keep having in common

#1648 is a question about how to steer a running agent. In the earlier
60-report adjudication, four of eight wrong matches were feature requests or
questions. Nothing in the pipeline asks whether a report describes a failure at
all, and it keeps producing the same category of error - a wish matched to a
bug.

That is the next phase's product-quality target. It is not to be approached by
adding verbs, package names or sentence-shape rules; those are frozen until a
product metric exists to move.

## Method notes

* Leave-one-out: the sampled report is deleted inside a transaction that is
  rolled back, so the corpus is unchanged by the measurement.
* The query is title + body + comments, matching how signatures are mined.
  Eligibility is still measured on the body alone, so the population is stable
  across revisions.
* A positive control - a report known to be a released incident - is asked the
  same way in every run and must reach a proposal, or the run fails. Without it,
  a zero would describe the harness.
