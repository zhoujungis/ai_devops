@echo off
REM ===========================================================================
REM  ai_devops / QA Copilot - 一键启动后端开发环境
REM
REM  依次做三件事：
REM    1. 启动 PostgreSQL 18（便携版，C:\Users\admin\pgsql18）
REM    2. 确认 Redis 服务在跑（端口 6379）
REM    3. 启动 Django 开发服务器（http://127.0.0.1:8000）
REM
REM  第 3 步开一个新窗口，关掉窗口即停止服务。
REM  前端已移除，客户端改为终端程序 `copilot`（见 cli/）。
REM ===========================================================================

set PGROOT=C:\Users\admin\pgsql18\pgsql
set PGDATA=C:\Users\admin\pgsql18\data
set PGLOG=C:\Users\admin\pgsql18\pg.log
set VENV=D:\ai_devops\backend\.venv
set BACKEND=D:\ai_devops\backend

echo ============================================
echo  [1/3] PostgreSQL 18
echo ============================================
"%PGROOT%\bin\pg_ctl.exe" status -D "%PGDATA%" >nul 2>&1
if errorlevel 1 (
    echo    启动中 ...
    "%PGROOT%\bin\pg_ctl.exe" -D "%PGDATA%" -l "%PGLOG%" -o "-p 5432" -w start
) else (
    echo    已在运行。
)

echo.
echo ============================================
echo  [2/3] Redis
echo ============================================
sc query Redis | findstr /i "RUNNING" >nul 2>&1
if errorlevel 1 (
    echo    启动 Redis 服务 ...
    net start Redis
) else (
    echo    已在运行。
)

echo.
echo ============================================
echo  [3/3] Django  http://127.0.0.1:8000
echo ============================================
start "ai_devops - Django" cmd /k "cd /d %BACKEND% && %VENV%\Scripts\python.exe manage.py runserver"

echo.
echo 启动完成。常用地址：
echo   API        http://127.0.0.1:8000
echo   Swagger    http://127.0.0.1:8000/api/docs/
echo   就绪探针   http://127.0.0.1:8000/readyz
echo.
echo 终端客户端（另开一个终端）：
echo   cd /d D:\ai_devops\cli  ^&^&  .venv\Scripts\copilot.exe login
echo   .venv\Scripts\copilot.exe projects
echo   .venv\Scripts\copilot.exe use qa-copilot-platform
echo   .venv\Scripts\copilot.exe findings
echo.
echo 首次使用需要建个账号（或用 copilot login 通过已有的账号登录）：
echo   curl -X POST http://127.0.0.1:8000/api/v1/auth/register ^
echo     -H "Content-Type: application/json" ^
echo     -d "{\"email\":\"me@example.com\",\"password\":\"devpass12345\"}"
echo.
pause
