# Holdout judgement — seed 20260904, sample 60

`evals/holdout.py` measures how often a **real** report, with itself removed
from the evidence, is matched to some *other* report. The machine can only count
matches; whether two reports are the same incident is a reading, so it is
recorded here by hand rather than assumed either way.

Run: `uv run python evals/holdout.py --sample 60 --seed 20260904`

    other_report_match_rate     0.2667  (16/60)   machine-counted
    proposal_opportunity_count  3                 matches where a released fix existed to point at
    false_proposal_count        0
    false_proposal_rate         0.0000  (0/60)    gated at 0.05

## A correction to the previous run

The first version of this measurement asked with the report's **body** while the
corpus had been mined from its **title and body** (`features_for_object`). The
query was therefore systematically poorer than the rows it was compared against
— a methodological asymmetry of my own making. Fixed, the count goes from 15 to
16: **#2463** appears, and the previous judgement was one pair short.

The eligibility filter still measures the body alone, on purpose. It decides
*which* reports are sampled, so changing it would change the population and make
the two runs incomparable.

## The 16 matches, read

| report | matched to | verdict |
|---|---|---|
| #1660 Windows folder picker truncates UTF-16 paths | #2126 Windows 原生目录选择器截断含 U+xx00 汉字的路径 | **same** |
| #255 会话日志 seq 重叠, corrupt session log | #2627 DSH-BUG-REPORT-seq-gap-overlap | **same** |
| #1627 到达输出 token 上限, invalid pi-ai replay state | #1263 [Bug] INVALID_REPLAY_STATE "block count does not…" | **same** |
| #5166 输出过长被截断不会自动重试 | #807 max-tokens 截断带工具调用后，下一轮报 invalid pi-ai replay state | **same** |
| #2463 页面加载失败，Failed to load plugins（dsh-script-library 未激活） | #5232 更新 dsh-agent-teams 后崩了再安装也启动不了 | borderline — both are a plugin failing to activate after an update, different plugins |
| #2927 cordis_inspect_query 无页面应答时永久挂起 | #1415 Session resume fails: Host Cordis inspect provider | borderline |
| #1315 项目目录迁移后 corrupt session log | #3577 Windows: EXDEV cross-device link not permitted | borderline |
| #3239 按照插件后 dsh web 无法启动 | #5066 我一觉醒来，它就这样了 | borderline — the matched title carries nothing to judge by |
| #4710 有没有可自定义重试时间的插件 | #4529 --profile headless crashes on any tool call | **wrong** |
| #4460 本地部署大模型后修改文件报错 | #1063 不支持 tokenhub 的 api key | **wrong** |
| #3818 怎么在不动旧版本的情况下更新版本 | #3710 npx dsh web 一直卡着 | **wrong** |
| #3029 [Ideas] edit 工具并行编辑提示 | #3111 [Bug] 版本 token 含 ctime 误报 FS_STALE_VERSION | **wrong** |
| #1131 创建插件中断后无法启动 | #328 win10 pnpm dsh web 报错 | **wrong** |
| #1930 {"param":null,"type":"invalid_request_error"} | #2770 长对话卡住，api 413 | **wrong** |
| #4891 方舟 CodingPlan 套餐期望接入 | #4856 更新模型无法拉取 GLM 最新版本 | **wrong** |
| #972 taskkill 阻塞时 dsh web 仍监听 3080 | #469 workspace-write 调用外部工具卡死 | **wrong** |

**4 same, 4 borderline, 8 wrong.**

| adjudicated | value |
|---|---|
| `confirmed_duplicate_rate` | 4/60 = 0.067 |
| `borderline_rate` | 4/60 = 0.067 |
| `adjudicated_false_identity_rate` | 8/60 = **0.133** |
| upper bound, counting every borderline as wrong | 12/60 = **0.200** |

The machine number, 0.2667, is not any of these. It counts real duplicates as
matches, which they are, and as errors, which they are not - which is why it is
named `other_report_match_rate` and not a false-identity rate.

## What the wrong ones have in common

Four of the eight are not incident reports at all: `有没有可自定义重试时间的插件`,
`怎么在不动旧版本的情况下更新版本？`, `[Ideas] edit 工具是否应该…`, `方舟的
CodingPlan 套餐期望能够接入` are feature requests and questions. They describe a
wish, not a failure, and there is no incident for them to be. Matching them to a
bug report is a category error before it is an identity error.

*(An earlier version of this file said five of eight. That was a miscount: the
other four - #4460, #1131, #1930, #972 - are genuine failure reports matched to
the wrong incident.)*

Nothing in the pipeline yet asks whether a report describes a failure at all.
That remains the clearest improvement lead, and it is recorded in
`docs/known_bypasses.md` rather than acted on here.

## Why `false_proposal_rate` is 0.00, and what it does and does not show

Three of the sixteen matched an incident that carried a released fix to point
at, so a version action was reachable for them. **None of the three produced
one**, and that is a real observation about those three rather than an artefact.

For the other thirteen the version gate stopped the answer before any action was
considered: it was a second gate doing the work, not the identity gate being
right. With only three opportunities the rate is a weak measurement, and it
should be read next to `proposal_opportunity_count` rather than on its own.

The number to watch is the match rate and its adjudication. The proposal rate is
the one that describes harm.
