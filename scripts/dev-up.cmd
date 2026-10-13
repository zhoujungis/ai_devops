@echo off
REM ===========================================================================
REM  ai_devops / QA Copilot - 一键启动本地开发环境
REM
REM  依次做四件事：
REM    1. 启动 PostgreSQL 18（Windows 服务 ai_devops_pg，开机自启）
REM    2. 确认 Redis 服务在跑（端口 6379）
REM    3. 启动 Django 开发服务器（http://127.0.0.1:8000）
REM    4. 启动 Celery worker 与 beat（仓库同步和 AI 分析是异步的，没有它们
REM       任务会永远停在 queued）
REM
REM  第 3、4 步各开一个新窗口，关掉窗口即停止对应服务。
REM  客户端是终端程序 aidevops（CLI + TUI），见 cli/。
REM
REM  为什么 PostgreSQL 走服务而不是 pg_ctl start：
REM  PostgreSQL 在 Windows 上拒绝以管理员身份启动（"Execution of PostgreSQL by
REM  a user with administrative permissions is not permitted."）。pg_ctl start 会把
REM  调用者的令牌传给 postgres.exe，所以在管理员终端里必然失败；而 SCM 启动服务
REM  时用的是服务账户令牌，不受影响。因此这里优先用 net start。
REM ===========================================================================

set PGROOT=C:\Users\admin\pgsql18\pgsql
set PGDATA=C:\Users\admin\pgsql18\data
set PGLOG=C:\Users\admin\pgsql18\pg.log
set PGSVC=ai_devops_pg
set VENV=D:\ai_devops\backend\.venv
set BACKEND=D:\ai_devops\backend
set RUNLOG=D:\ai_devops\.run

echo ============================================
echo  [1/4] PostgreSQL 18
echo ============================================
net start %PGSVC% >nul 2>&1
if errorlevel 1 (
    "%PGROOT%\bin\pg_ctl.exe" status -D "%PGDATA%" >nul 2>&1
    if errorlevel 1 (
        echo    service not registered or refused - trying pg_ctl
        echo    NOTE: this fails in an administrator terminal by design.
        "%PGROOT%\bin\pg_ctl.exe" -D "%PGDATA%" -l "%PGLOG%" -o "-p 5432" -w start
    ) else (
        echo    already running.
    )
) else (
    echo    service started.
)
"%PGROOT%\bin\psql.exe" -U copilot -h 127.0.0.1 -d postgres -tAc "SELECT 1" >nul 2>&1
if errorlevel 1 (
    echo    WARNING: 5432 is not accepting queries.
) else (
    echo    accepting queries on 5432.
)

echo.
echo ============================================
echo  [2/4] Redis
echo ============================================
net start Redis >nul 2>&1
if errorlevel 1 (
    echo    already running.
) else (
    echo    service started.
)

echo.
echo ============================================
echo  [3/4] Django  http://127.0.0.1:8000
echo ============================================
start "ai_devops - Django" cmd /k "cd /d %BACKEND% && %VENV%\Scripts\python.exe manage.py runserver"

echo.
echo ============================================
echo  [4/4] Celery
echo ============================================
start "ai_devops - Celery worker" cmd /k "cd /d %BACKEND% && %VENV%\Scripts\python.exe -m celery -A config worker -Q ai,sync,default -l info --pool=solo"
start "ai_devops - Celery beat" cmd /k "cd /d %BACKEND% && %VENV%\Scripts\python.exe -m celery -A config beat -l info --schedule=%RUNLOG%\celerybeat-schedule"

echo.
echo 启动完成。常用地址：
echo   API        http://127.0.0.1:8000
echo   Swagger    http://127.0.0.1:8000/api/docs/
echo   就绪探针   http://127.0.0.1:8000/readyz
echo.
echo 终端客户端（另开一个终端）：
echo   cd /d D:\ai_devops\cli
echo   .venv\Scripts\aidevops.exe login
echo   .venv\Scripts\aidevops.exe projects
echo   .venv\Scripts\aidevops.exe use qa-copilot-platform
echo   .venv\Scripts\aidevops.exe findings
echo   .venv\Scripts\aidevops.exe tui
echo.
echo 首次使用需要建个账号（或用 aidevops login 登录已有账号）：
echo   curl -X POST http://127.0.0.1:8000/api/v1/auth/register ^
echo     -H "Content-Type: application/json" ^
echo     -d "{\"email\":\"me@example.com\",\"password\":\"devpass12345\"}"
echo.
pause
