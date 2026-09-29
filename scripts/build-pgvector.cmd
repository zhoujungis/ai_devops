@echo off
REM Build & install pgvector for the portable PostgreSQL 18 at PGROOT.
REM
REM vcvars64.bat is deliberately NOT used here: it shells out to reg.exe, which is
REM blocked in this sandbox. The MSVC + Windows SDK environment is wired up by hand
REM instead, which is all nmake/cl need.

setlocal

set PGROOT=C:\Users\admin\pgsql18\pgsql
set SRC=C:\Users\admin\pgvector-src

set VCTOOLS=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.44.35207
set SDKROOT=C:\Program Files (x86)\Windows Kits\10
set SDKVER=10.0.26100.0

REM --- PATH: cl.exe, link.exe, nmake.exe, rc.exe ---
set PATH=%VCTOOLS%\bin\Hostx64\x64;%SDKROOT%\bin\%SDKVER%\x64;%PATH%

REM --- INCLUDE: CRT, STL, UCRT, shared SDK headers, um/winrt ---
set INCLUDE=%VCTOOLS%\include;%SDKROOT%\Include\%SDKVER%\ucrt;%SDKROOT%\Include\%SDKVER%\shared;%SDKROOT%\Include\%SDKVER%\um;%SDKROOT%\Include\%SDKVER%\winrt

REM --- LIB: CRT + SDK libs ---
set LIB=%VCTOOLS%\lib\x64;%SDKROOT%\Lib\%SDKVER%\ucrt\x64;%SDKROOT%\Lib\%SDKVER%\um\x64

if not exist "%SRC%\Makefile.win" (
    echo [ERROR] pgvector source not found at %SRC%
    exit /b 1
)

where cl.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] cl.exe not on PATH
    exit /b 1
)

cd /d "%SRC%"
nmake /NOLOGO /F Makefile.win PGROOT="%PGROOT%" install
if errorlevel 1 (
    echo [ERROR] pgvector build failed
    exit /b 1
)

echo [OK] pgvector installed into %PGROOT%
endlocal
