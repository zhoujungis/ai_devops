# AI DevOps / QA Copilot

**[中文](#中文) | [English](#english)**

---

<a id="中文"></a>

# 中文

一个理解「需求 → 代码 → 测试 → Bug → 发布 → 线上问题」完整研发链路的 AI 工作平台。核心不是聊天机器人，而是**主动理解项目当前发生了什么**：把原本孤立的研发数据关联起来，发现风险、生成测试、辅助定位问题。

## 核心能力

### 1. 研发数据关联引擎

- **GitHub 仓库同步**：REST API 增量 + 历史回填双游标，预算截断后可安全续跑；限流退避重试
- **Webhook 实时摄取**：HMAC-SHA256 验签 + `delivery_id` 幂等，push 事件即时增量入库
- **模块归属解析**：覆盖规则 > 源根推断 > 兜底策略，每种归属都带可引用的理由（`reason`）
- **全链路可解释**：`explain` 端点返回一条提交的完整关联链 —— 命中的需求、模块影响、回归测试候选、历史 Bug、所在发布
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
- 信号缺失时显式标注，而不是静默按 0 处理

### 3. AI 分析（Provider 可插拔）

- **Provider Adapter**：OpenAI 兼容协议 + DeepSeek / Qwen / Ollama 预设；凭据组织级管理，Fernet 加密落库
- **内置 Agent**：
  - **Code Impact** —— 一次提交影响什么，复用关联引擎
  - **Test Generation** —— 11 类场景覆盖矩阵（正常 / 边界 / 异常 / 并发等），强制逐类给结论
  - **Bug Investigation** —— 基于 Bug 出现轨迹与关联链调查根因
  - **Requirement Analysis** —— 需求与模块/用例的关联推断
- **证据校验**：AI 引用必须能解析到本项目真实数据，否则丢弃并记录原因
- **事实 / 假设 / 证据强制分离**的 schema 约束（`StrictModel` 拒绝多余字段）
- **版本化 Prompt 注册表**：frontmatter 绑定输出 schema，禁止硬编码
- **全链路追踪**：`AIAnalysisRun` / `AIToolCall` 记录 prompt 版本、tokens、延迟、digest；成本记账（cached token 单独计价，未定价记 `NULL` 而非 0）
- **异步作业**：`POST` 返回 `202 + job_id`，客户端轮询；幂等键防重复提交；永久/瞬时失败区分处理
- **两段式工具暴露**：模型 function-calling 列表只有 13 个只读工具，全部强制按 project 作用域过滤；写操作不进模型

### 4. 确认流（AI 不直接改状态）

- AI 产出「建议对象」，用户**确认后**才由注册的 Executor 执行，全程写 `AuditLog`
- Executor 集合由代码固定，模型只能指名已有动作，不能发明执行路径
- 未确认 = 零写入；越权工具调用记 `scope_denied` 留痕而非静默
- 重复分析更新已有 finding（dedupe），不无限堆积

## 架构与技术栈

```
┌────────────┐   REST/JSON + JWT   ┌──────────────────────────────┐
│  Frontend  │ ──────────────────► │  Backend (Django + DRF)      │
│  Vue 3+TS  │                     │  ├─ 10 个领域 app             │
│  Naive UI  │                     │  ├─ CorrelationService 关联引擎│
│  Pinia     │                     │  ├─ 风险引擎 + AI Agent 管线   │
└────────────┘                     │  └─ Celery 异步作业           │
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
| Frontend | Vue 3 · TypeScript · Vite · Naive UI · Tailwind CSS（深色优先）· Pinia · axios（JWT 自动刷新） |
| 质量 | pytest（487 个测试）· ruff · black · mypy（strict + django/drf 插件）· drf-spectacular（OpenAPI 零告警） |
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
    releases/               Release / ReleaseCommitLink / ReleaseRiskSnapshot
    ai/                     Provider Adapter、结构化输出、prompt 注册表、
                            Tool 层、AIAnalysisJob / AIAnalysisRun / AIToolCall、
                            Agent（code_impact / bug_investigation / requirement /
                            test_generation）与确认流模型
    risk/                   RiskRule / RiskScore，10 信号风险引擎 + 权重归一化
  services/
    correlation.py          CorrelationService.explain_commit —— 关联链路唯一实现
    executors.py            Executor 注册表（确认流执行器）
    linking.py              跨实体边（source + confidence）
  tests/integration/       test_s3_api / test_ai_api / test_correlation_chain /
                           test_github_live
frontend/
  src/pages/               登录 / 项目选择 / Dashboard（AI 发现）/
                           Code Impact（提交风险分解）
  src/api/                 axios 实例 + JWT 自动刷新 + 错误信封解包
  src/components/          RiskBreakdown（逐信号贡献）/ DataGaps（数据缺口）
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

前端：

```bash
cd frontend && npm install --include=dev && npm run dev   # http://localhost:5173
```

`npm run build` 会先跑 `vue-tsc --noEmit`，类型不过就构建不过。

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

## 配置

复制 `.env.example` 为 `.env`，关键项：

| 变量 | 说明 |
|---|---|
| `DJANGO_SECRET_KEY` | 生产环境必须更换 |
| `FIELD_ENCRYPTION_KEY` | Fernet 密钥，加密 git token / AI key 等字段。生成：`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DB_*` | PostgreSQL 连接（需 pgvector 扩展：`CREATE EXTENSION vector;`） |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis 与 Celery（示例里分用 db 0/1/2） |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` / `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | 默认 15 分钟 / 7 天 |
| `CORS_ALLOWED_ORIGINS` | 前端源，默认含 `localhost:5173` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `DEEPSEEK_API_KEY` / `QWEN_API_KEY` / `OLLAMA_BASE_URL` | AI Provider 凭据（也可运行后在 `ai-providers` API 里按组织配置，推荐后者） |

## API 概览

### 通用约定

- 前缀 `/api/v1/`，JWT 认证（access 15min / refresh 7d，refresh 走黑名单登出），除探针与 webhook 外默认 `IsAuthenticated`
- 错误响应统一信封：`{"error": {"code", "message", "details", "request_id"}}`
- 所有响应带 `X-Request-ID`，入站同名头会被透传，便于与审计记录对齐
- AI 分析一律异步：`POST` 返回 `202 + job_id`，`GET` 轮询状态
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
| POST | `.../repositories/{repository_pk}/sync` | developer（返回 202 + task_id） |
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
| GET | `.../ai/findings[/{finding_pk}]` | AI 发现（重复分析去重更新） |
| GET | `.../ai/recommendations[/{recommendation_pk}]` | AI 建议 |
| POST | `.../ai/recommendations/{recommendation_pk}/confirm` · `/reject` | 确认 / 拒绝（确认才由 Executor 执行并写审计） |
| GET/POST/PATCH/DELETE | `/api/v1/orgs/{org_pk}/ai-providers[/{provider_pk}]` | 模型凭据管理（组织级，仅 admin） |
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
make verify    # ruff + mypy + django check + pytest
make fmt       # black + ruff --fix
make cov       # 带覆盖率跑测试
```

- **487 个测试**全部不依赖外网：`FakeGitProvider`、脚本化假 AI Provider、`httpx.MockTransport`，默认 `-m 'not network'`
- S6 验收逐条有测试锁定：未确认不建行 / 确认精确创建 + 审计 / 拒绝零写入 / 越权记 `scope_denied`
- `tests/integration/test_github_live.py` 的 4 个用例会真实访问 `api.github.com`（匿名 GET，仓库 `octocat/Hello-World`），手动执行：

  ```bash
  cd backend && python -m pytest -m network -q
  ```

  一次运行约 20 次请求，计入出口 IP 的匿名额度（60 次/小时）；设置 `GITHUB_TOKEN` 可提升到 5000 次/小时。

- pytest 配置要点：`python_classes = []`（领域模型叫 `TestCase`，避免 pytest 误收集）、`testpaths = ["tests", "apps"]`、strict markers
- CI（GitHub Actions）：lint + type + `manage.py check` + `makemigrations --check` + migrations + tests

## Roadmap

- [ ] 前端补完：ECharts 健康度趋势、Monaco diff 查看器、需求 / 用例 / Bug 详情 / 发布 / 设置页面、Playwright e2e
- [ ] 注册 `rca`（根因分析）为独立 Agent（prompt 与 schema 已就绪）
- [ ] 真实 LLM 端到端 smoke test
- [ ] Celery worker 生产形态实跑验证

---

<a id="english"></a>

# English

An AI work platform that understands the full engineering chain — **requirements → code → tests → bugs → releases → production issues**. It is not a chatbot: it proactively understands what is happening in your project, correlating previously siloed engineering data to surface risks, generate tests, and help investigate problems.

## Key Capabilities

### 1. Engineering Data Correlation Engine

- **GitHub repository sync**: dual-cursor REST sync (incremental + historical backfill), safely resumable after budget truncation, with rate-limit backoff
- **Real-time webhooks**: HMAC-SHA256 signature verification + `delivery_id` idempotency; push events ingested immediately
- **Module attribution**: override rules > source-root inference > fallback, each attribution carrying a citable `reason`
- **Explainable chain**: the `explain` endpoint returns the full correlation chain for a commit — matched requirement, module impact, regression test candidates, historical bugs, releases involved
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
- Missing signals are labeled explicitly instead of silently counting as zero

### 3. AI Analysis (Pluggable Providers)

- **Provider adapter**: OpenAI-compatible protocol with DeepSeek / Qwen / Ollama presets; credentials managed per organization, encrypted at rest with Fernet
- **Built-in agents**:
  - **Code Impact** — what a commit affects, reusing the correlation engine
  - **Test Generation** — an 11-category scenario coverage matrix (happy path, boundary, error, concurrency, …), forced to conclude on every category
  - **Bug Investigation** — root-cause investigation from occurrence trajectories and correlation chains
  - **Requirement Analysis** — requirement ↔ module/test-case correlation inference
- **Evidence validation**: every AI citation must resolve to real data in this project, otherwise it is dropped and the reason recorded
- **Fact / hypothesis / evidence separation** enforced by strict schemas (`StrictModel` rejects extra fields)
- **Versioned prompt registry**: frontmatter binds each prompt to its output schema — no hardcoded prompts
- **Full tracing**: `AIAnalysisRun` / `AIToolCall` record prompt version, tokens, latency, digest; cost accounting prices cached tokens separately and stores `NULL` (not 0) for unpriced models
- **Async jobs**: `POST` returns `202 + job_id`, clients poll; idempotency keys prevent duplicates; permanent vs transient failures distinguished
- **Two-stage tool exposure**: the model's function-calling list contains only 13 read-only tools, all forcibly project-scoped; write tools are never exposed to the model

### 4. Confirmation Flow (AI never mutates state directly)

- AI produces **recommendations**; only after explicit user confirmation does a registered Executor act, with a full `AuditLog`
- The Executor set is fixed in code — the model can only name existing actions, never invent execution paths
- No confirmation = zero writes; unauthorized tool calls are logged as `scope_denied`, never silenced
- Repeated analyses update existing findings (dedupe) instead of piling up

## Architecture & Stack

```
┌────────────┐   REST/JSON + JWT   ┌──────────────────────────────┐
│  Frontend  │ ──────────────────► │  Backend (Django + DRF)      │
│  Vue 3+TS  │                     │  ├─ 10 domain apps           │
│  Naive UI  │                     │  ├─ CorrelationService engine│
│  Pinia     │                     │  ├─ Risk engine + AI agents  │
└────────────┘                     │  └─ Celery async jobs        │
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
| Frontend | Vue 3 · TypeScript · Vite · Naive UI · Tailwind CSS (dark-first) · Pinia · axios (auto JWT refresh) |
| Quality | pytest (487 tests) · ruff · black · mypy (strict + django/drf plugins) · drf-spectacular (zero-warning OpenAPI) |
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
    releases/               Release / ReleaseCommitLink / ReleaseRiskSnapshot
    ai/                     provider adapter, structured output, prompt registry,
                            tool layer, AIAnalysisJob / AIAnalysisRun / AIToolCall,
                            agents (code_impact / bug_investigation / requirement /
                            test_generation) + confirmation-flow models
    risk/                   RiskRule / RiskScore, 10-signal engine + weight
                            normalization
  services/
    correlation.py          CorrelationService.explain_commit — the single
                            implementation of the correlation chain
    executors.py            Executor registry (confirmation-flow executors)
    linking.py              cross-entity edges (source + confidence)
  tests/integration/        test_s3_api / test_ai_api / test_correlation_chain /
                            test_github_live
frontend/
  src/pages/                Login / Project picker / Dashboard (AI findings) /
                            Code Impact (per-commit risk breakdown)
  src/api/                  axios instance + auto JWT refresh + error-envelope
                            unwrapping + X-Request-ID passthrough
  src/components/           RiskBreakdown (per-signal contributions) /
                            DataGaps (explicit data gaps)
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

Frontend:

```bash
cd frontend && npm install --include=dev && npm run dev   # http://localhost:5173
```

`npm run build` runs `vue-tsc --noEmit` first — the build fails if types fail.

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

## Configuration

Copy `.env.example` to `.env`; key variables:

| Variable | Description |
|---|---|
| `DJANGO_SECRET_KEY` | Must be changed in production |
| `FIELD_ENCRYPTION_KEY` | Fernet key encrypting git tokens / AI keys. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DB_*` | PostgreSQL connection (requires the pgvector extension: `CREATE EXTENSION vector;`) |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis & Celery (example uses dbs 0/1/2) |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` / `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | Defaults: 15 minutes / 7 days |
| `CORS_ALLOWED_ORIGINS` | Frontend origins, includes `localhost:5173` by default |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `DEEPSEEK_API_KEY` / `QWEN_API_KEY` / `OLLAMA_BASE_URL` | AI provider credentials (can also be configured per organization via the `ai-providers` API — preferred) |

## API Overview

### Conventions

- Prefix `/api/v1/`, JWT auth (access 15min / refresh 7d, refresh-token blacklist on logout); `IsAuthenticated` by default except probes and webhooks
- Uniform error envelope: `{"error": {"code", "message", "details", "request_id"}}`
- Every response carries `X-Request-ID`; an inbound header of the same name is passed through for audit alignment
- AI analysis is always async: `POST` returns `202 + job_id`, `GET` polls the status
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
| POST | `.../repositories/{repository_pk}/sync` | developer (returns 202 + task_id) |
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
| GET | `.../ai/findings[/{finding_pk}]` | AI findings (deduped on re-analysis) |
| GET | `.../ai/recommendations[/{recommendation_pk}]` | AI recommendations |
| POST | `.../ai/recommendations/{recommendation_pk}/confirm` · `/reject` | Confirm / reject (confirmation triggers the Executor + audit) |
| GET/POST/PATCH/DELETE | `/api/v1/orgs/{org_pk}/ai-providers[/{provider_pk}]` | Model credential management (org-level, admin only) |
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
make verify    # ruff + mypy + django check + pytest
make fmt       # black + ruff --fix
make cov       # tests with coverage
```

- **487 tests**, none requiring internet access: `FakeGitProvider`, scripted fake AI providers, `httpx.MockTransport`; default `-m 'not network'`
- S6 acceptance is locked by tests: no row without confirmation / exact creation + audit on confirmation / zero writes on rejection / `scope_denied` on unauthorized tool calls
- The 4 tests in `tests/integration/test_github_live.py` hit `api.github.com` for real (anonymous GET, repo `octocat/Hello-World`); run manually:

  ```bash
  cd backend && python -m pytest -m network -q
  ```

  One run is ~20 requests against the anonymous IP quota (60/hour); set `GITHUB_TOKEN` to raise the limit to 5000/hour.

- pytest config notes: `python_classes = []` (the domain model is called `TestCase`, which pytest would otherwise collect), `testpaths = ["tests", "apps"]`, strict markers
- CI (GitHub Actions): lint + type + `manage.py check` + `makemigrations --check` + migrations + tests

## Roadmap

- [ ] Frontend completion: ECharts health trends, Monaco diff viewer, requirement / test-case / bug detail / release / settings pages, Playwright e2e
- [ ] Register `rca` (root-cause analysis) as a standalone agent (prompt & schema ready)
- [ ] End-to-end smoke test against a real LLM
- [ ] Production-shape Celery worker verification
