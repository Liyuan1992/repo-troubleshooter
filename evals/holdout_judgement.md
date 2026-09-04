# Holdout judgement — seed 20260904, sample 60

`evals/holdout.py` measures how often a **real** report, with itself removed
from the evidence, is matched to some *other* incident. The machine can only
report the pairs; whether a pair is the same incident is a reading, so it is
recorded here by hand rather than assumed either way.

Run: `uv run python evals/holdout.py --sample 60 --seed 20260904`

    false_identity_rate  0.25   (15/60)  — upper bound, machine-counted
    false_proposal_rate  0.00   (0/60)   — machine-counted, hard-gated

## The 15 matches, read

| report | matched to | verdict |
|---|---|---|
| #1660 Windows folder picker truncates UTF-16 paths | #2126 Windows 原生目录选择器截断含 U+xx00 汉字的路径 | **same** |
| #255 会话日志 seq 重叠, corrupt session log | #2627 DSH-BUG-REPORT-seq-gap-overlap | **same** |
| #1627 到达输出 token 上限, invalid pi-ai replay state | #1263 [Bug] INVALID_REPLAY_STATE "block count" | **same** |
| #5166 输出过长被截断不会自动重试 | #807 max-tokens 截断带工具调用后，下一轮报 invalid pi-ai replay | **same** |
| #2927 cordis_inspect_query 无页面应答时永久挂起 | #1415 Session resume fails: Host Cordis inspect provider | borderline |
| #1315 项目目录迁移后 corrupt session log | #3577 Windows: EXDEV cross-device link not permitted | borderline |
| #3239 按照插件后 dsh web 无法启动 | #5066 我一觉醒来，它就这样了 | borderline (title carries nothing) |
| #4710 有没有可自定义重试时间的插件 | #4529 --profile headless crashes on any tool call | **wrong** |
| #4460 本地部署大模型后修改文件报错 | #1063 不支持 tokenhub 的 api key | **wrong** |
| #3818 怎么在不动旧版本的情况下更新版本 | #3710 npx dsh web 一直卡着 | **wrong** |
| #3029 [Ideas] edit 工具并行编辑提示 | #3111 [Bug] 版本 token 含 ctime 误报 FS_STALE | **wrong** |
| #1131 创建插件中断后无法启动 | #328 win10 pnpm dsh web 报错 | **wrong** |
| #1930 {"param":null,"type":"invalid_request_error"} | #2770 长对话卡住，api 413 | **wrong** |
| #4891 方舟 CodingPlan 套餐期望接入 | #4856 更新模型无法拉取 GLM 最新版本 | **wrong** |
| #972 taskkill 阻塞时 dsh web 仍监听 3080 | #469 workspace-write 调用外部工具卡死 | **wrong** |

**4 same, 3 borderline, 8 wrong.** So the estimate behind the 0.25 upper bound is
roughly **0.13–0.18** false identity on real reports.

## What the wrong ones have in common

Five of the eight are not incident reports at all. `有没有可自定义重试时间的插件`,
`怎么在不动旧版本的情况下更新版本？`, `[Ideas] edit 工具是否应该…`, `方舟的
CodingPlan 套餐期望能够接入` are feature requests and questions; they describe a
wish, not a failure, and there is no incident for them to be. Matching them to a
bug report is a category error before it is an identity error, and it is a
tractable one: nothing yet asks *is this a failure report at all*.

That is the clearest improvement lead this measurement produced, and it is
recorded rather than acted on - it belongs to the next round, not this one.

## Why `false_proposal_rate` is 0.00, and why that is not reassuring on its own

None of the 15 reached a version action, because the incidents they matched
carry no released fix to point at, so the version gate stopped them. That is a
second gate doing the work, not the identity gate being right. If those
incidents had had releases, some of the eight wrong matches would have become
proposals. The number to watch is `false_identity_rate`; `false_proposal_rate`
is the one that describes harm.
