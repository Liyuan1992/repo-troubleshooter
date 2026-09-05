# Repository Troubleshooter

[English](README.md) | 简体中文

根据真实仓库证据，把一份错误报告转换成可验证的故障处理建议。

Repository Troubleshooter 会检索仓库中的讨论、发布版本、提交历史和文档，回答：

- 这是不是一个已知问题？
- 修复是否已经发布？
- 用户当前版本是否已经包含该改动？
- 下一步应该怎么做，有哪些证据支持？

## 使用示例

```text
输入
  错误：     启动后 client boot graph 为空

从当前项目自动检测
  当前版本： 0.1.2-alpha.1
  运行环境： Node.js 24.11.1 / Windows
  相关包：   @deepseek-ai/dsh

输出
  已知事件： loader 相关问题
  建议动作： 升级
  目标版本： dsh-v0.1.2-alpha.2
  证据：     原始讨论、修复提交和包含该提交的 release
```

如果证据不足，工具会返回 `insufficient_evidence`，而不是给出没有依据的建议。

## 快速开始

需要 Python 3.12、[uv](https://docs.astral.sh/uv/) 和 Docker Compose。

```bash
docker compose up -d
uv sync --extra dev
cp .env.example .env
uv run rt prepare deepseek-harness --max-discussions 200
```

PowerShell 请将复制命令改为 `Copy-Item .env.example .env`。如果本机已经通过
`gh auth login` 登录 GitHub，可以不填写 GitHub Token。`prepare` 会创建或升级本地数据库并完成
首次、有范围限制的同步；可以安全重复运行，中断后也可继续。它不会自行启动 Docker，也不会超出你给出的
同步范围。

## 管理仓库证据

```bash
uv run rt profiles
uv run rt probe deepseek-ai/deepseek-harness
uv run rt sync deepseek-harness --max-discussions 200
uv run rt status deepseek-ai/deepseek-harness
```

同步过程可以重复执行，也可以中断后继续。`status` 会显示各类数据的更新时间和完整度。

## 诊断问题

将错误报告保存为 `report.txt`。在本项目目录中开发运行时，只需指向用户项目：

```bash
uv run rt diagnose --workspace /path/to/project --error-file report.txt
```

如果已经把 `rt` 安装到 `PATH`，可以直接进入需要排查的项目：

```bash
cd /path/to/project
rt diagnose --error-file report.txt
```

CLI 会从当前项目自动获取证据仓库、已安装版本、运行时、操作系统和相关包，并回显每个值及其来源。
检测到某个依赖只代表项目使用了它，不会被系统悄悄认定为故障主体。

如果找到了已有 release 的已知事件、但没有以字段形式给出故障包，CLI 会先显示它对报告的理解、匹配事件、
证据和拟议版本，再请求确认摘要。确认的含义仅是“这个已经展示的理解符合我的情况”；它只允许工具给出建议，
绝不会执行升级、修改配置或改动项目。

`--repo`、`--version`、`--runtime`、`--os` 和 `--package` 仍然保留，用于覆盖远程报告、
容器环境或无法唯一判断的情况。

报告过于模糊时，可用 `--report-kind failure` 明确说明它是实际观察到的故障；`--report-kind question`
或 `idea` 会阻止按事件检索。这不是授权来源。如果已知精确的错误码、API 路径、源文件路径、模块或包名，
可重复使用 `--anchor KIND:VALUE`，拒绝不包含该事实的候选。锚点只会收窄候选，不会授权建议。

需要机器可读结果时使用 `--json`，需要查看候选和判断过程时使用 `--debug`。完整参数请运行
`uv run rt diagnose --help`。

## MCP 接口

项目提供只读 stdio MCP 服务，包含 `diagnose` 和 `get_evidence` 两个工具。

```bash
uv run repo-troubleshooter-mcp --check
uv run repo-troubleshooter-mcp
```

## 当前状态

目前已经跑通两个结构不同的真实仓库证据链：
[`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness) 使用
Discussions 证据链；[`vllm-project/vllm`](https://github.com/vllm-project/vllm) 使用
Issue → PR → Commit → Release 证据链。CLI 和 MCP 接口都可以使用。真实报告普查也表明，
自由文本提案仍可能指错事件，因此证据回显和用户确认不能省略。工具不会直接修改用户项目。

## 相关文档

- [当前状态](docs/status.md)
- [威胁模型](docs/threat_model.md)
- [已知限制](docs/known_bypasses.md)
