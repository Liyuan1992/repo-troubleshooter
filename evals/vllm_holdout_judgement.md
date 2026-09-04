# vLLM holdout judgement

This is the second-repository counterpart to `holdout_judgement.md`. The
machine can count matches and proposals; deciding whether two Issues describe
the same incident is a human reading, so the machine numbers are not called
error rates until the pairs below are adjudicated.

## Corpus and method

- Repository: `vllm-project/vllm`.
- Stored support surface: the latest 1,000 Issues and 1,000 pull requests, plus
  frozen historical Issue #6461 and PR #6463.
- Upstream at the observed probe: 17,893 Issues and 36,645 pull requests. This
  is therefore a **census of the bounded stored corpus**, not a census of all
  vLLM history.
- Eligible reports: 1,001/1,001 Issues. Each Issue is removed in its own
  transaction, diagnosed using its title, body and comments, then rolled back.
- Signatures: 65,580 at extractor 16.
- Version input: 230/1,001 Issues supplied a version through a repository
  template field that the profile knows how to read. Missing versions remain
  `null`; an arbitrary version mention in prose is not promoted to authority.
- Positive control: the reviewed `/metrics` regression on 0.5.2 still reaches
  a proposal for v0.5.3.
- Parallel execution: four independent transactions. A 10-case fixture was
  run sequentially and in parallel first; every case and control result was
  identical.

The reproducible machine report is `evals/reports/vllm-census-final.json`
(generated and ignored by Git because it contains a full corpus-derived run).

## Machine result

| measure | result |
|---|---:|
| Issues asked | 1,001 |
| matched another Issue | 96 (0.0959) |
| version-action opportunities | 10 |
| proposals | 10 |
| proposal rate overall | 10/1,001 = **0.0100** |
| proposal rate given opportunity | 10/10 = **1.000** |
| authorised version actions | **0** |

An opportunity requires both released-fix evidence and a report-supplied
current version. An earlier run incorrectly assigned 0.5.2 to every modern
Issue and incorrectly counted matches without a readable version as
opportunities. Those runs are discarded; the table above uses report-bound
versions and the corrected denominator.

## Every proposal, read in full

| report | proposed incident | verdict and distinguishing cause |
|---|---|---|
| [#19668](https://github.com/vllm-project/vllm/issues/19668) EngineCore `TimeoutError` while serving | [#32732](https://github.com/vllm-project/vllm/issues/32732) no valid attention backend at model load | **wrong** — shared outer worker/EngineCore frames; runtime shared-memory dequeue timeout versus startup backend incompatibility |
| [#52065](https://github.com/vllm-project/vllm/issues/52065) DSpark startup DeepGEMM illegal address on 0.27.0 | [#49922](https://github.com/vllm-project/vllm/issues/49922) FlashMLA sparse-prefill TMA assertion on 0.26.0 | **wrong** — the source explicitly lists the target family as a different version, kernel and signature |
| [#43174](https://github.com/vllm-project/vllm/issues/43174) DeepGEMM weight post-process divides by zero on H20 TP=16 | [#54219](https://github.com/vllm-project/vllm/issues/54219) `fp8e4nv` datatype failure on A100 | **wrong** — FP8 is only the topic; hardware, model-load phase and exception differ |
| [#53089](https://github.com/vllm-project/vllm/issues/53089) P/D block-count mismatch from tool-dictionary serialisation order | [#54096](https://github.com/vllm-project/vllm/issues/54096) `warning_once` caches live exceptions and leaks tracebacks | **wrong** — unrelated state-count assertion versus logger cache lifetime bug |
| [#53477](https://github.com/vllm-project/vllm/issues/53477) DFlash2 reprocesses Qwen context every reply | [#52049](https://github.com/vllm-project/vllm/issues/52049) Gemma MTP throughput collapses at high context | **wrong** — both are speculative-decoding performance reports, but the method, model and observed mechanism differ |
| [#44318](https://github.com/vllm-project/vllm/issues/44318) XPU GGUF load lacks `ggml_dequantize` custom op | [#38884](https://github.com/vllm-project/vllm/issues/38884) CUDA Gemma bitsandbytes load fails in Dynamo fake-tensor execution | **wrong** — only the broad model-loading context is shared |
| [#40791](https://github.com/vllm-project/vllm/issues/40791) DCP + EAGLE3 workspace is locked at 0 MB | [#40919](https://github.com/vllm-project/vllm/issues/40919) `RMSNormGated` input guard breaks Dynamo tracing | **wrong** — workspace allocation in speculative decode versus a compile-tracing guard |
| [#41287](https://github.com/vllm-project/vllm/issues/41287) Ray PP rank update causes KV-cache layer `KeyError` | [#54723](https://github.com/vllm-project/vllm/issues/54723) FlashInfer TRTLLM MoE hangs under DP/EP | **wrong** — rank/config indexing bug versus fused-MoE backend hang |
| [#54526](https://github.com/vllm-project/vllm/issues/54526) Speculators-trained Eagle3 model cannot load | [#54723](https://github.com/vllm-project/vllm/issues/54723) FlashInfer TRTLLM MoE hangs under DP/EP | **wrong** — different model path, execution phase and failure |
| [#38884](https://github.com/vllm-project/vllm/issues/38884) Gemma load crashes in Dynamo fake-tensor execution | [#36010](https://github.com/vllm-project/vllm/issues/36010) Qwen batch inference is slow with guided regex | **wrong** — copied benchmark scaffolding links a load-time crash to a throughput report |

There are no borderline or confirmed-duplicate proposals in this census.

## Adjudicated result

| measure | result |
|---|---:|
| adjudicated false-proposal rate overall | 10/1,001 = **0.0100** |
| adjudicated false-proposal rate given opportunity | 10/10 = **1.000** |
| Wilson 95% interval on 10/10 | **0.722–1.000** |
| unsafe/authorised action rate | **0/1,001** |

The overall number looks small because only ten stored reports both matched a
released incident and supplied a usable current version. The conditional number
is the product-quality result: in this bounded vLLM corpus, every time the
engine had enough information to form a version proposal, it pointed at a
different incident.

This does **not** contradict the safety result. Free text produced proposals,
not authorised recommendations. None crossed the package-or-confirmation gate.
It does show why that gate is necessary and why the DeepSeek identity rate must
not be treated as repository-independent.

## Product conclusion

The second repository proves that the evidence architecture transfers: Issues,
GitHub-native closing PRs, merge commits and first-containing releases all work.
It also disproves the stronger claim that the current lexical identity model
generalises with acceptable conditional precision.

The chosen product tradeoff remains defensible: free text may retrieve and
propose, while an action requires a structured failing subject or confirmation
of the echoed incident/evidence/action digest. Improving proposal quality should
be measured against this census and should not resume the unbounded game of
adding package names, verbs or sentence shapes. A future semantic or structured
identity channel needs its own calibrated acceptance threshold before it can
authorise anything.
