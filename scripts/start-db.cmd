@echo off
REM ---------------------------------------------------------------------------
REM Start the project's local PostgreSQL 18 and verify Redis.
REM
REM PostgreSQL here is a portable EDB build at C:\Users\admin\pgsql18 — no
REM Windows service and no installer, so it does not auto-start with Windows.
REM Double-click this file (or call it from a shortcut under shell:startup) to
REM bring the dev database up. It is safe to run repeatedly.
REM ---------------------------------------------------------------------------

set PGROOT=C:\Users\admin\pgsql18\pgsql
set PGDATA=C:\Users\admin\pgsql18\data
set PGLOG=C:\Users\admin\pgsql18\pg.log

echo [1/2] PostgreSQL 18 on port 5432
"%PGROOT%\bin\pg_ctl.exe" status -D "%PGDATA%" >nul 2>&1
if errorlevel 1 (
    echo       not running - starting ...
    "%PGROOT%\bin\pg_ctl.exe" -D "%PGDATA%" -l "%PGLOG%" -o "-p 5432" start
) else (
    echo       already running.
)

echo [2/2] Redis on port 6379
net start | findstr /i "Redis" >nul
if errorlevel 1 (
    echo       service not running - starting ...
    net start Redis
) else (
    echo       already running.
)

echo.
echo Done. Endpoints:
echo   PostgreSQL  127.0.0.1:5432   db=ai_devops  user=copilot
echo   Redis       127.0.0.1:6379
echo.
pause
