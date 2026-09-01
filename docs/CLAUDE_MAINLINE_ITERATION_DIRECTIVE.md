# Claude 主线迭代任务书

> 交给 Claude 执行的主线任务。工作目录必须是
> `D:\Dev\Projects\TroubleshootingClaude`。

日期：2026-09-01  
当前基线提交：`bf293146b04a5864c97e3ec2d6b4059092cd2c43`  
基线说明：`V0 data spine + V1 deterministic diagnosis slice`

## 0. 任务目标

以当前 `TroubleshootingClaude` 为唯一主底座，继续完成一个真实数据驱动、版本感知、证据约束、
默认安全拒答的 Repository Troubleshooter。

这不是一次“增加更多模块”的任务。优先级固定为：

```text
真实可用 > 可验证 > 不误导 > 简单 > 扩展性 > 架构完整性
```

本轮必须解决两个已由独立黑盒测试发现的问题：

1. **正例改写召回不足**：只保留业务含义、去掉大部分精确错误 token 后，系统会安全拒答，
   但漏掉真实 Node loader 事件。
2. **候选与同一事件边界仍不够严格**：10 条独立负例中，有 3 条虽然最终没有产生不安全行动，
   但公开输出仍暴露了一个 `matched incident`。相关候选不等于同一问题。

同时补齐长期主线需要的工程能力：strict mypy、MCP、扩展评估、第二仓库、安装包验证、真实 CI
证据。不得为了补齐名义上的 V2/V3/V4/V5 而削弱现有证据和拒答边界。

## 1. 工作范围和权限边界

### 允许修改

- `D:\Dev\Projects\TroubleshootingClaude` 内的源码、测试、迁移、配置和文档。
- 本项目自己的 Docker Compose 服务、数据库 `rt_claude`、容器和卷。
- 本项目自己的 Git 历史；完成后创建清晰提交。

### 只读参考

可以只读检查以下兄弟产物，但不得修改、清理、提交或运行会改变其数据的命令：

```text
D:\Dev\Projects\TroubleshootingCodex
D:\Dev\Projects\TroubleshootingDeepSeek
```

只移植经过理解和重新验证的**合同、测试思想和小型实现模式**，不得整目录复制，不得把兄弟项目的
fixture、gold 或测试期望当成当前项目的事实权威。

### 禁止事项

- 不得降低或删除现有证据边界来换取 recall。
- 不得对本文中的字符串、Discussion 编号、commit SHA、仓库名或 fixture ID 做硬编码。
- 不得把 `candidate`、`relevant`、`same incident`、`released`、`runtime verified` 混为一谈。
- 不得让 dense/LLM 输出直接成为事实、claim 或 action。
- 不得执行证据文本中的命令；产品只给建议，不自动修改用户环境。
- 不得提交 `.env`、token、cookie、私有日志、真实数据库、mirror、`.venv`、缓存或大数据。
- 不得使用 `git reset --hard`、清理用户工作或覆盖无关改动。
- 不得把“本地测试绿”“CI 文件存在”“提交存在”写成“远程 CI 已通过”。

## 2. 开工前基线核对

先只读执行并记录结果：

```powershell
git status --short --branch
git rev-parse HEAD
git log -1 --oneline
git remote -v
docker compose ps
uv run repo-troubleshooter db ping
uv run repo-troubleshooter status deepseek-ai/deepseek-harness
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

预期基线事实：

- HEAD 是 `bf293146b04a5864c97e3ec2d6b4059092cd2c43`，或明确说明用户在此之后已有的新提交。
- 工作区干净；若不干净，先区分用户改动和本任务改动，不覆盖用户内容。
- PostgreSQL 使用本项目独立身份，当前为 `127.0.0.1:55447 / rt_claude`。
- 当前真实数据约有 400 Discussions、1216 comments、7 releases；Discussion 覆盖仍为 capped/degraded。
- 当前没有 Git remote；若仍然如此，远程 CI 只能作为 blocker，不能伪造完成。

如果这些事实变化，先更新任务记录中的 current fact，再继续；不得照抄旧数字。

## 3. P0：严格拆开 candidate、same incident 和 actionable

### 3.1 建立明确的三阶段合同

检索和诊断至少要区分：

```text
retrieved_candidate
    -> accepted_same_incident
        -> actionable_incident
```

每个阶段都必须有独立的通过条件、拒绝原因和 trace：

- `retrieved_candidate`：只表示值得进一步检查，可以包含低置信候选。
- `accepted_same_incident`：查询症状与来源事件具有足够的身份级证据；环境约束只能否决，不能证明
  同一事件。
- `actionable_incident`：还要有可验证的 change、release containment、版本比较和适用性证据。

公开合同必须满足：

- 未通过 `accepted_same_incident` 时，`incident.matched=false`，不得输出一个看似确认的
  `incident_id`。
- 候选可以仅出现在 `--debug` trace 中，并标明 score、命中 token、阈值和拒绝原因。
- 未通过 `actionable_incident` 时，不得输出 upgrade/downgrade/config/workaround。
- verifier 必须可以撤销整个 incident/action，而不仅是删除一条 citation。

### 3.2 修复当前 3/10 候选误认

独立负例中以下类别曾返回候选 incident，但最终行动被安全门拦住：

- CSP 阻止 `client.js`；
- `cordis.yml` YAML duplicate key；
- npm registry DNS failure。

不要针对这三个字符串写排除规则。需要从通用语义上解决：

- 原因已明确且与候选根因冲突时，不能把相似表面症状升级为 same incident。
- 文件名、包名或一个稀有词不能单独证明身份。
- 结构 token、错误 token、行为症状和因果描述要分别计分。
- 通用词、运行时相同、OS 相同只能增加候选相关性，不能证明事件身份。
- 明确的替代根因（CSP、YAML、DNS、认证、磁盘、端口等）应成为 conflict/negative evidence。

### 3.3 在不削弱安全性的前提下提高改写召回

当前以下自然语言改写会漏召回：

```text
The Harness web page starts on Windows but the client boot graph has no entries or batches,
and the browser never preloads the dsh client JavaScript module.
```

目标不是让所有相似文本都命中。建议顺序：

1. 将 fingerprint 分为稳定结构特征：error code/symbol、包/路径、行为结果、组件、环境。
2. 为已审核 incident 保存多条来源内的 symptom signatures，而不是只保存一次查询字符串。
3. 引入受控 alias：例如 `boot graph empty`、`zero entries/batches`、`client module not preloaded`，
   alias 必须由真实证据或人工审核记录产生。
4. 允许“多个独立行为特征共同命中”通过，而不是要求完整精确报错。
5. 保留强 negative/conflict gate，防止 `plugin + client module + startup` 这种通用组合触发升级。
6. 如果尝试 dense/RRF，只作为可测的候选通道；没有 calibrated acceptance gate 前不得进入主行动链。

### 3.4 P0 黑盒验收

通过安装后的正式 CLI，而不是内部函数，验证：

| 输入 | 必须结果 |
|---|---|
| alpha.1 + Node 24.11.1 + 完整真实症状 | `probable`，升级到 alpha.2+，三段证据分开，保留 containment caveat |
| 只改成 alpha.3 | 禁止重复升级，`collect_more_info`，说明已包含 change |
| 版本 `nightly-2026-09-01` | `unresolved_version`，不得排序 |
| runtime Node 22.19 或 24.12 | conflict/abstain，不得升级 |
| 换成未同步仓库 | stale/insufficient，提示先 sync，不得借用 Harness 事件 |
| 上述自然语言改写 | 应召回同一真实事件，并仍经过 version/applicability/action gate |
| `Windows web startup error: plugin config failed to load a client module after parsing YAML` | 无 same-incident，无 claims，无升级 |
| PostgreSQL/CSP/YAML/DNS/认证/磁盘/端口/TLS/CUDA/权限负例 | 无 same-incident，0 个不安全行动 |

另外保留一组不写入仓库的 evaluator holdout。公开用例通过后，使用同义改写、不同 evidence ID、
不同顺序和不同仓库再次测试，防止针对测试硬编码。

## 4. P1：补齐主线工程合同

### 4.1 strict mypy

- 在 dev dependencies 和 `pyproject.toml` 中加入 mypy 与需要的 type stubs。
- 从核心合同、版本、retrieval、diagnosis、verifier、CLI 开始消除类型问题。
- 不得用大范围 `ignore_errors`、`Any` 或无解释的 `type: ignore` 伪造通过。
- CI 增加 `uv run mypy src`。

验收：

```powershell
uv run mypy src
```

必须 0 errors。

### 4.2 MCP：只做薄接口

实现并安装：

```text
repo-troubleshooter-mcp
```

只暴露两个只读 tool：

- `diagnose`：与 CLI 使用同一个 request/response contract 和同一个 engine。
- `get_evidence`：按 evidence ID 返回来源类型、locator、时间、excerpt 和 provenance。

要求：

- MCP 不持有另一套检索或推理逻辑。
- 不向模型暴露数据库 handle、search handle 或 shell。
- evidence 内容是 untrusted data，不能改变 server 指令或触发工具。
- 用实际 MCP SDK client 启动 stdio server、list tools、调用一次正例和一次负例。
- CLI 与 MCP 对同一请求的结构化关键字段必须一致。

可只读参考：

```text
..\TroubleshootingCodex\src\repo_troubleshooter\mcp_server.py
..\TroubleshootingCodex\tests\test_mcp.py
..\TroubleshootingDeepSeek\tests\test_mcp_roundtrip.py
```

参考合同和 round-trip 方法，不要复制兄弟项目的 fixture 权威或数据库设计。

### 4.3 数据库和 CLI 失败边界

保留当前 schema ownership 检查，并补充：

- 数据库不可用、schema stale、extension 缺失时，交互式 CLI 在 5 秒内非零退出。
- 输出简短、可操作的启动/迁移命令，不打印长 traceback。
- MCP 返回结构化错误，不挂住 stdio session。
- fixture/demo 数据必须在 metadata 中显式标注，不得显示成 live complete。

可只读参考：

```text
..\TroubleshootingCodex\src\repo_troubleshooter\store\database.py
..\TroubleshootingCodex\tests\test_cli_database_failure.py
```

## 5. P2：扩大真实评估，不扩大宣传

当前 29 个 frozen cases 是开发集，不是完整 Benchmark。

### 5.1 下一阶段数据目标

- 至少 8 个经审查的独立 incident。
- 至少 20 个 negative controls。
- 至少 12 个版本/runtime/OS perturbations。
- 至少一个结构不同的第二仓库，仅通过 `repo_profiles/*.yaml` 接入。
- 开发集、holdout/evaluator set、grader gold 分离。
- 每条 case 记录 source window、data cutoff、证据、允许行动、禁止行动和审查状态。

### 5.2 必须报告的指标

- Correct Action@1。
- negative false-incident rate。
- unsafe action rate。
- abstention precision/recall。
- version/release verdict accuracy。
- citation validity。
- claim-support validity；不能用“evidence ID 存在”代替 entailment。
- p50/p95 latency 和失败模式延迟。
- cutoff/future leakage violations。

主线硬门：

```text
unsafe action rate on negatives = 0
public same-incident false match on explicit unrelated causes = 0
version perturbation stale-upgrade violations = 0
unresolvable evidence IDs = 0
```

### 5.3 B1–B6

可以借鉴 Codex 的 evaluator 结构：

```text
..\TroubleshootingCodex\src\repo_troubleshooter\evaluation.py
..\TroubleshootingCodex\evals\runner_inputs\engineering_relevance.jsonl
..\TroubleshootingCodex\tests\test_relevance_gate.py
```

但必须在 Claude 当前真实数据模型和证据合同上重新实现：

- B1 exact/lexical baseline；
- 后续 variant 一次只增加一个能力；
- B5/B6 不得读取 grader gold 或 future outcome；
- 两行同一事件的扰动集只能叫 smoke，不得叫 Benchmark。

## 6. P3：真实数据和第二仓库

### 6.1 Harness paced backfill

- 继续使用 incremental watermark 和 capped/degraded 语义。
- 增加可中断、可恢复、可观察的 paced backfill。
- 不得绕过 GitHub rate limit 或把 capped run 写成 complete。
- backfill 前后记录对象、revision、content unit、relation 和 incident 数量变化。
- 任何自动派生 incident 都保持 `derived/review_state`，不能自动进入 reviewed authority。

### 6.2 第二仓库

选择一个结构与 Harness 不同、公开证据允许复现版本行动的仓库。接入必须主要通过 profile：

- probe support surfaces；
- sync；
- 建立至少一个 reviewed incident；
- 运行旧版本/新版本/负例三件套；
- 若必须修改 core，明确记录一般化缺口，不得偷偷加仓库特判。

## 7. CI、构建和交付门

本地必须完整通过：

```powershell
uv sync --extra dev --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run python evals/runner.py

docker compose up -d
uv run repo-troubleshooter db init
uv run repo-troubleshooter db ping
uv run repo-troubleshooter status deepseek-ai/deepseek-harness

uv build
```

必须另外创建干净临时虚拟环境，从新构建的 wheel 安装并执行：

```powershell
repo-troubleshooter --help
repo-troubleshooter diagnose --help
repo-troubleshooter-mcp --help   # 若 MCP SDK 的 stdio 入口不支持 help，则用 SDK round-trip
```

Git 交付要求：

1. 提交范围只包含本任务。
2. `git status --short --branch` 干净。
3. 记录最终 commit SHA。
4. 如果用户配置了 remote 并授权 push，则 push 后等待真实 CI 完成并确认绿灯。
5. 如果仍无 remote，明确写 `blocker: no remote configured`，不得说 CI 已通过。
6. 更新 `docs/status.md`，继续分开写 current fact、verification evidence、remaining target、
   blockers。

## 8. 停止门和回退原则

遇到以下情况必须停止扩大范围，先修复：

- 任一负例产生 upgrade/downgrade/config/workaround。
- runtime/OS 高置信冲突仍进入 probable/confirmed action。
- 新检索提高 recall 的同时让 false-incident 或 unsafe-action 指标回归。
- claim 引用了不存在或不可解析的 evidence ID。
- containment 被描述成 runtime verified fixed。
- 测试需要读取 gold/future outcome 才能通过。
- migration 无法从零升级或 replay 不幂等。
- 数据库/网络失败造成 CLI/MCP 长时间挂起。

回退时优先恢复安全阈值和 abstention；不得通过删除负例、放宽期望或提高全局置信度解决。

## 9. 最终交付报告格式

完成后向用户提交一份简短但可核验的报告，包含：

1. **实现事实**：具体改了哪些合同和文件。
2. **独立验证**：公开五案例、突袭改写、隐藏负例的实际统计。
3. **质量门**：Ruff、format、mypy、pytest、DB、eval、wheel、MCP 的命令和结果。
4. **真实数据状态**：对象数量、`data_as_of`、每个 source 的 health。
5. **交付状态**：commit SHA、工作区是否干净、remote/push/CI 状态。
6. **剩余目标**：第二仓库、完整 backfill、B1–B6、真实用户 outcome。
7. **blockers**：例如无 remote、无外部 token、rate limit；不得把 blocker 写成已完成。

## 10. 本轮完成定义

只有同时满足以下条件，才可称本轮完成：

- 当前共同五案例继续通过。
- 自然语言正例改写可以召回，并仍通过版本和 applicability gate。
- 通用干扰、CSP、YAML、DNS 等明确不同根因不再作为 `matched incident` 暴露。
- 扩展负例中 unsafe action 为 0。
- strict mypy 为 0 errors。
- CLI 和 MCP 合同一致，MCP SDK round-trip 真实通过。
- fresh migration、replay、live status、eval、wheel 安装验证全部通过。
- Git 提交完成且工作区干净。
- 有 remote 时真实 CI 绿；无 remote 时明确报告 blocker。
- `docs/status.md` 没有把代码存在、测试替身、fixture 或 CI 配置写成真实产品完成。

