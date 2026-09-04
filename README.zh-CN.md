# Repository Troubleshooter

[English](README.md) | 简体中文

根据真实仓库证据，把一份错误报告、当前版本和运行环境转换成可验证的故障处理建议。

Repository Troubleshooter 会检索仓库中的讨论、发布版本、提交历史和文档，回答：

- 这是不是一个已知问题？
- 修复是否已经发布？
- 用户当前版本是否已经包含该改动？
- 下一步应该怎么做，有哪些证据支持？

## 使用示例

```text
输入
  错误：     启动后 client boot graph 为空
  当前版本： 0.1.2-alpha.1
  运行环境： Node.js 24.11.1 / Windows
  相关包：   @deepseek-ai/dsh-client-modules

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
uv run rt db init
```

PowerShell 请将复制命令改为 `Copy-Item .env.example .env`。如果本机已经通过
`gh auth login` 登录 GitHub，可以不填写 GitHub Token。

## 同步仓库数据

```bash
uv run rt profiles
uv run rt probe deepseek-ai/deepseek-harness
uv run rt sync deepseek-harness --max-discussions 200
uv run rt status deepseek-ai/deepseek-harness
```

同步过程可以重复执行，也可以中断后继续。`status` 会显示各类数据的更新时间和完整度。

## 诊断问题

将错误报告保存为 `report.txt`，然后运行：

```bash
uv run rt diagnose --repo deepseek-ai/deepseek-harness --error-file report.txt --version 0.1.2-alpha.1 --runtime "node 24.11.1" --os windows --package @deepseek-ai/dsh-client-modules
```

`--package` 表示用户认为发生故障的组件，可以重复传入。如果暂时无法确定故障包，可以省略，
先查看工具给出的候选事件、建议和证据，再决定是否确认。

需要机器可读结果时使用 `--json`，需要查看候选和判断过程时使用 `--debug`。完整参数请运行
`uv run rt diagnose --help`。

## MCP 接口

项目提供只读 stdio MCP 服务，包含 `diagnose` 和 `get_evidence` 两个工具。

```bash
uv run repo-troubleshooter-mcp --check
uv run repo-troubleshooter-mcp
```

## 当前状态

第一个使用真实数据验证的仓库是
[`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness)。CLI 和 MCP
接口现在都可以使用。工具只输出建议与证据，不会直接修改用户项目。第二个仓库仍在验证中。

## 相关文档

- [当前状态](docs/status.md)
- [威胁模型](docs/threat_model.md)
- [已知限制](docs/known_bypasses.md)
