# 登录改造方案：邮箱密码 → GitHub

> 状态：**待评审**（尚未动代码）
> 目标：产品面登录只保留 GitHub；邮箱密码端点一并删除。

## 1. 已拍板的范围

| 决定 | 内容 |
|---|---|
| 登录方式 | 只保留 GitHub，**完全替掉**邮箱密码 |
| `/auth/register`、`/auth/login` | **删除** |
| 应用类型 | 本次用 **OAuth App + device flow**；GitHub App 留到以后做细粒度仓库权限 |

## 2. 影响面实测（先纠正我先前说过头的地方）

我一开始警告「删掉邮箱密码会让 CI 断」，**这个说法夸大了**。实测数据：

| 检查项 | 实测结果 |
|---|---|
| 真正 HTTP 调 `/auth/login` 或 `/auth/register` 的测试 | **仅 2 个文件、5 处** |
| 用 `force_authenticate` / `auth_client` 的测试 | 8 个文件、32 处 —— **完全不受影响** |
| `test_auth_api.py`（16 个用例） | 删 4、重写 5、**不动 8** |
| `test_throttling.py`（4 个用例） | 只改 `test_login_is_throttled` 1 个 |

原因：`backend/apps/accounts/tests/conftest.py` 里的 `auth_client()` 用的是
`client.force_authenticate(user=user)`，**根本不经过登录接口**。绝大多数测试走的是它。

所以真实代价比我先前说的小一个数量级。

## 3. 认证流程

采用 **OAuth Device Authorization Grant**。选它的依据（GitHub 官方文档）：

- device flow **不需要 `client_secret`** → CLI 可以独立跑完，不用把密钥塞进客户端；
- device flow **不需要回调 URL** → 用固定的 `https://github.com/login/device`，
  不需要本地起 HTTP 服务、也不受"回调地址要精确匹配"的折磨；
- scope **可以按用例拆成多个 token** → 登录只要最小权限，仓库权限另算。

```
  用户                  aidevops CLI                GitHub              后端
   │                        │                         │                   │
   │  aidevops login        │                         │                   │
   ├───────────────────────>│                         │                   │
   │                        │ POST /login/device/code │                   │
   │                        │  (client_id, scope=     │                   │
   │                        │   "read:user user:email")                   │
   │                        ├────────────────────────>│                   │
   │                        │<──── device_code ───────┤                   │
   │                        │      user_code          │                   │
   │<── "打开 github.com/login/device 输入 WDJB-MJHT"─┤                   │
   │                        │                         │                   │
   │  在浏览器输码并授权     │                         │                   │
   ├─────────────────────────────────────────────────>│                   │
   │                        │                         │                   │
   │                        │ 轮询 POST /access_token │                   │
   │                        │  (每 interval 秒；       │                   │
   │                        │   处理 slow_down)       │                   │
   │                        ├────────────────────────>│                   │
   │                        │<─── access_token ───────┤                   │
   │                        │                         │                   │
   │                        │ POST /api/v1/auth/github {token}            │
   │                        ├────────────────────────────────────────────>│
   │                        │                         │  GET /user        │
   │                        │                         │<──────────────────┤
   │                        │                         │  GET /user/emails │
   │                        │                         ├─────────────────> │
   │                        │                         │                   │ 按 github_id
   │                        │                         │                   │ 找/建用户
   │                        │<──────── JWT (access + refresh) ────────────┤
   │<──── "Signed in as <login>" ────────────────────┤                   │
```

### 仓库权限走第二条、独立的 device flow

登录拿到的 token 只有 `read:user user:email`，**读不了私有仓库**。等用户第一次
`repo-add` 时再做一次 device flow，这次 scope 要 `repo`，拿到的 token 存进现有的
`GitConnection`（已有 `EncryptedTextField` 加密字段，直接复用）。

好处：授权页第一次很温和（只读公开资料），不会一上来就吓人要私有库权限。

## 4. 后端改动

| 文件 | 改动 |
|---|---|
| `backend/apps/accounts/models.py` | `User` 加 `github_id`（BigInteger，unique，null）、`github_login`（CharField）。`email` 保留（仍是 `USERNAME_FIELD`），但**身份匹配走 `github_id`**。`avatar_url` 已有，GitHub 直接给 |
| `backend/apps/accounts/managers.py` | 加 `create_from_github(github_id, login, email, avatar_url)`，内部 `set_unusable_password()` |
| `backend/apps/accounts/views.py` | 删 `RegisterView`、`LoginView`；新增 `GitHubLoginView`：收 token → 调 GitHub `/user` 验明身份 → get_or_create → 发 JWT。**`RefreshView` / `LogoutView` / `MeView` 不动** |
| `backend/apps/accounts/serializers.py` | 删注册/登录的 serializer；加 `GitHubLoginSerializer{access_token}` |
| `backend/apps/accounts/urls.py` | 删 `auth/register`、`auth/login`；加 `auth/github` |
| `backend/apps/accounts/throttling.py` | 限流从登录端点迁到 `auth/github` |
| `backend/config/settings/*.py` | 加 `GITHUB_CLIENT_ID`、`GITHUB_OAUTH_SCOPES`（登录用）；device flow 无 secret，所以**不需要 client_secret 配置** |
| 迁移 | 一个 migration 加两个字段 |

### 私有邮箱怎么处理（必须处理，否则登录会挂）

GitHub 的 `/user` 在用户勾了"保持邮箱私密"时返回 `email: null`。而本项目的
`User.email` 是 `unique=True` 且是 `USERNAME_FIELD`，不能为空。

三级兜底：

1. `/user` 里的 `email`（公开时最省事）
2. 拿不到 → 调 `GET /user/emails`（需要 `user:email` scope），取 primary + verified
3. 还是拿不到 → 合成 GitHub 官方 noreply 地址：`{github_id}+{login}@users.noreply.github.com`

## 5. CLI 改动

| 文件 | 改动 |
|---|---|
| `cli/ai_devops_cli/commands/auth.py` | `login` 重写为 device flow；`--email/--password` 换成 `--no-browser`（只打印码不自动开浏览器，方便远程/容器）；`logout/me/status/version` 不动 |
| `cli/ai_devops_cli/client.py` | 加 `login_with_github(token)` → `POST /auth/github`；加 `github_device_code()` / `github_poll_token()`（直连 GitHub，不走自家 API） |
| `cli/ai_devops_cli/commands/scope.py` | **补一个 `org-add`**（见下） |
| `cli/ai_devops_cli/config.py` | 缓存 `github_login`，让 `status` 显示 GitHub 账号而不是邮箱 |

### 必须补 `org-add`，否则新用户的 `projects` 永远是空的

`/auth/register` 删掉后，新用户第一次登录**没有任何组织**。好消息是我确认了
`OrganizationViewSet.required_roles["create"] = Role.VIEWER`，注释写着
"any authenticated user may start an organization" —— 所以后端**已经允许**任何登录用户建组织，
创建者自动成为 admin。缺的只是 CLI 没这个命令（现在只有 `orgs` 列表）。

两个选择：

- **A（推荐）**：CLI 补 `aidevops org-add <name>`，用户自己建。控制权清晰。
- **B**：首次 GitHub 登录时后端自动建一个个人组织（如 `{login}'s workspace`）。

A 更符合现有的"用户显式操作"风格；B 更省事但会往用户账户里塞东西。**建议 A**。

## 6. 测试改动（逐用例）

| 文件 | 处理 |
|---|---|
| `test_auth_api.py` | 删 4 个 register 用例、重写 5 个 login 用例（改成 mock GitHub 的 `/user` 与 `/user/emails`，覆盖：新用户创建、老用户命中、私有邮箱兜底、token 无效被拒、inactive 用户被拒）；`me`×4 / `refresh`×1 / `logout`×2 **原样保留** |
| `test_throttling.py` | `test_login_is_throttled` 改成打 `auth/github`；其余 3 个不动 |
| 新增 | `auth/github` 的 device flow 边界：`expired_token`、`access_denied`、`slow_down` 只测后端不够 —— 这几条在 CLI 侧，用 `httpx.MockTransport` 测 |
| `cli/tests/` | 加 device flow 的轮询状态机用例（`authorization_pending` → 继续；最终拿到 token） |

后端对外部 GitHub 的调用一律用 `responses` / `httpx` mock，**CI 不联网**。

## 7. 你需要做的一件事

GitHub 网页 → Settings → Developer settings → **OAuth Apps** → New OAuth App：

| 字段 | 值 |
|---|---|
| Application name | `aidevops` |
| Homepage URL | `https://github.com/zhoujungis/ai_devops` |
| **Enable Device Flow** | **必须勾**（不勾会报 `device_flow_disabled`） |
| Authorization callback URL | 不用管（device flow 不需要） |

建完把 **Client ID** 给我即可。**Client Secret 不用给** —— device flow 根本不用它。

GitHub 没有创建 OAuth App 的 API，所以这步只能网页点。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 本地开发被迫依赖 GitHub | 登录后拿到的是普通 JWT，**后续所有开发与测试都走 `force_authenticate`**，只有"首次登录"这一条路需要 GitHub。影响面已被第 2 节的实测框住 |
| GitHub 不可用时无法登录 | 已登录的会话靠 JWT + refresh，不受影响；只有新登录需要 GitHub |
| 私有邮箱导致建号失败 | 第 4 节的三级兜底 |
| `repo` scope 的 token 存在服务器 | 复用现有 `EncryptedTextField`（与当前存 PAT 的做法一致），权限不比现状更宽 |
| 现有账号 `me@example.com` 迁移 | **已决：不做**。既有的邮箱密码账号**不会被**自动绑定到 GitHub —— 首次 GitHub 登录会**新建**账号。不按邮箱回填 `github_id`，也不提供手工绑定命令：按邮箱自动认领会把「邮箱相同」当成身份证明，那是个安全决定，不该顺手做；且当前部署只有一个可弃的老账号，重建即可 |
| 删端点破坏已有部署 | 目前只有本机一个部署，无外部用户。已加 tag 的 0.1.0 CLI 会因找不到 `/auth/login` 而登录失败 —— 需要发 0.2.0 并说明 |

## 9. 实施顺序（每步都能独立验收）

1. **后端**：User 加字段 + migration + `create_from_github` + `/auth/github`（含私有邮箱兜底）
   → 验收：mock GitHub 的测试全绿；`force_authenticate` 的既有测试一个不挂
2. **删端点**：移除 register/login + 重写那 9 个用例 + 限流迁移
   → 验收：`pytest` 全绿
3. **CLI**：device flow + `org-add` + `status` 显示 GitHub 账号
   → 验收：`cli-verify` 全绿；对你真实 GitHub 账号联调一次
4. **文档 + 发布**：更新 README 与 `docs/local-setup-windows.md`，打 tag `v0.2.0`
   → 验收：`pipx install aidevops==0.2.0` 后能完成一次真实登录

## 10. 要你确认的遗留问题

1. 第 5 节组织创建的 **A（CLI `org-add`）还是 B（自动建个人组织）**？
2. 现有账号 `me@example.com` 要不要做 GitHub 绑定/迁移，还是**直接不要**（本机重建即可）？ —— **已决：不做**（见 §8：不回填、不提供绑定命令）。
3. `0.1.0` 已经发布且登录接口即将消失 —— 接受"发 0.2.0 并告知破坏性变更"，还是要在 0.2.0 里给个过渡期？
