# Repository Troubleshooter

[English](README.md) | 简体中文

一个受证据约束、理解开源软件版本演进的故障排查工具。

> “我正在使用这个版本、这个运行环境，并遇到了这个错误。我现在究竟应该怎么做？”

它专注回答一个问题：**结合用户的版本与运行环境，这是不是一个已知事件；如果是，下一步应该做什么？**

`insufficient_evidence`（证据不足）是正常且重要的产品结果，不是系统失败。

## 为什么需要这个项目

普通语义检索很容易找到“看起来相似”的讨论，然后直接建议升级，却未必知道：

- 两段报错是否真属于同一事件；
- 修复只是合并了，还是已经进入正式发布；
- 用户当前版本是否已经包含该改动；
- Git 树中包含某个提交，是否真的证明运行时症状已解决；
- 用户描述中提到的包，究竟是故障主体、普通依赖，还是已明确无恙的组件。

```text
普通混合 RAG：             请升级到 0.1.2-alpha.1

Repository Troubleshooter：你的版本已经包含该改动
                            （首个包含版本：0.1.2-alpha.1），
                            因此这很可能是另一个事件。
                            下一步：请补充 <X>。
```

如果这个差异不能在真实仓库、真实报告和真实版本证据上复现，本项目就没有成立的价值。

## 核心产品契约

一次诊断必须明确经过三个阶段：

```text
retrieved_candidate
        ↓
accepted_same_incident
        ↓
actionable_incident
```

| 阶段 | 含义 | 可以输出什么 |
|---|---|---|
| `retrieved_candidate` | 找到了可能相关的候选 | 只能说明“值得查看”，不能声称是同一事件 |
| `accepted_same_incident` | 身份证据足以接受为同一事件 | 可以回显理解和证据，但还不一定能给版本动作 |
| `actionable_incident` | 身份、版本适用性和证据都通过 | 可以形成升级、降级、迁移、配置变更或临时规避提案 |

候选没有通过身份门时，不会被包装成 `matched incident`；证据不足时，系统应停在相应阶段并说明原因。

## 自由文本不能直接授权动作

自由文本只用于召回和理解，不能单独授权升级、降级或配置变更。动作授权只能来自以下方式之一：

1. 用户用结构化 `--package` 明确指定故障主体，并且它与候选事件的故障主体一致；
2. 工具先完整回显事件、身份规则、共享证据、引用和拟议动作，用户再提交与本次理解绑定的 `--confirm` 摘要。

如果 `--package` 只命中了依赖、健康组件、矛盾主体或角色不明的包，系统会进入确认路径，而不会直接授权动作。

这是一项明确取舍：允许召回少一些、确认多一步，换取错误身份不会静默变成错误指令。

## 证据模型为什么看起来很“谨慎”

本项目刻意保留下列区别：

| 不能混为一谈的概念 | | |
|---|---|---|
| 内容相关 | ≠ | 同一个问题 |
| PR 已合并 | ≠ | 修复已发布 |
| 某个 release 包含提交 | ≠ | 运行时问题已被证明修复 |
| discussion 已关闭或已回答 | ≠ | 问题已经解决 |
| 首次在版本 X 中被报告 | ≠ | 由版本 X 引入 |

例如，`git tag --contains` 的结果会以 `ReleaseContainment` 保存，并带上原始命令记录。它只能证明变更存在于对应标签的 Git 树中，不能独自证明症状已经消失。

GitHub、Git 和文档直接给出的内容，与系统推导的关系也分开保存。推导结果必须标注来源类型和证据，不能悄悄升级为事实。

## 当前状态与适用边界

当前已经具备：

- PostgreSQL 数据脊柱、可恢复同步与来源健康状态；
- GitHub Discussions、releases、Git 历史和文档快照；
- 症状指纹、候选召回、身份门、版本适用性、证据包和 claim 校验；
- CLI 与只读 MCP 两个接口；
- 已安装 wheel、CLI 子进程和全新 MCP stdio 进程的端到端测试；
- 基于真实已同步报告的 leave-one-out holdout 与全量普查。

第一个经过真实数据验证的目标是 [`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness)。该仓库没有传统的公开 `Issue → PR → Commit` 链路，因此核心模型不会假设所有 GitHub 仓库都具有相同结构。仓库差异放在 profile 中，只有经过第二个结构不同仓库的验证后，项目才会声称具备跨仓库泛化能力。

当前本地基线对 499 条合格真实报告做过普查：其中 32 次存在版本提案机会，机器形成 6 次提案；人工复核为 5 次错误身份、1 次边界案例。按机会计算的人工错误提案率为 `5/32 = 0.156`，上界为 `6/32 = 0.1875`。这说明“需要用户确认”不是装饰，而是当前产品安全边界的一部分。

这些数字描述的是当前仓库与当前语料，不代表未知仓库的系统错误率。最新实现事实、数据口径、已知缺口和外部 CI 范围请以 [docs/status.md](docs/status.md) 为准。

## 快速开始

### 前置条件

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker 与 Docker Compose
- 可选：GitHub CLI 登录状态，或 GitHub Token

### 1. 启动数据库并安装

Bash：

```bash
docker compose up -d
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
cp .env.example .env
uv run rt db init
```

PowerShell：

```powershell
docker compose up -d
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
Copy-Item .env.example .env
uv run rt db init
```

根据需要编辑 `.env`。`RT_GITHUB_TOKEN` 是可选项；如果本机已执行 `gh auth login`，CLI 会尝试使用现有认证。

### 2. 探测、同步和查看状态

```bash
uv run rt profiles
uv run rt probe deepseek-ai/deepseek-harness
uv run rt sync deepseek-harness --max-discussions 200
uv run rt status deepseek-ai/deepseek-harness
```

同步是幂等且可恢复的。若需要继续分页回填，可查看：

```bash
uv run rt sync --help
```

### 3. 运行诊断

准备一份 `report.txt`，然后执行：

```bash
uv run rt diagnose --repo deepseek-ai/deepseek-harness --error-file report.txt --version 0.1.2-alpha.1 --runtime "node 24.11.1" --os windows --package @deepseek-ai/dsh-client-modules
```

这里的 `--package` 不是普通搜索关键词，而是用户对“哪个包发生故障”的结构化声明。它可以重复使用。若用户无法确定故障主体，可以先不传该字段，让工具输出提案、证据和确认摘要。

常用诊断选项：

```text
--error / --error-file    直接输入报告，或从文件读取
--question                补充自然语言问题
--version                 当前核心版本
--runtime / --os          当前运行环境
--plugin / --config-key   相关插件或配置键
--package                 明确故障主体，可重复
--confirm                 确认当前理解摘要
--json                    输出机器可读 JSON
--debug                   展示候选和阶段 trace
--no-persist              不持久化本次诊断记录
```

查看完整参数：

```bash
uv run rt diagnose --help
```

### 4. 查看证据与版本包含关系

```bash
uv run rt contains deepseek-ai/deepseek-harness <commit-sha> --version 0.1.1-rc.2
uv run rt get-evidence --help
```

诊断输出中的 evidence id 可以通过 `get-evidence` 解析。工具会同时保留证据类型、来源和推导方式，便于人工复核。

## MCP 接口

MCP 服务只暴露只读能力：`diagnose` 与 `get_evidence`。

先检查安装和配置：

```bash
uv run repo-troubleshooter-mcp --check
```

以 stdio MCP 服务运行：

```bash
uv run repo-troubleshooter-mcp
```

MCP 与 CLI 应对同一请求给出相同的阶段、匹配、动作、目标和 evidence id 集合。MCP 不会直接修改仓库、安装包或更改配置。

## 项目结构

```text
src/repo_troubleshooter/
  connectors/       GitHub GraphQL/REST 与 Git 镜像
  store/            PostgreSQL 模型、迁移和签名失效
  sync/             幂等、可恢复、按来源记录健康状态的同步
  normalize/        prose / code / log / config 内容单元
  fingerprint/      错误、症状与带角色的主体特征
  retrieval/        候选召回、包族扩展和身份判定
  relations/        显式关系与症状签名
  versions/         版本规范化、适用性与 release containment
  evidence/         可追溯证据包
  diagnosis/        三阶段诊断合同与动作提案
  verifier/         claim 与 incident 撤销校验
  repo_profiles/    仓库专用配置
  cli/              命令行接口
  mcp/              只读 MCP 接口
```

## 开发与验证

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run python evals/runner.py
```

真实报告 holdout 和普查会访问本地语料库，具体参数与结果口径见：

- [evals/holdout.py](evals/holdout.py)
- [evals/holdout_judgement.md](evals/holdout_judgement.md)

注意：仓库中的 CI workflow 文件不是 CI 已通过的证据。应分别记录 workflow 是否存在、是否实际运行，以及 live 数据门是否被执行。

## 当前未完成

仍未完成的主要能力：

- 第二个结构不同仓库的完整验证；
- 全量 Discussions 历史回填；
- dense retrieval / RRF 与校准后的接受门；
- duplicate-of 关系；
- claim entailment；当前 claim-support 主要是结构性校验，不是语义蕴含；
- 更大规模、跨仓库的提案质量基线。

## 安全与威胁模型

- GitHub 内容、仓库文档、日志和代码块都按不可信输入处理；
- 自由文本可以影响召回，但不能直接授权动作；
- 围栏代码中的 path、symbol 和 error 可以帮助召回，但不能独自承载事件身份；
- 矛盾、角色不明或证据不足时，系统应拒绝形成动作；
- V1 面向合作用户，不声称抵抗任意对抗性提示构造；
- Token、凭据和私有日志不应进入提交、测试夹具或诊断输出。

详见 [docs/threat_model.md](docs/threat_model.md) 与 [docs/known_bypasses.md](docs/known_bypasses.md)。

## 进一步阅读

- [当前状态与验证边界](docs/status.md)
- [威胁模型](docs/threat_model.md)
- [已知绕过与召回缺口](docs/known_bypasses.md)
- [独立测试与改进计划](docs/INDEPENDENT_TEST_REPORT_AND_IMPROVEMENT_PLAN.md)
- [主线迭代说明](docs/CLAUDE_MAINLINE_ITERATION_DIRECTIVE.md)

## License

项目包元数据在 [pyproject.toml](pyproject.toml) 中声明为 `Apache-2.0`。当前仓库尚未包含独立的 `LICENSE` 文件；对外分发前应补齐完整许可证文本。
