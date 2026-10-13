@echo off
REM ---------------------------------------------------------------------------
REM Start the project's local PostgreSQL 18 and verify Redis.
REM
REM PostgreSQL is registered as the Windows service ai_devops_pg and is set to
REM start automatically, so normally there is nothing to do here. This script is
REM for when the service has been stopped (or the machine has just had the
REM service disabled).
REM
REM Why the service rather than pg_ctl start: PostgreSQL refuses to run under an
REM administrator's token ("Execution of PostgreSQL by a user with administrative
REM permissions is not permitted."). pg_ctl start hands the caller's token to
REM postgres.exe, so it fails in an administrator terminal; the SCM starts a
REM service with a service token and is unaffected.
REM
REM It is safe to run repeatedly.
REM ---------------------------------------------------------------------------

set PGROOT=C:\Users\admin\pgsql18\pgsql
set PGDATA=C:\Users\admin\pgsql18\data
set PGLOG=C:\Users\admin\pgsql18\pg.log
set PGSVC=ai_devops_pg

echo [1/2] PostgreSQL 18 on port 5432
net start %PGSVC% >nul 2>&1
if errorlevel 1 (
    "%PGROOT%\bin\pg_ctl.exe" status -D "%PGDATA%" >nul 2>&1
    if errorlevel 1 (
        echo       service unavailable - falling back to pg_ctl
        echo       NOTE: this fails in an administrator terminal by design.
        "%PGROOT%\bin\pg_ctl.exe" -D "%PGDATA%" -l "%PGLOG%" -o "-p 5432" start
    ) else (
        echo       already running.
    )
) else (
    echo       service started.
)
"%PGROOT%\bin\psql.exe" -U copilot -h 127.0.0.1 -d postgres -tAc "SELECT 1" >nul 2>&1
if errorlevel 1 (
    echo       WARNING: 5432 is not accepting queries.
) else (
    echo       accepting queries on 5432.
)

echo [2/2] Redis on port 6379
net start Redis >nul 2>&1
if errorlevel 1 (
    echo       already running.
) else (
    echo       service started.
)

echo.
echo Done. Endpoints:
echo   PostgreSQL  127.0.0.1:5432   db=ai_devops  user=copilot
echo   Redis       127.0.0.1:6379
echo.
pause
