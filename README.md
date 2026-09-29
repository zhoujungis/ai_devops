# AI DevOps / QA Copilot

**[中文](#中文) | [English](#english)**

---

<a id="中文"></a>

# 中文

一个理解「需求 → 代码 → 测试 → Bug → 发布 → 线上问题」完整研发链路的 AI 工作平台。核心不是聊天机器人，而是**主动理解项目当前发生了什么**：把原本孤立的研发数据关联起来，发现风险、生成测试、辅助定位问题。

## 核心能力

### 1. 研发数据关联引擎

- **GitHub 仓库同步**：REST API 增量 + 历史回填双游标，预算截断后可安全续跑；限流退避重试
- **Webhook 实时摄取**：HMAC-SHA256 验签 + `delivery_id` 幂等，push 事件即时增量入库；beat 另按 `GIT_SYNC_INTERVAL_MINUTES` 补一轮同步，兜住漏投或未配 webhook 的仓库
- **模块归属解析**：覆盖规则 > 源根推断 > 兜底策略，每种归属都带可引用的理由（`reason`）
- **全链路可解释**：`explain` 端点返回一条提交的完整关联链 —— 命中的需求、模块影响、回归测试候选、历史 Bug、所在发布
- **关联自动落库**：提交信息里写到的需求号（`PAY-18`）与缺陷号（`QAC-201`）在同步时自动建链，并把提交碰到的模块、它服务的需求一并带到缺陷上；测试运行中通过的结果也会给它对应的提交打上「已验证」边。全部记 `LinkSource.INFERRED`，与人工声明区分得开 —— 这是回归候选与 `historical_bug_density` 信号的唯一数据来源
- **诚实的数据边界**：`data_gaps` 显式区分「没有发现问题」和「我们没有数据」，空结果不会被误读成"一切正常"

### 2. 风险引擎

针对一次提交或一个模块，计算归一化风险分数，并**逐信号给出贡献分解**：

| 信号 | 含义 |
|---|---|
| `change_volume` | 变更规模（churn 行数，饱和归一化） |
| `historical_bug_density` | 模块历史上关联过的缺陷数 |
| `test_failure_rate` | 覆盖该模块的测试最近失败率 |
| `module_centrality` | 模块在架构中的中心度 |
| `change_frequency` | 近期变更频次（滑动窗口） |
| `coverage_gap` | 行覆盖缺口 |
| `open_high_severity_bugs` | 未解决的 S1/S2 缺陷 |
| `test_coverage_gap` | 用例覆盖薄弱的模块 |
| `release_proximity` | 距离下次发布的时间压力 |
| `data_freshness_penalty` | 数据陈旧度惩罚（数据不可信时降低置信） |

- **权重归一化**：报告权重恒为 100 份额，每个分数可分解为逐信号贡献，**贡献之和精确等于分数**
- **规则三级继承**：系统默认 → 组织覆盖 → 项目覆盖
- **不用发版就能调参**：权重是配置不是代码，在 Django admin 的 Risk rules 里改（`/admin/`），改动记录到 `updated_by`
- **中心度是算出来的**：每次同步结束按「触及该模块的提交占比」重算并归一化，`module_centrality` 不依赖人工填数
- 信号缺失时显式标注，而不是静默按 0 处理

### 3. AI 分析（Provider 可插拔）

- **Provider Adapter**：OpenAI 兼容协议 + DeepSeek / Qwen / Ollama 预设；凭据组织级管理，Fernet 加密落库；可用 `copilot provider-add` 直接在终端配置
- **内置 Agent**：
  - **Code Impact** —— 一次提交影响什么，复用关联引擎
  - **Test Generation** —— 11 类场景覆盖矩阵（正常 / 边界 / 异常 / 并发等），强制逐类给结论
  - **Bug Investigation** —— 基于 Bug 出现轨迹与关联链调查根因
  - **Root Cause Analysis（`rca`）** —— 复用 Bug 调查链，输出按置信度排序的候选根因与时间线，不给你一个"根因就是它"的断言
  - **Requirement Analysis** —— 需求与模块/用例的关联推断
- **证据校验**：AI 引用必须能解析到本项目真实数据，否则丢弃并记录原因
- **事实 / 假设 / 证据强制分离**的 schema 约束（`StrictModel` 拒绝多余字段）
- **版本化 Prompt 注册表**：frontmatter 绑定输出 schema，禁止硬编码
- **全链路追踪**：`AIAnalysisRun` / `AIToolCall` 记录 prompt 版本、tokens、延迟、digest；成本记账（cached token 单独计价，未定价记 `NULL` 而非 0，参考价在 Django admin 的 `AIModelPricing` 里维护）。可用 `copilot trace <job_id>`（或 `GET .../ai/jobs/{id}/trace`）把一次分析的每次模型调用与工具调用翻出来
- **异步作业**：`POST` 返回 `202 + job_id`，客户端轮询；幂等键防重复提交；永久/瞬时失败区分处理
- **两段式工具暴露**：模型 function-calling 列表只有 13 个只读工具，全部强制按 project 作用域过滤；写操作不进模型。工具循环上限由 `AI_ANALYSIS_MAX_TOOL_ITERATIONS` 约束，超限就让模型作答而不是把作业判失败

### 4. 确认流（AI 不直接改状态）

- AI 产出「建议对象」，用户**确认后**才由注册的 Executor 执行，全程写 `AuditLog`
- Executor 集合由代码固定，模型只能指名已有动作，不能发明执行路径
- 未确认 = 零写入；越权工具调用记 `scope_denied` 留痕而非静默
- 重复分析更新已有 finding（dedupe），不无限堆积
- AI 发现的处置状态（`acknowledged` / `dismissed` / `converted`）是它唯一可由人改动的字段，改动同样进审计
- 审计流水有只读 API（`/orgs/{org_pk}/audit-logs`，仅 admin）与 `copilot audit`，不必进数据库 shell 查「这是谁确认的」

## 架构与技术栈

```
┌────────────┐   REST/JSON + JWT   ┌──────────────────────────────┐
│  Terminal  │ ──────────────────► │  Backend (Django + DRF)      │
│ copilot    │                     │  ├─ 10 个领域 app             │
│ CLI + TUI  │                     │  ├─ CorrelationService 关联引擎│
└────────────┘                     │  ├─ 风险引擎 + AI Agent 管线   │
                                   │  └─ Celery 异步作业           │
                                   └──────┬───────────┬───────────┘
                                          │           │
                              PostgreSQL + pgvector   Redis
                                                      (broker/cache)
```

| 层 | 技术 |
|---|---|
| Backend | Python 3.13 · Django 5 · Django REST Framework · Celery |
| 数据库 | PostgreSQL + pgvector 0.8 · Redis 8（broker / cache） |
| AI | OpenAI 兼容 Provider Adapter · Pydantic 严格结构化输出 · 版本化 Prompt · 只读 Tool 层 |
| 客户端 | Python · Typer（命令式 CLI）· Textual（TUI）· Rich · httpx（JWT 自动刷新） |
| 质量 | pytest（559 个后端测试）· 终端客户端测试（36，含 Textual pilot）· ruff · black · mypy（strict + django/drf 插件）· drf-spectacular（OpenAPI 零告警） |
| 部署 | docker compose（web / worker / beat / db / redis）· GitHub Actions CI |

## 目录结构

```
backend/
  pyproject.toml            依赖 + ruff/black/mypy/pytest 配置（唯一来源）
  manage.py
  config/
    settings/{base,dev,prod,test}.py
    celery.py  urls.py  asgi.py  wsgi.py
  apps/
    core/                   基类模型、Fernet 加密字段、分页、错误信封、
                            request_id 中间件、健康探针
      scoping.py            ScopedModel 契约 + scoped_queryset 租户过滤唯一入口
    accounts/               用户、组织、项目、成员、角色、RBAC
    integrations/           GitProvider 抽象 + GitHub 实现、GitConnection /
                            Repository / WebhookEvent、双游标同步服务
    codebase/               Branch / Commit / CommitFile / Module 及
                            ModuleResolver 模块解析
    requirements/           Requirement / RequirementItem / ModuleRequirementLink
    testing/                TestCase / TestSuite / TestRun / TestResult /
                            CoverageSnapshot + 关联表
    bugs/                   Bug / BugOccurrence / BugRelation + 证据关联表
    releases/               Release / ReleaseCommitLink
    ai/                     Provider Adapter、结构化输出、prompt 注册表、
                            Tool 层、AIAnalysisJob / AIAnalysisRun / AIToolCall、
                            Agent（code_impact / bug_investigation / requirement /
                            test_generation / rca）与确认流模型
    risk/                   RiskRule 配置 + 10 信号风险引擎（分数现算、不落库）
  services/
    correlation.py          CorrelationService.explain_commit —— 关联链路唯一实现
    executors.py            Executor 注册表（确认流执行器）
    linking.py              跨实体边（source + confidence）
  tests/integration/       test_s3_api / test_ai_api / test_correlation_chain /
                           test_github_live
cli/
  pyproject.toml            依赖 + ruff/mypy 配置；console script 名 `copilot`
  ai_devops_cli/
    config.py               配置与 token（platformdirs，0600）
    client.py               httpx 客户端 + JWT 自动刷新 + 错误信封解包 + 分页
    context.py              解析当前组织 / 项目作用域
    output.py               Rich 表格 / JSON 输出
    main.py                 Typer 根命令与错误处理
    commands/               auth / scope / code / ai / repos / tracker
    tui/app.py              Textual 交互式仪表盘
docker/
  backend.Dockerfile       backend 容器镜像
  initdb/                  pgvector 建扩展脚本（compose db 初始化挂载）
docker-compose.yml         pgvector + redis + web + worker + beat
.github/workflows/ci.yml   lint + type + check + migrations + tests
Makefile                   跨平台命令入口（Windows / Linux 自动切换 venv 路径）
```

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
cp .env.example .env    # 至少填 DJANGO_SECRET_KEY 和 FIELD_ENCRYPTION_KEY
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser   # 可选
```

- API：`http://127.0.0.1:8000`
- Swagger：`http://127.0.0.1:8000/api/docs/`
- 探针：`/healthz`（存活，不碰依赖）、`/readyz`（就绪，检查 PostgreSQL + pgvector + Redis）

终端客户端 `copilot`（CLI + TUI）：

```bash
cd cli
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"        # Windows；Linux/macOS 用 .venv/bin/pip
.venv/Scripts/copilot login                  # 交互输入邮箱和密码
.venv/Scripts/copilot projects               # 列出项目
.venv/Scripts/copilot use qa-copilot-platform
.venv/Scripts/copilot findings               # AI 发现
.venv/Scripts/copilot explain <sha>          # 一次提交的完整关联链
.venv/Scripts/copilot risk <sha>             # 逐信号风险分解
.venv/Scripts/copilot tui                    # 全屏交互式仪表盘
```

`--json` 是全局选项，放在子命令之前：`copilot --json findings`（或设 `COPILOT_JSON=1`）。
配置与 token 存在用户配置目录下的 `ai-devops-copilot/config.json`（权限 0600）。

### 方式二：本地开发

```bash
cp .env.example .env
python -m venv backend/.venv
make install          # 或手动: pip install -e "backend[dev]"
make migrate
make run              # Django dev server
make worker           # 另开终端: Celery worker（-Q ai,sync,default）
make beat             # 另开终端: Celery beat
```

Celery 队列路由：`ai.tasks.* → ai`、`integrations.tasks.* → sync`，其余走 `default`。

> **Windows + WSL2 用户**：如果 PostgreSQL / Redis 跑在 WSL 内而 Windows 原生进程连不上（NAT 端口转发不可用），可在 `%USERPROFILE%\.wslconfig` 的 `[wsl2]` 段启用 `networkingMode=mirrored` 后 `wsl --shutdown`；或者把后端整体跑在 WSL / docker compose 里。
> 注意 `docker-compose.yml` 使用 `pgvector/pgvector:pg16` 镜像，与本机手装的 PostgreSQL 版本可能不同，容器环境以 compose 为准。

## 终端客户端

`copilot` 是唯一的客户端：命令式 CLI 用于脚本与管道，`copilot tui` 是全屏仪表盘。

### 常用流程

```bash
copilot login                       # 交互输入邮箱/密码，token 存本地
copilot projects                    # 列出所有组织下的项目（* 标出当前项目）
copilot use qa-copilot-platform     # 选中项目，后续命令默认作用于它
copilot status                      # 当前后端 / 账号 / 项目
```

### 命令一览

| 命令 | 作用 |
|---|---|
| `login` · `logout` · `me` · `status` | 会话 |
| `orgs` · `projects` · `use` · `unuse` | 组织与项目作用域 |
| `commits` · `commit <sha>` | 提交列表 / 详情（含模块归属与文件） |
| `explain <sha>` | 一条提交的完整关联链：需求、模块、回归候选、历史 Bug、发布、data gaps |
| `risk <sha>` · `module-risk <id>` | 风险分数 + 逐信号贡献分解 |
| `modules` | 模块列表 |
| `findings` · `finding <id>` · `triage <id> <status>` | AI 发现（含证据引用）与处置 |
| `proposals` · `approve <id>` · `reject <id>` | AI 建议与确认流：**只有 approve 会真正执行**，且复用同一套 Executor 与审计 |
| `agents` · `analyse -a <code> [--wait]` · `analyses` · `job <id>` · `trace <id>` | 发起分析、查看历史、轮询作业，以及查看一次分析背后的模型调用与工具调用 |
| `connections` · `connection-add` · `connection-verify` · `connection-delete` | 组织级 Git 凭据（管理员） |
| `repos` · `repo-add` · `repo-update` · `repo-remove` · `sync <id> [--wait]` | 仓库注册、调整窗口 / 模块深度、同步 |
| `providers` · `provider-add` · `provider-verify` · `provider-remove` | 模型凭据（组织级，仅管理员）；`provider-verify` 在跑分析之前先把密钥验一遍 |
| `requirements` · `test-cases` · `test-runs` · `bugs` · `releases` | 只读台账 |
| `audit` | 审计流水（组织级，仅管理员） |
| `tui` | 全屏交互式仪表盘（Proposals / Findings / Commits） |

### 约定

- `--json` 是**全局**选项，放在子命令之前：`copilot --json findings`（也可用 `COPILOT_JSON=1`），方便直接管道给 `jq`。
- 已开启 shell 补全：`copilot --install-completion`（bash / zsh / fish / PowerShell）。
- 配置与 token 存在用户配置目录（Windows `%LOCALAPPDATA%`、Linux `~/.config`、macOS `~/Library/Application Support`）下的 `ai-devops-copilot/config.json`，写入权限 0600。
- 环境变量：`COPILOT_CONFIG`（换配置路径）、`COPILOT_BASE_URL`（指向远程后端）、`COPILOT_TOKEN`（CI 里免登录）。
- 失败统一为「一行文案 + 退出码 1」，并附带响应的 `code` / `status` / `request_id`，便于和后端日志对齐。
- TUI 快捷键：`r` 刷新、`a` 确认、`x` 拒绝、`q` 退出。

## 配置

复制 `.env.example` 为 `.env`，关键项：

| 变量 | 说明 |
|---|---|
| `DJANGO_SECRET_KEY` | 生产环境必须更换 |
| `FIELD_ENCRYPTION_KEY` | Fernet 密钥，加密 git token / AI key 等字段。生成：`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DB_*` | PostgreSQL 连接（需 pgvector 扩展：`CREATE EXTENSION vector;`） |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis 与 Celery（示例里分用 db 0/1/2） |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` / `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | 默认 15 分钟 / 7 天 |
| `CORS_ALLOWED_ORIGINS` | 允许的浏览器源（Swagger UI 等；终端客户端不需要），默认含 `localhost:5173` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `DEEPSEEK_API_KEY` / `QWEN_API_KEY` / `OLLAMA_BASE_URL` | AI Provider 凭据（也可运行后在 `ai-providers` API 里按组织配置，推荐后者） |

## API 概览

### 通用约定

- 前缀 `/api/v1/`，JWT 认证（access 15min / refresh 7d，refresh 走黑名单登出），除探针与 webhook 外默认 `IsAuthenticated`
- 错误响应统一信封：`{"error": {"code", "message", "details", "request_id"}}`
- 所有响应带 `X-Request-ID`，入站同名头会被透传，便于与审计记录对齐
- AI 分析一律异步：`POST` 返回 `202 + job_id`，`GET` 轮询状态
- `?ordering=` 对枚举列按**语义**排序而非字母序（如 findings 的 `severity` 从 critical 到 info、需求 `status` 按生命周期），其余列照常
- 交互式文档：**`/api/docs/`**（Swagger），OpenAPI schema：`/api/schema/`

### 认证与组织

| 方法 | 路径 | 最低角色 |
|---|---|---|
| POST | `/api/v1/auth/register` · `/auth/login` · `/auth/refresh` · `/auth/logout` | — |
| GET/PATCH | `/api/v1/me` | 已登录 |
| GET/POST | `/api/v1/orgs` | 已登录（创建组织者自动成为 admin） |
| GET/PUT/PATCH/DELETE | `/api/v1/orgs/{org_pk}` | viewer / admin |
| GET/POST | `/api/v1/orgs/{org_pk}/members` | viewer / admin |
| GET/PATCH/DELETE | `/api/v1/orgs/{org_pk}/members/{user_id}` | viewer / admin |
| GET/POST | `/api/v1/orgs/{org_pk}/projects` | viewer / pm |
| GET/PUT/PATCH/DELETE | `/api/v1/orgs/{org_pk}/projects/{project_pk}` | viewer / pm / admin |
| GET/POST | `.../projects/{project_pk}/members` | viewer / pm |
| GET/PATCH/DELETE | `.../projects/{project_pk}/members/{user_id}` | viewer / pm |

### Git 集成

| 方法 | 路径 | 最低角色 |
|---|---|---|
| GET/POST | `/api/v1/orgs/{org_pk}/git-connections` | admin（凭据仅 admin 可见） |
| GET/PATCH/DELETE | `/api/v1/orgs/{org_pk}/git-connections/{connection_pk}` | admin |
| POST | `.../git-connections/{connection_pk}/verify` | admin |
| GET/POST | `.../projects/{project_pk}/repositories` | viewer / pm |
| GET/PATCH/DELETE | `.../repositories/{repository_pk}` | viewer / pm / admin |
| POST | `.../repositories/{repository_pk}/sync` | developer（返回 202 + task_id；已在运行时 409 `sync_in_progress`） |
| GET | `.../projects/{project_pk}/commits` · `/commits/{commit_pk}` | viewer |
| GET | `.../projects/{project_pk}/modules` · `/modules/{module_pk}` | viewer |
| POST | `/api/v1/webhooks/github/{connection_pk}` | **无认证**，靠 HMAC 验签 |

`commits` 支持 `?repository=` `?author_email=` `?since=` `?until=` `?search=` `?ordering=`。

### 需求 / 测试 / Bug / 发布

| 方法 | 路径 | 允许的角色 |
|---|---|---|
| GET/POST | `.../requirements` | viewer / {admin, pm, developer} |
| GET/PUT/PATCH/DELETE | `.../requirements/{requirement_pk}` | viewer / {admin, pm, developer} |
| GET/POST | `.../test-cases` · `.../test-suites` · `.../test-runs` | viewer / {admin, developer, qa} |
| GET/PUT/PATCH/DELETE | `.../test-cases/{test_case_pk}` 等 | viewer / {admin, developer, qa} |
| GET/POST | `.../coverage-snapshots` | viewer / {admin, developer, qa}（只增不改） |
| GET/POST | `.../bugs` | viewer / {admin, pm, developer, qa} |
| GET/PUT/PATCH/DELETE | `.../bugs/{bug_pk}` | viewer / {admin, pm, developer, qa} |
| GET/POST | `.../releases` | viewer / {admin, pm} |
| GET/PUT/PATCH/DELETE | `.../releases/{release_pk}` | viewer / {admin, pm} |
| **GET** | **`.../commits/{commit_pk}/explain`** | viewer |

角色矩阵**不是层级**的：QA 能改用例但 PM 不能，PM 能发版但开发者不能。`RoleRequired` 同时支持「最低角色」和「显式角色集合」两种形式。

### 关联解释 `explain`

```jsonc
{
  "commit": {"sha": "abc123", "message": "PAY-18 retry payment"},
  "modules": [{"module": {"path_prefix": "src/payment"}, "weight": 1.0, "churn_lines": 16}],
  "requirement": {"external_key": "PAY-18"}, "requirement_source": "commit",
  "regression_candidates": [{"test_case": {"key": "TC-002"}, "score": 0.85,
                             "reasons": ["covers module src/payment",
                                         "verified a commit in the same modules"]}],
  "historical_bugs": [{"bug": {"key": "BUG-1023"}, "shared_modules": ["src/payment"]}],
  "releases": [{"version": "2026.09"}],
  "data_gaps": ["No open bugs are linked to the touched modules."]
}
```

### AI 与风险

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `.../ai/analyses` | 提交 AI 分析，返回 **202 + job_id** |
| GET | `.../ai/analyses` · `.../ai/analyses/agents` | 分析历史 / 可用 Agent 注册表 |
| GET | `.../ai/jobs/{job_pk}` | 轮询异步作业状态与结果 |
| GET | `.../ai/jobs/{job_pk}/trace` | 一次作业背后的模型调用与工具调用（prompt 版本、tokens、延迟、scope_denied） |
| GET | `.../ai/findings[/{finding_pk}]` | AI 发现（重复分析去重更新） |
| POST | `.../ai/findings/{finding_pk}/status` | 处置 AI 发现：new / acknowledged / dismissed / converted（写审计） |
| GET | `.../ai/recommendations[/{recommendation_pk}]` | AI 建议 |
| POST | `.../ai/recommendations/{recommendation_pk}/confirm` · `/reject` | 确认 / 拒绝（确认才由 Executor 执行并写审计） |
| GET/POST/PATCH/DELETE | `/api/v1/orgs/{org_pk}/ai-providers[/{provider_pk}]` | 模型凭据管理（组织级，仅 admin） |
| POST | `.../ai-providers/{provider_pk}/verify` | 校验密钥并回写 status / last_verified_at（组织级，仅 admin） |
| GET | `/api/v1/orgs/{org_pk}/audit-logs[/{audit_pk}]` | 审计流水（组织级，仅 admin，只读） |
| GET | `.../commits/{commit_pk}/risk` · `.../modules/{module_pk}/risk` | 风险评分 + 逐信号贡献分解 |

AI 分析流程固定为三步：`POST analyses`（带 `agent` + 目标实体）→ 轮询 `GET jobs/{id}` → 读 `findings`。

## 多租户与安全设计

- **租户 id 只从 URL 取，永远不从请求体取**；`org` / `project` 字段对序列化器不可写，请求体无法把数据放进调用者不拥有的租户
- 失败语义刻意分两种：不是该租户的成员 → **404**（不泄露存在性）；是成员但角色不足 → **403**（资源可见，仅无权操作）
- 所有模型查询走 `scoped_queryset` 唯一入口，租户过滤不可绕过
- 敏感字段（git token、AI key）Fernet 加密落库，序列化层仅 admin 可见
- Webhook 无认证端点靠 HMAC-SHA256 验签 + `delivery_id` 幂等
- 状态变更一律走确认流 + `AuditLog`；越权工具调用记 `scope_denied`
- AI 输出里的证据引用必须解析到本项目的真实行，解析失败即丢弃并记录原因

## 开发与测试

```bash
make verify    # ruff + black --check + mypy + django check + pytest（与 CI 同一套）
make fmt       # black + ruff --fix
make cov       # 带覆盖率跑测试
make cli-verify  # 终端客户端：ruff + black --check + mypy + pytest
```

- **559 个后端测试**全部不依赖外网：`FakeGitProvider`、脚本化假 AI Provider、`httpx.MockTransport`，默认 `-m 'not network'`
- **36 个终端客户端测试**：客户端用 `httpx.MockTransport` 覆盖 401 刷新/分页/错误信封；命令层用 Typer `CliRunner`；TUI 用 Textual 的 `run_test` pilot 真跑（挂载、填表、确认/拒绝打到接口）
- S6 验收逐条有测试锁定：未确认不建行 / 确认精确创建 + 审计 / 拒绝零写入 / 越权记 `scope_denied`
- `tests/integration/test_github_live.py` 的 4 个用例会真实访问 `api.github.com`（匿名 GET，仓库 `octocat/Hello-World`），手动执行：

  ```bash
  cd backend && python -m pytest -m network -q
  ```

  一次运行约 20 次请求，计入出口 IP 的匿名额度（60 次/小时）；设置 `GITHUB_TOKEN` 可提升到 5000 次/小时。

- pytest 配置要点：`python_classes = []`（领域模型叫 `TestCase`，避免 pytest 误收集）、`testpaths = ["tests", "apps"]`、strict markers
- CI（GitHub Actions）：lint + type + `manage.py check` + `makemigrations --check` + migrations + tests

## Roadmap

- [ ] 终端客户端补完：需求 / 用例 / Bug / 发布 详情视图、提交 diff 查看、在 TUI 里直接发起分析并浏览结果
- [ ] 真实 LLM 端到端 smoke test
- [ ] Celery worker 生产形态实跑验证

---

<a id="english"></a>

# English

An AI work platform that understands the full engineering chain — **requirements → code → tests → bugs → releases → production issues**. It is not a chatbot: it proactively understands what is happening in your project, correlating previously siloed engineering data to surface risks, generate tests, and help investigate problems.

## Key Capabilities

### 1. Engineering Data Correlation Engine

- **GitHub repository sync**: dual-cursor REST sync (incremental + historical backfill), safely resumable after budget truncation, with rate-limit backoff
- **Real-time webhooks**: HMAC-SHA256 signature verification + `delivery_id` idempotency; push events ingested immediately — and Beat runs a reconciliation pass every `GIT_SYNC_INTERVAL_MINUTES` for repositories whose delivery was missed or that have no webhook configured
- **Module attribution**: override rules > source-root inference > fallback, each attribution carrying a citable `reason`
- **Explainable chain**: the `explain` endpoint returns the full correlation chain for a commit — matched requirement, module impact, regression test candidates, historical bugs, releases involved
- **Links are written, not assumed**: a requirement key (`PAY-18`) or defect key (`QAC-201`) named in a commit message is linked during sync, carrying the commit's modules and the requirement it serves across to the defect; a passing result in a test run tied to a commit marks that commit as verified by the case. Everything is stored as `LinkSource.INFERRED`, distinguishable from a human declaration — and it is the only source of data for regression candidates and the `historical_bug_density` signal
- **Honest data boundaries**: `data_gaps` explicitly distinguishes "no problems found" from "we have no data", so empty results are never mistaken for "all clear"

### 2. Risk Engine

Computes a normalized risk score for a commit or a module, with a **per-signal contribution breakdown**:

| Signal | Meaning |
|---|---|
| `change_volume` | Change size (churn lines, saturating normalization) |
| `historical_bug_density` | Defects ever linked to the touched modules |
| `test_failure_rate` | Recent failure rate of tests covering the modules |
| `module_centrality` | Architectural centrality of the modules |
| `change_frequency` | Recent change frequency (sliding window) |
| `coverage_gap` | Line-coverage gap |
| `open_high_severity_bugs` | Unresolved S1/S2 defects |
| `test_coverage_gap` | Modules with thin test coverage |
| `release_proximity` | Time pressure toward the next release |
| `data_freshness_penalty` | Staleness penalty (lowers confidence when data is old) |

- **Normalized weights**: report weights always sum to 100 shares and **contributions sum exactly to the score**
- **Three-level rule inheritance**: system defaults → organization overrides → project overrides
- **Retunable without a deploy**: weights are configuration, not code — edit them in the Django admin under Risk rules (`/admin/`), and every change is attributed via `updated_by`
- **Centrality is computed, not entered**: at the end of every sync, each module's score is derived from the share of commits that touched it and normalised, so `module_centrality` cannot drift from the code
- Missing signals are labeled explicitly instead of silently counting as zero

### 3. AI Analysis (Pluggable Providers)

- **Provider adapter**: OpenAI-compatible protocol with DeepSeek / Qwen / Ollama presets; credentials managed per organization, encrypted at rest with Fernet, and configurable from the terminal with `copilot provider-add`
- **Built-in agents**:
  - **Code Impact** — what a commit affects, reusing the correlation engine
  - **Test Generation** — an 11-category scenario coverage matrix (happy path, boundary, error, concurrency, …), forced to conclude on every category
  - **Bug Investigation** — root-cause investigation from occurrence trajectories and correlation chains
  - **Root Cause Analysis (`rca`)** — reuses the bug investigation chain to rank candidate causes with a timeline, instead of handing you "the root cause is X"
  - **Requirement Analysis** — requirement ↔ module/test-case correlation inference
- **Evidence validation**: every AI citation must resolve to real data in this project, otherwise it is dropped and the reason recorded
- **Fact / hypothesis / evidence separation** enforced by strict schemas (`StrictModel` rejects extra fields)
- **Versioned prompt registry**: frontmatter binds each prompt to its output schema — no hardcoded prompts
- **Full tracing**: `AIAnalysisRun` / `AIToolCall` record prompt version, tokens, latency, digest; cost accounting prices cached tokens separately and stores `NULL` (not 0) for unpriced models — reference prices live in `AIModelPricing` in the Django admin. `copilot trace <job_id>` (or `GET .../ai/jobs/{id}/trace`) lays out every model run and tool call behind one analysis
- **Async jobs**: `POST` returns `202 + job_id`, clients poll; idempotency keys prevent duplicates; permanent vs transient failures distinguished
- **Two-stage tool exposure**: the model's function-calling list contains only 13 read-only tools, all forcibly project-scoped; write tools are never exposed to the model. The loop is bounded by `AI_ANALYSIS_MAX_TOOL_ITERATIONS`, and exceeding it asks the model to answer rather than failing the job

### 4. Confirmation Flow (AI never mutates state directly)

- AI produces **recommendations**; only after explicit user confirmation does a registered Executor act, with a full `AuditLog`
- The Executor set is fixed in code — the model can only name existing actions, never invent execution paths
- No confirmation = zero writes; unauthorized tool calls are logged as `scope_denied`, never silenced
- Repeated analyses update existing findings (dedupe) instead of piling up
- A finding's triage status (`acknowledged` / `dismissed` / `converted`) is the only field a human may change about it, and the change is audited like any other
- The audit trail has a read-only API (`/orgs/{org_pk}/audit-logs`, admin only) and a `copilot audit` command, so "who confirmed this" needs no database shell

## Architecture & Stack

```
┌────────────┐   REST/JSON + JWT   ┌──────────────────────────────┐
│  Terminal  │ ──────────────────► │  Backend (Django + DRF)      │
│ copilot    │                     │  ├─ 10 domain apps           │
│ CLI + TUI  │                     │  ├─ CorrelationService engine│
└────────────┘                     │  ├─ Risk engine + AI agents  │
                                   │  └─ Celery async jobs        │
                                   └──────┬───────────┬───────────┘
                                          │           │
                              PostgreSQL + pgvector   Redis
                                                      (broker/cache)
```

| Layer | Technology |
|---|---|
| Backend | Python 3.13 · Django 5 · Django REST Framework · Celery |
| Data | PostgreSQL + pgvector 0.8 · Redis 8 (broker / cache) |
| AI | OpenAI-compatible provider adapter · strict Pydantic structured output · versioned prompts · read-only tool layer |
| Client | Python · Typer (imperative CLI) · Textual (TUI) · Rich · httpx (auto JWT refresh) |
| Quality | pytest (559 backend tests) · terminal-client tests (36, including Textual pilot) · ruff · black · mypy (strict + django/drf plugins) · drf-spectacular (zero-warning OpenAPI) |
| Deployment | docker compose (web / worker / beat / db / redis) · GitHub Actions CI |

## Repository Layout

```
backend/
  pyproject.toml            deps + ruff/black/mypy/pytest config (single source)
  manage.py
  config/
    settings/{base,dev,prod,test}.py
    celery.py  urls.py  asgi.py  wsgi.py
  apps/
    core/                   base models, Fernet-encrypted fields, pagination,
                            error envelope, request_id middleware, health probes
      scoping.py            ScopedModel contract + scoped_queryset (the only
                            tenant-filter entry point)
    accounts/               users, organizations, projects, memberships, RBAC
    integrations/           GitProvider abstraction + GitHub impl, GitConnection /
                            Repository / WebhookEvent, dual-cursor sync service
    codebase/               Branch / Commit / CommitFile / Module + ModuleResolver
    requirements/           Requirement / RequirementItem / ModuleRequirementLink
    testing/                TestCase / TestSuite / TestRun / TestResult /
                            CoverageSnapshot + link tables
    bugs/                   Bug / BugOccurrence / BugRelation + evidence tables
    releases/               Release / ReleaseCommitLink
    ai/                     provider adapter, structured output, prompt registry,
                            tool layer, AIAnalysisJob / AIAnalysisRun / AIToolCall,
                            agents (code_impact / bug_investigation / requirement /
                            test_generation / rca) + confirmation-flow models
    risk/                   RiskRule config + 10-signal engine (scores computed, never stored)
  services/
    correlation.py          CorrelationService.explain_commit — the single
                            implementation of the correlation chain
    executors.py            Executor registry (confirmation-flow executors)
    linking.py              cross-entity edges (source + confidence)
  tests/integration/        test_s3_api / test_ai_api / test_correlation_chain /
                            test_github_live
cli/
  pyproject.toml            deps + ruff/mypy config; console script `copilot`
  ai_devops_cli/
    config.py               settings + tokens (platformdirs, 0600)
    client.py               httpx client + auto JWT refresh + error envelope + paging
    context.py              resolves the current org / project scope
    output.py               Rich tables / JSON output
    main.py                 Typer root command and error handling
    commands/               auth / scope / code / ai / repos / tracker
    tui/app.py              Textual interactive dashboard
docker/
  backend.Dockerfile        backend image
  initdb/                   pgvector extension bootstrap (mounted by compose db)
docker-compose.yml          pgvector + redis + web + worker + beat
.github/workflows/ci.yml    lint + type + check + migrations + tests
Makefile                    cross-platform command entry (auto Windows/Linux venv)
```

## Quick Start

### Option 1: Docker Compose (recommended)

```bash
cp .env.example .env    # at minimum set DJANGO_SECRET_KEY and FIELD_ENCRYPTION_KEY
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser   # optional
```

- API: `http://127.0.0.1:8000`
- Swagger: `http://127.0.0.1:8000/api/docs/`
- Probes: `/healthz` (liveness, no dependencies touched), `/readyz` (readiness — checks PostgreSQL + pgvector + Redis)

Terminal client `copilot` (CLI + TUI):

```bash
cd cli
python -m venv .venv
.venv/bin/pip install -e ".[dev]"            # Windows: .venv\Scripts\pip
.venv/bin/copilot login                      # prompts for email + password
.venv/bin/copilot projects                   # list projects
.venv/bin/copilot use qa-copilot-platform
.venv/bin/copilot findings                   # AI findings
.venv/bin/copilot explain <sha>              # full correlation chain for a commit
.venv/bin/copilot risk <sha>                 # per-signal risk breakdown
.venv/bin/copilot tui                        # full-screen dashboard
```

`--json` is a global option placed before the subcommand: `copilot --json findings`
(or set `COPILOT_JSON=1`). Settings and tokens live in the user config directory
under `ai-devops-copilot/config.json` (mode 0600).

### Option 2: Local development

```bash
cp .env.example .env
python -m venv backend/.venv
make install          # or manually: pip install -e "backend[dev]"
make migrate
make run              # Django dev server
make worker           # separate terminal: Celery worker (-Q ai,sync,default)
make beat             # separate terminal: Celery beat
```

Celery queue routing: `ai.tasks.* → ai`, `integrations.tasks.* → sync`, everything else on `default`.

> **Windows + WSL2 users**: if PostgreSQL/Redis run inside WSL and native Windows processes cannot reach them (NAT port forwarding unavailable), enable `networkingMode=mirrored` in the `[wsl2]` section of `%USERPROFILE%\.wslconfig` and run `wsl --shutdown`; or run the backend entirely in WSL / docker compose.
> Note that `docker-compose.yml` uses the `pgvector/pgvector:pg16` image, which may differ from a manually installed PostgreSQL — the container environment follows compose.

## Terminal Client

`copilot` is the only client: an imperative CLI for scripting and pipes, plus a
full-screen dashboard via `copilot tui`.

### Typical flow

```bash
copilot login                       # prompts for email + password; stores the token
copilot projects                    # every project across your orgs (* marks the current one)
copilot use qa-copilot-platform     # select it; later commands act on it by default
copilot status                      # backend / account / project in effect
```

### Command reference

| Command | What it does |
|---|---|
| `login` · `logout` · `me` · `status` | session |
| `orgs` · `projects` · `use` · `unuse` | organization and project scope |
| `commits` · `commit <sha>` | commit list / detail (module attribution and files) |
| `explain <sha>` | the full correlation chain: requirement, modules, regression candidates, historical bugs, releases, data gaps |
| `risk <sha>` · `module-risk <id>` | risk score with the per-signal contribution breakdown |
| `modules` | module list |
| `findings` · `finding <id>` · `triage <id> <status>` | AI findings, with their evidence, and triage |
| `proposals` · `approve <id>` · `reject <id>` | proposals and the confirmation flow: **only `approve` executes**, through the same executor registry and audit log |
| `agents` · `analyse -a <code> [--wait]` · `analyses` · `job <id>` · `trace <id>` | run an analysis, list history, poll a job, and inspect the model and tool calls behind it |
| `connections` · `connection-add` · `connection-verify` · `connection-delete` | org-level git credentials (admin) |
| `repos` · `repo-add` · `repo-update` · `repo-remove` · `sync <id> [--wait]` | repository registration, window / module depth, sync |
| `providers` · `provider-add` · `provider-verify` · `provider-remove` | model credentials (org-level, admin only); `provider-verify` checks the key before an analysis discovers it is dead |
| `requirements` · `test-cases` · `test-runs` · `bugs` · `releases` | read-only trackers |
| `audit` | the audit trail (org-level, admin only) |
| `tui` | full-screen interactive dashboard (Proposals / Findings / Commits) |

### Conventions

- `--json` is a **global** option placed before the subcommand: `copilot --json findings` (or `COPILOT_JSON=1`) — that is what makes piping into `jq` work.
- Shell completion is enabled: `copilot --install-completion` (bash / zsh / fish / PowerShell).
- Settings and tokens live in the user config directory (Windows `%LOCALAPPDATA%`, Linux `~/.config`, macOS `~/Library/Application Support`) under `ai-devops-copilot/config.json`, written with mode 0600.
- Environment: `COPILOT_CONFIG` (alternate config path), `COPILOT_BASE_URL` (point at a remote backend), `COPILOT_TOKEN` (skip login in CI).
- Failures are one line plus exit code 1, carrying the response's `code` / `status` / `request_id` so a failure lines up with the server log.
- TUI keys: `r` refresh, `a` approve, `x` reject, `q` quit.

## Configuration

Copy `.env.example` to `.env`; key variables:

| Variable | Description |
|---|---|
| `DJANGO_SECRET_KEY` | Must be changed in production |
| `FIELD_ENCRYPTION_KEY` | Fernet key encrypting git tokens / AI keys. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DB_*` | PostgreSQL connection (requires the pgvector extension: `CREATE EXTENSION vector;`) |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis & Celery (example uses dbs 0/1/2) |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` / `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | Defaults: 15 minutes / 7 days |
| `CORS_ALLOWED_ORIGINS` | Allowed browser origins (Swagger UI, etc.; the terminal client needs none), includes `localhost:5173` by default |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `DEEPSEEK_API_KEY` / `QWEN_API_KEY` / `OLLAMA_BASE_URL` | AI provider credentials (can also be configured per organization via the `ai-providers` API — preferred) |

## API Overview

### Conventions

- Prefix `/api/v1/`, JWT auth (access 15min / refresh 7d, refresh-token blacklist on logout); `IsAuthenticated` by default except probes and webhooks
- Uniform error envelope: `{"error": {"code", "message", "details", "request_id"}}`
- Every response carries `X-Request-ID`; an inbound header of the same name is passed through for audit alignment
- AI analysis is always async: `POST` returns `202 + job_id`, `GET` polls the status
- `?ordering=` ranks enum columns by **meaning**, not spelling (findings `severity` from critical to info, requirement `status` along its lifecycle); every other column orders as usual
- Interactive docs: **`/api/docs/`** (Swagger); OpenAPI schema: `/api/schema/`

### Auth & Organizations

| Method | Path | Minimum role |
|---|---|---|
| POST | `/api/v1/auth/register` · `/auth/login` · `/auth/refresh` · `/auth/logout` | — |
| GET/PATCH | `/api/v1/me` | authenticated |
| GET/POST | `/api/v1/orgs` | authenticated (creator becomes admin) |
| GET/PUT/PATCH/DELETE | `/api/v1/orgs/{org_pk}` | viewer / admin |
| GET/POST | `/api/v1/orgs/{org_pk}/members` | viewer / admin |
| GET/PATCH/DELETE | `/api/v1/orgs/{org_pk}/members/{user_id}` | viewer / admin |
| GET/POST | `/api/v1/orgs/{org_pk}/projects` | viewer / pm |
| GET/PUT/PATCH/DELETE | `/api/v1/orgs/{org_pk}/projects/{project_pk}` | viewer / pm / admin |
| GET/POST | `.../projects/{project_pk}/members` | viewer / pm |
| GET/PATCH/DELETE | `.../projects/{project_pk}/members/{user_id}` | viewer / pm |

### Git Integration

| Method | Path | Minimum role |
|---|---|---|
| GET/POST | `/api/v1/orgs/{org_pk}/git-connections` | admin (credentials admin-only) |
| GET/PATCH/DELETE | `/api/v1/orgs/{org_pk}/git-connections/{connection_pk}` | admin |
| POST | `.../git-connections/{connection_pk}/verify` | admin |
| GET/POST | `.../projects/{project_pk}/repositories` | viewer / pm |
| GET/PATCH/DELETE | `.../repositories/{repository_pk}` | viewer / pm / admin |
| POST | `.../repositories/{repository_pk}/sync` | developer (returns 202 + task_id; 409 `sync_in_progress` if one is already running) |
| GET | `.../projects/{project_pk}/commits` · `/commits/{commit_pk}` | viewer |
| GET | `.../projects/{project_pk}/modules` · `/modules/{module_pk}` | viewer |
| POST | `/api/v1/webhooks/github/{connection_pk}` | **unauthenticated**, HMAC-verified |

`commits` supports `?repository=` `?author_email=` `?since=` `?until=` `?search=` `?ordering=`.

### Requirements / Testing / Bugs / Releases

| Method | Path | Allowed roles |
|---|---|---|
| GET/POST | `.../requirements` | viewer / {admin, pm, developer} |
| GET/PUT/PATCH/DELETE | `.../requirements/{requirement_pk}` | viewer / {admin, pm, developer} |
| GET/POST | `.../test-cases` · `.../test-suites` · `.../test-runs` | viewer / {admin, developer, qa} |
| GET/PUT/PATCH/DELETE | `.../test-cases/{test_case_pk}` etc. | viewer / {admin, developer, qa} |
| GET/POST | `.../coverage-snapshots` | viewer / {admin, developer, qa} (append-only) |
| GET/POST | `.../bugs` | viewer / {admin, pm, developer, qa} |
| GET/PUT/PATCH/DELETE | `.../bugs/{bug_pk}` | viewer / {admin, pm, developer, qa} |
| GET/POST | `.../releases` | viewer / {admin, pm} |
| GET/PUT/PATCH/DELETE | `.../releases/{release_pk}` | viewer / {admin, pm} |
| **GET** | **`.../commits/{commit_pk}/explain`** | viewer |

The role matrix is **deliberately non-hierarchical**: QA can edit test cases but PM cannot; PM can manage releases but developers cannot. `RoleRequired` supports both a "minimum role" and an "explicit role set".

### Correlation Explanation `explain`

```jsonc
{
  "commit": {"sha": "abc123", "message": "PAY-18 retry payment"},
  "modules": [{"module": {"path_prefix": "src/payment"}, "weight": 1.0, "churn_lines": 16}],
  "requirement": {"external_key": "PAY-18"}, "requirement_source": "commit",
  "regression_candidates": [{"test_case": {"key": "TC-002"}, "score": 0.85,
                             "reasons": ["covers module src/payment",
                                         "verified a commit in the same modules"]}],
  "historical_bugs": [{"bug": {"key": "BUG-1023"}, "shared_modules": ["src/payment"]}],
  "releases": [{"version": "2026.09"}],
  "data_gaps": ["No open bugs are linked to the touched modules."]
}
```

### AI & Risk

| Method | Path | Description |
|---|---|---|
| POST | `.../ai/analyses` | Submit an AI analysis, returns **202 + job_id** |
| GET | `.../ai/analyses` · `.../ai/analyses/agents` | Analysis history / available agent registry |
| GET | `.../ai/jobs/{job_pk}` | Poll async job status & result |
| GET | `.../ai/jobs/{job_pk}/trace` | The model runs and tool calls behind one job (prompt version, tokens, latency, `scope_denied`) |
| GET | `.../ai/findings[/{finding_pk}]` | AI findings (deduped on re-analysis) |
| POST | `.../ai/findings/{finding_pk}/status` | Triage a finding: new / acknowledged / dismissed / converted (audited) |
| GET | `.../ai/recommendations[/{recommendation_pk}]` | AI recommendations |
| POST | `.../ai/recommendations/{recommendation_pk}/confirm` · `/reject` | Confirm / reject (confirmation triggers the Executor + audit) |
| GET/POST/PATCH/DELETE | `/api/v1/orgs/{org_pk}/ai-providers[/{provider_pk}]` | Model credential management (org-level, admin only) |
| POST | `.../ai-providers/{provider_pk}/verify` | Check the key and write back status / last_verified_at (org-level, admin only) |
| GET | `/api/v1/orgs/{org_pk}/audit-logs[/{audit_pk}]` | The audit trail (org-level, admin only, read-only) |
| GET | `.../commits/{commit_pk}/risk` · `.../modules/{module_pk}/risk` | Risk score + per-signal contribution breakdown |

The AI analysis flow is always three steps: `POST analyses` (with `agent` + target entity) → poll `GET jobs/{id}` → read `findings`.

## Multi-Tenancy & Security

- **Tenant ids come from the URL only, never the request body**; `org` / `project` are serializer read-only, so a request body can never place data into a tenant the caller does not own
- Failure semantics are intentional: not a member of the tenant → **404** (existence not disclosed); a member with insufficient role → **403** (visible, but not permitted)
- All model queries go through the single `scoped_queryset` entry point — tenant filtering cannot be bypassed
- Sensitive fields (git tokens, AI keys) are Fernet-encrypted at rest and exposed only to admins via serializers
- The unauthenticated webhook endpoint relies on HMAC-SHA256 verification + `delivery_id` idempotency
- Every state change goes through the confirmation flow + `AuditLog`; unauthorized tool calls are logged as `scope_denied`
- Evidence citations in AI output must resolve to real rows in this project; failures are dropped with the reason recorded

## Development & Testing

```bash
make verify    # ruff + black --check + mypy + django check + pytest (the same gates CI runs)
make fmt       # black + ruff --fix
make cov       # tests with coverage
make cli-verify  # terminal client: ruff + black --check + mypy + pytest
```

- **559 backend tests**, none requiring internet access: `FakeGitProvider`, scripted fake AI providers, `httpx.MockTransport`; default `-m 'not network'`
- **36 terminal-client tests**: the client is exercised over `httpx.MockTransport` (401 refresh, pagination, error envelope), the command layer through Typer's `CliRunner`, and the TUI through Textual's `run_test` pilot (it mounts, fills its tables, and the confirm/reject keys really reach the API)
- S6 acceptance is locked by tests: no row without confirmation / exact creation + audit on confirmation / zero writes on rejection / `scope_denied` on unauthorized tool calls
- The 4 tests in `tests/integration/test_github_live.py` hit `api.github.com` for real (anonymous GET, repo `octocat/Hello-World`); run manually:

  ```bash
  cd backend && python -m pytest -m network -q
  ```

  One run is ~20 requests against the anonymous IP quota (60/hour); set `GITHUB_TOKEN` to raise the limit to 5000/hour.

- pytest config notes: `python_classes = []` (the domain model is called `TestCase`, which pytest would otherwise collect), `testpaths = ["tests", "apps"]`, strict markers
- CI (GitHub Actions): lint + type + `manage.py check` + `makemigrations --check` + migrations + tests

## Roadmap

- [ ] Terminal client completion: requirement / test-case / bug / release detail views, a commit diff viewer, and launching analyses and browsing their results from the TUI
- [ ] End-to-end smoke test against a real LLM
- [ ] Production-shape Celery worker verification
