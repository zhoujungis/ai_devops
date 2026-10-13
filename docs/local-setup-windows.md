# 本地开发环境启动说明（Windows / 免 Docker）

本机未安装 Docker，PostgreSQL 与 Redis 都以「Windows 原生 + 免安装」的方式部署，
**不需要 WSL，也不需要管理员权限**。以下是已经配置好的现状与日常启动方式。

客户端是终端程序 `aidevops`（CLI + TUI），通过 HTTP 调后端；前端已移除。

## 已安装的组件

| 组件 | 位置 / 方式 | 端口 | 凭据 |
|---|---|---|---|
| PostgreSQL 18.6 | `C:\Users\admin\pgsql18\pgsql`（EDB zip 便携版），已注册为 Windows 服务 `ai_devops_pg`（自启） | 5432 | 库 `ai_devops`，用户 `copilot`，密码 `copilot_dev_pw` |
| pgvector 0.8.6 | 已编译并安装进上面这份 PostgreSQL | — | — |
| Redis 3.0.504 | `C:\Program Files\Redis`，已注册为 Windows 服务 `Redis`（自启） | 6379 | 无 |
| 后端 venv | `D:\ai_devops\backend\.venv` | 8000 | — |
| 客户端 venv | `D:\ai_devops\cli\.venv` | — | — |

数据目录在 `C:\Users\admin\pgsql18\data`，日志在 `C:\Users\admin\pgsql18\pg.log`。

> **为什么是便携版而不是安装程序**：EDB 的 Windows 安装程序需要 UAC 提权，而当前沙箱
> 无法弹出/确认 UAC 对话框，所以改用官方 zip 二进制包。它就是标准 PostgreSQL 18.6，
> 功能和安装版完全一致。
>
> **为什么 PG 要注册成服务**：PostgreSQL 在 Windows 上拒绝以管理员身份启动，报
> `Execution of PostgreSQL by a user with administrative permissions is not permitted.`
> 然后 exit code 1。`pg_ctl start` 会把调用者的令牌原样交给 `postgres.exe`，所以只要
> 终端是提权的就一定失败；而 SCM 启动服务时用的是服务账户令牌，不受影响。
> 注册命令（需管理员，已执行过）：
>
> ```cmd
> "C:\Users\admin\pgsql18\pgsql\bin\pg_ctl.exe" register -N ai_devops_pg ^
>   -D "C:\Users\admin\pgsql18\data" -S auto
> ```
>
> **pgvector 为什么要自己编译**：Windows 版 PostgreSQL 官方不带 pgvector，而
> `docker/initdb/01-extensions.sql` 假定容器里已有该扩展。因此这里拉取 pgvector 0.8.6
> 源码用 MSVC 编译安装（编译脚本见 `scripts/build-pgvector.cmd`）。
> 注意 **pgvector 必须 ≥ 0.8.1** 才支持 PostgreSQL 18，0.8.0 及以下会在
> `hnswvacuum.c` 编译失败。

## 日常启动：双击 `scripts\dev-up.cmd`

脚本会依次：启动 PostgreSQL 服务 → 确认 Redis → 开窗口跑 Django → 开窗口跑
Celery worker 与 beat。

启动后：

- API：<http://127.0.0.1:8000>
- Swagger：<http://127.0.0.1:8000/api/docs/>
- 探针：`/healthz`（存活）、`/readyz`（就绪，会实查 PostgreSQL + pgvector + Redis）

只想要数据库、不启应用时，双击 `scripts\start-db.cmd`。

> 两个数据库服务都是自启的，所以重启机器后通常什么都不用做，直接跑 Django 即可。

> 本机**没有装 docker，也没有装 make**，所以 README 里的 `docker compose up` 和
> `make run` 都用不了。下一节的等价命令就是替代方案，照抄即可。

> **Celery 不是可选项**：仓库同步和每一次 AI 分析都是入队执行的。没有 worker，
> `aidevops sync` 会一直停在 `queued`，看起来就像坏了。`dev-up.cmd` 已经把 worker 和
> beat 带上；本地没配模型 Provider 时，分析任务会以「永久失败」结束，这是预期行为。

## 手动启动（等价命令）

```bash
# 1) PostgreSQL —— 走服务（自启，一般无需手动）
net start ai_devops_pg

# 2) Redis —— 同上
net start Redis

# 3) 后端
cd D:\ai_devops\backend
.venv\Scripts\python.exe manage.py runserver

# 4) Celery（同步与 AI 分析都靠它）
.venv\Scripts\python.exe -m celery -A config worker -Q ai,sync,default -l info
.venv\Scripts\python.exe -m celery -A config beat -l info
```

## 终端客户端 `aidevops`

```bash
cd D:\ai_devops\cli
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"

.venv\Scripts\aidevops.exe login                    # GitHub 设备码登录（浏览器输入码授权）
.venv\Scripts\aidevops.exe org-add "My Team"        # 新用户先建一个组织（创建者即 admin）
.venv\Scripts\aidevops.exe projects                 # 列出项目
.venv\Scripts\aidevops.exe use qa-copilot-platform  # 选中一个项目
.venv\Scripts\aidevops.exe commits                  # 最近提交
.venv\Scripts\aidevops.exe explain <sha>            # 一次提交的完整关联链
.venv\Scripts\aidevops.exe risk <sha>               # 逐信号风险分解
.venv\Scripts\aidevops.exe findings                 # AI 发现
.venv\Scripts\aidevops.exe proposals                # 待确认的 AI 建议
.venv\Scripts\aidevops.exe approve <id>             # 确认（才会真正执行）
.venv\Scripts\aidevops.exe tui                      # 全屏交互式仪表盘
```

- 配置与 token 存在 `%LOCALAPPDATA%\aidevops\config.json`（权限 0600），
  可用 `AIDEVOPS_CONFIG` 指定别的路径，用 `AIDEVOPS_BASE_URL` 指向远程后端。
- `--json` 是**全局**选项，放在子命令之前：`aidevops.exe --json findings`；
  也可用环境变量 `AIDEVOPS_JSON=1`，方便直接管道给 `jq`。

## 从 0.1.0 升级（破坏性变更）

- **`/api/v1/auth/login`、`/api/v1/auth/register` 已删除**：0.1.0 的 CLI 用邮箱密码
  登录，这两个端点现在都不存在。**必须升级客户端**：`pipx upgrade aidevops`
  （或 `pip install -U aidevops`），否则登录直接 404。
- **登录改为 GitHub 设备码流程**：`.env` 里必须配 `GITHUB_CLIENT_ID`（OAuth App 要勾
  **Enable Device Flow**），否则 `aidevops login` 会明确报错（见下方「首次使用」）。
- **老账号不会自动迁移**：既有的邮箱密码账号（本机的 `me@example.com`）**不会被自动
  绑定到 GitHub**。首次用 GitHub 登录会**新建**一个账号，老账号及其组织/项目不会自动
  跟过来。本机只有一个无组织的老账号，直接重建即可（旧行可留可删，不影响使用）。

## 数据库维护

```bash
PG="C:\Users\admin\pgsql18\pgsql\bin\psql.exe"
PGPASSWORD=copilot_dev_pw "$PG" -U copilot -h 127.0.0.1 -d ai_devops

# 应用迁移
cd D:\ai_devops\backend && .venv\Scripts\python.exe manage.py migrate

# 建管理员（Django admin，可选）
.venv\Scripts\python.exe manage.py createsuperuser
```

### 测试库需要 vector 扩展

pytest 会新建 `test_ai_devops` 库。若该库没有 `vector` 类型，表创建会报
`type "vector" does not exist`。已把扩展预装在 `template1`，新建库会自动继承：

```bash
PGPASSWORD=copilot_dev_pw "$PG" -U copilot -h 127.0.0.1 -d template1 \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

## 跑质量检查

```bash
# 后端
cd D:\ai_devops\backend
.venv\Scripts\python.exe -m pytest -q            # 默认跳过 network 标记
.venv\Scripts\python.exe -m pytest -m network -q # 会真实访问 api.github.com，手动跑
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .

# 终端客户端
cd D:\ai_devops\cli
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy ai_devops_cli
```

> 运行 pytest 时请确保环境变量 `DJANGO_SETTINGS_MODULE` 没有被设成
> `config.settings.dev`（例如从跑过 dev-up 的窗口里直接跑测试）。它优先级高于
> `pyproject.toml` 里的 `config.settings.test`，被它覆盖后测试会用 dev 配置运行：
> Celery 不再 eager，异步用例会挂在「任务永远没人执行」上。

## 首次使用：GitHub 登录

登录只走 GitHub 设备码流程，需要先在 GitHub 建一个 OAuth App：

1. GitHub → Settings → Developer settings → **OAuth Apps** → New OAuth App
2. **务必勾选 Enable Device Flow**（不勾会报 `device_flow_disabled`）
3. 建完把 **Client ID** 填进仓库根目录 `.env` 的 `GITHUB_CLIENT_ID=`，重启后端。
   **不需要 Client Secret** —— 设备码流程根本不用它。

然后：

```bash
cd D:\ai_devops\cli
.venv\Scripts\aidevops.exe login              # 浏览器打开 github.com/login/device，输入终端里显示的码
.venv\Scripts\aidevops.exe org-add "My Team"  # 新用户先建一个组织，之后才能建项目
.venv\Scripts\aidevops.exe projects           # 应该能看到刚建的组织下的项目（初始为空）
```

> `/api/v1/auth/github` 与 `/api/v1/auth/github/config` **都没有尾斜杠** —— 带斜杠会
> 返回 404，整个 API 都遵循这个约定。旧的 `/auth/register`、`/auth/login` 已删除。

## 灌入演示数据

产品的主链路是「配置 Git 连接 → 注册仓库 → 同步 → 产生 commit/module → 风险算分 →
AI 产出 finding」。这条链路缺任何一环，`aidevops findings` / `explain` / `risk` 就是空态。
本地想看有数据的界面，跑：

```bash
cd D:\ai_devops
backend\.venv\Scripts\python.exe scripts\seed_demo.py        # 幂等，可反复跑
backend\.venv\Scripts\python.exe scripts\verify_demo_api.py  # 走真实 HTTP 回读校验
```

`seed_demo.py` 会为 `QA Copilot Platform` 与 `Payments Gateway` 两个项目写入完整的
演示数据：1 个 Git 连接、1 个仓库、6 个模块、12 个 commit、覆盖率快照、6 次测试运行、
5 个 bug、5 条 requirement、3 个 release，以及 5 条 AI finding 和 3 条 AI 建议。

> **为什么需要脚本而不是直接调 API**：`AIFinding` 和 `AIRecommendation` 的
> serializer 全部 `read_only`——按设计它们只能由 agent 运行产生，HTTP 无法写入。
> 而且真实的仓库数据需要外部 Git 平台，本地没有。所以演示数据走 ORM。

## 接真实仓库（同步排错）

`seed_demo.py` 写的连接用的是**假 token**，所以 `aidevops sync` 必然失败 —— 那是预期的。
要接真仓库：

```bash
cd D:\ai_devops\cli
.venv\Scripts\aidevops.exe connections                        # 拿 connection id
.venv\Scripts\aidevops.exe connection-verify <connection-id>  # 先验证凭据
.venv\Scripts\aidevops.exe repos                              # 拿 repository id
.venv\Scripts\aidevops.exe sync <repository-id> --wait
```

### 看到 404 先别怀疑仓库不存在

GitHub 对**私有库的无权限访问返回 404 而不是 403**（有意为之，不泄露仓库是否存在）。
所以 `GitNotFoundError: GitHub returned 404` 通常是**凭据无效/无权限**，不是仓库没了。

按这个顺序查：

1. **`base_url` 要用默认值（留空或 `https://api.github.com`）。**
   填 `https://github.com` 是**网页站**，所有 API 路径都会 404，而 `connection-verify`
   打 `/user` 得到 **406**，现象极像"仓库不存在"。这是最容易踩的一个坑。
2. `connection-verify` 是否返回 `Verified (active)`。不是就换 token
   （私有库需要 `repo` scope 的 PAT）。
3. 看**真实请求 URL**：`.run/django.log` 与 `.run/celery-worker.log` 里 httpx 会打出完整
   URL，一眼能分辨是打错主机还是真没权限。

> `sync --wait` 报 `failed` 时，请以 `repos` 显示的 `LAST SYNCED` 和实际数据为准：
> 入队后 worker 需要一点时间接手，早先的版本会把上一轮遗留的失败状态当成本次结果。

## 已知限制

- 本机的 `.env` 原本指向「WSL2 里的 PostgreSQL」，现已改为 Windows 原生实例，
  连接参数（`DB_HOST=127.0.0.1`、库名、用户、密码）保持不变，无需修改。
- README 的 docker compose 流程在本机不可用（未安装 Docker）；两套方案共用同一份
  `.env`，切回容器时 compose 会用服务名覆盖 `DB_HOST` 等变量。

## aidevops 与本机 HTTP 代理

本机环境里注入了 `HTTP_PROXY` / `HTTPS_PROXY`（指向沙箱自己的出口代理）。
`aidevops` 用的 httpx **默认信任环境代理**（`trust_env=True`），于是它会尝试通过代理
访问 `http://127.0.0.1:8000`。代理不稳定时会看到这种诡异现象：

```
x <!DOCTYPE html> ... <title>Page not found at
http://127.0.0.1:8000/api/v1/orgs/<uuid>/projects</title>
```

Django 那边会记成：

```
"GET http://127.0.0.1:8000/api/v1/orgs/.../projects HTTP/1.1" 404
Not Found: /http:/127.0.0.1:8000/api/v1/orgs/...
```

注意请求行是**绝对 URL** 而不是路径——本地 API 根本不该经过代理。临时绕开：

```bash
cd D:\ai_devops\cli
set HTTP_PROXY=
set HTTPS_PROXY=
.venv\Scripts\aidevops.exe projects
```

（bash 里用 `export HTTP_PROXY= HTTPS_PROXY=`。）

这属于本机环境问题，不是后端或 CLI 的逻辑错误；在没配代理的机器上不会出现。

