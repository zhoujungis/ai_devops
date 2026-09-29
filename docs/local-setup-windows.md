# 本地开发环境启动说明（Windows / 免 Docker）

本机未安装 Docker，PostgreSQL 与 Redis 都以「Windows 原生 + 免安装」的方式部署，
**不需要 WSL，也不需要管理员权限**。以下是已经配置好的现状与日常启动方式。

客户端是终端程序 `copilot`（CLI + TUI），通过 HTTP 调后端；前端已移除。

## 已安装的组件

| 组件 | 位置 / 方式 | 端口 | 凭据 |
|---|---|---|---|
| PostgreSQL 18.6 | `C:\Users\admin\pgsql18\pgsql`（EDB zip 便携版） | 5432 | 库 `ai_devops`，用户 `copilot`，密码 `copilot_dev_pw` |
| pgvector 0.8.6 | 已编译并安装进上面这份 PostgreSQL | — | — |
| Redis 3.0.504 | `C:\Program Files\Redis`，已注册为 Windows 服务 `Redis` | 6379 | 无 |
| 后端 venv | `D:\ai_devops\backend\.venv` | 8000 | — |
| 客户端 venv | `D:\ai_devops\cli\.venv` | — | — |

数据目录在 `C:\Users\admin\pgsql18\data`，日志在 `C:\Users\admin\pgsql18\pg.log`。

> **为什么是便携版而不是安装程序**：EDB 的 Windows 安装程序需要 UAC 提权，而当前沙箱
> 无法弹出/确认 UAC 对话框，所以改用官方 zip 二进制包。它就是标准 PostgreSQL 18.6，
> 功能和安装版完全一致，只是没有注册成服务，需要手动启动。
>
> **pgvector 为什么要自己编译**：Windows 版 PostgreSQL 官方不带 pgvector，而
> `docker/initdb/01-extensions.sql` 假定容器里已有该扩展。因此这里拉取 pgvector 0.8.6
> 源码用 MSVC 编译安装（编译脚本见 `scripts/build-pgvector.cmd`）。
> 注意 **pgvector 必须 ≥ 0.8.1** 才支持 PostgreSQL 18，0.8.0 及以下会在
> `hnswvacuum.c` 编译失败。

## 日常启动：双击 `scripts\dev-up.cmd`

脚本会依次：启动 PostgreSQL → 确认 Redis → 开一个窗口跑 Django。

启动后：

- API：<http://127.0.0.1:8000>
- Swagger：<http://127.0.0.1:8000/api/docs/>
- 探针：`/healthz`（存活）、`/readyz`（就绪，会实查 PostgreSQL + pgvector + Redis）

只想要数据库、不启应用时，双击 `scripts\start-db.cmd`。

> AI 分析走异步作业，需要 worker：另开终端跑
> `backend\.venv\Scripts\python.exe -m celery -A config worker -Q ai,sync,default -l info`
> （本地没有配置模型 Provider 时，分析任务会以「永久失败」结束，这是预期行为。）

## 手动启动（等价命令）

```bash
# 1) PostgreSQL
"C:\Users\admin\pgsql18\pgsql\bin\pg_ctl.exe" -D "C:\Users\admin\pgsql18\data" \
  -l "C:\Users\admin\pgsql18\pg.log" -o "-p 5432" start

# 2) Redis —— 已注册为服务，通常开机自启；没起时手动拉一下
net start Redis

# 3) 后端
cd D:\ai_devops\backend
.venv\Scripts\python.exe manage.py runserver

# 4) Celery（AI 分析走异步作业，需要 worker + beat）
.venv\Scripts\python.exe -m celery -A config worker -Q ai,sync,default -l info
.venv\Scripts\python.exe -m celery -A config beat -l info
```

## 终端客户端 `copilot`

```bash
cd D:\ai_devops\cli
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"

.venv\Scripts\copilot.exe login                    # 交互输入邮箱和密码
.venv\Scripts\copilot.exe projects                 # 列出项目
.venv\Scripts\copilot.exe use qa-copilot-platform  # 选中一个项目
.venv\Scripts\copilot.exe commits                  # 最近提交
.venv\Scripts\copilot.exe explain <sha>            # 一次提交的完整关联链
.venv\Scripts\copilot.exe risk <sha>               # 逐信号风险分解
.venv\Scripts\copilot.exe findings                 # AI 发现
.venv\Scripts\copilot.exe proposals                # 待确认的 AI 建议
.venv\Scripts\copilot.exe approve <id>             # 确认（才会真正执行）
.venv\Scripts\copilot.exe tui                      # 全屏交互式仪表盘
```

- 配置与 token 存在 `%LOCALAPPDATA%\ai-devops-copilot\config.json`（权限 0600），
  可用 `COPILOT_CONFIG` 指定别的路径，用 `COPILOT_BASE_URL` 指向远程后端。
- `--json` 是**全局**选项，放在子命令之前：`copilot.exe --json findings`；
  也可用环境变量 `COPILOT_JSON=1`，方便直接管道给 `jq`。

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

## 首次使用要点账号

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"me@example.com\",\"password\":\"devpass12345\"}"
```

> 注意路径**没有尾斜杠**。`/api/v1/auth/login/` 带斜杠会返回 404 —— 路由就是这么
> 声明的，整个 API 都遵循这个约定。

也可以之后直接用 `copilot login` 登录已有账号。

## 灌入演示数据

产品的主链路是「配置 Git 连接 → 注册仓库 → 同步 → 产生 commit/module → 风险算分 →
AI 产出 finding」。这条链路缺任何一环，`copilot findings` / `explain` / `risk` 就是空态。
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

## 已知限制

- **PostgreSQL 不会开机自启**（便携版没有服务），每次重启机器后要先跑一次
  `scripts\start-db.cmd`。想彻底免掉这一步，可以管理员身份执行：

  ```cmd
  "C:\Users\admin\pgsql18\pgsql\bin\pg_ctl.exe" register -N ai_devops_pg ^
    -D "C:\Users\admin\pgsql18\data" -S auto
  ```

- 本机的 `.env` 原本指向「WSL2 里的 PostgreSQL」，现已改为 Windows 原生实例，
  连接参数（`DB_HOST=127.0.0.1`、库名、用户、密码）保持不变，无需修改。
- README 的 docker compose 流程在本机不可用（未安装 Docker）；两套方案共用同一份
  `.env`，切回容器时 compose 会用服务名覆盖 `DB_HOST` 等变量。
