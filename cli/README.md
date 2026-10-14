# aidevops

**AI DevOps / QA Copilot 的终端客户端**（CLI + 全屏 TUI）。

它的核心不是聊天机器人，而是**主动理解项目当前发生了什么**：把「需求 → 代码 → 测试 →
Bug → 发布」这条链路上原本孤立的研发数据关联起来，算出风险、生成测试、辅助定位问题。

- 一次提交的**完整关联链**一条命令拿到：`aidevops explain <sha>`
- 风险分数是**可解释的**：10 个信号各自贡献多少，逐条列出，不是黑盒
- AI 只**提议**，人确认后才执行（`approve` / `reject`），且全程可审计

## 安装

```bash
pipx install aidevops        # 推荐：独立虚拟环境，命令直接进 PATH
# 或者
pip install aidevops
```

升级用 `aidevops upgrade`。客户端每次运行都会比对 PyPI 上的最新版，落后了会用**红字**
提示；结果缓存 24 小时，设 `AIDEVOPS_NO_UPDATE_CHECK=1` 可关掉检查。

> `upgrade` 在 `pip install -e` 的开发检出处会**拒绝执行**（那会把工作区换成发布版），
> 提示你改用 `git pull`。

## 快速开始

```bash
aidevops login                       # GitHub 设备码登录（浏览器里输入码授权）
aidevops org-add "My Team"           # 首次使用：建一个组织（创建者即 admin）
aidevops projects                    # 列出项目（* 标出当前项目）
aidevops use qa-copilot-platform     # 选中它，后续命令默认作用于该项目
aidevops status                      # 当前后端 / 账号 / 项目
```

## 接入一个仓库

这条链路是产品的入口，缺任何一环后面的 `commits` / `risk` / `explain` 都是空态。

```bash
aidevops connections                                     # 看现有 Git 凭据，拿 connection id
aidevops connection-add -l my-github -t <PAT>            # 私有库需要 repo 读权限
aidevops connection-verify <connection-id>               # 先验证凭据能用
aidevops repo-add -c <connection-id> -f owner/repo       # 注册仓库
aidevops sync <repository-id> --wait                     # 拉取历史，跑完即用
```

## 常用命令

| 命令 | 作用 |
|---|---|
| `login` · `logout` · `me` · `status` | 会话（`login --no-browser` 适用于远程 / 容器） |
| `orgs` · `org-add` · `projects` · `use` · `unuse` | 组织与项目作用域 |
| `commits` · `commit <sha>` | 提交列表 / 详情 |
| `explain <sha>` | 完整关联链：需求、模块、回归候选、历史 Bug、发布、数据缺口 |
| `risk <sha>` · `module-risk <id>` | 风险分数 + 逐信号贡献分解 |
| `findings` · `finding <id>` · `triage <id> <status>` | AI 发现及其证据引用 |
| `proposals` · `approve <id>` · `reject <id>` | AI 建议与确认流：**只有 `approve` 会真正执行** |
| `analyse -a <code> [--wait]` · `analyses` · `trace <id>` | 发起分析、看历史、查一次分析背后的模型与工具调用 |
| `connections` · `connection-add` · `connection-verify` | 组织级 Git 凭据（管理员） |
| `repos` · `repo-add` · `repo-update` · `sync <id> [--wait]` | 仓库注册、同步窗口与模块深度 |
| `providers` · `provider-add` · `provider-verify` | 模型凭据（组织级，管理员） |
| `requirements` · `test-cases` · `test-runs` · `bugs` · `releases` | 只读台账 |
| `tui` | 全屏交互式仪表盘 |
| `upgrade` | 安装 PyPI 上的最新版本 |

## 脚本化

`--json` 是**全局**选项，放在子命令之前：

```bash
aidevops --json findings | jq '.[] | select(.severity == "critical")'
```

`--json` 模式下，升级提示走 **stderr**，不会污染管道里的 stdout。

## 环境变量

| 变量 | 作用 |
|---|---|
| `AIDEVOPS_BASE_URL` | 后端地址（默认 `http://127.0.0.1:8000/api/v1`） |
| `AIDEVOPS_CONFIG` | 配置文件路径（默认见下） |
| `AIDEVOPS_TOKEN` | 直接提供访问令牌，适合 CI |
| `AIDEVOPS_JSON=1` | 等价于全局 `--json` |
| `AIDEVOPS_NO_UPDATE_CHECK=1` | 关闭版本检查 |

配置与令牌存在用户配置目录（Windows `%LOCALAPPDATA%\aidevops\`，Linux `~/.config/aidevops/`），
文件权限 0600。

## 注意：这个包只是客户端

`aidevops` 本身不含任何服务端逻辑，它通过 HTTP 调用后端（Django + PostgreSQL + Redis）。
没有可用的后端时，`login` 第一步就会连不上。本地起后端的步骤见仓库文档。

## 开发

源码在 <https://github.com/zhoujungis/ai_devops> 的 `cli/` 目录下：

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy ai_devops_cli
```

## 许可证

MIT。

---

## English

**Terminal client (CLI + TUI) for AI DevOps / QA Copilot.**

It is not a chat bot. It correlates the data a team already produces along
*requirement -> code -> tests -> bugs -> release* and reports what is actually going
on: an explainable risk score, where a change is risky, what to regression-test, and
where an incident most likely came from.

- `aidevops explain <sha>` walks the full correlation chain for one commit.
- The risk score is **explainable**: ten signals, each with its own contribution.
- AI only *proposes*; a human confirms (`approve` / `reject`), and everything is audited.

### Install

```bash
pipx install aidevops
```

Upgrade with `aidevops upgrade`; the client checks PyPI on each run and says so in red
when it is behind. Set `AIDEVOPS_NO_UPDATE_CHECK=1` to switch that off.

### Quick start

```bash
aidevops login                       # GitHub device-code sign-in
aidevops org-add "My Team"
aidevops projects
aidevops use <project-slug>
aidevops status
```

This package is a **client only** -- it calls a Django + PostgreSQL + Redis backend over
HTTP. Without a reachable backend, `login` fails on the first request. See the repository
for how to run one locally.

## License

MIT.
