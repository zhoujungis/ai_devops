"""Bring up the local PostgreSQL + pgvector and apply Django migrations.

Run with the project venv's python:
    D:\\ai_devops\\backend\\.venv\\Scripts\\python.exe D:\\ai_devops\\scripts\\dev_db_up.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PGROOT = Path(r"C:\Users\admin\pgsql18\pgsql")
PGDATA = Path(r"C:\Users\admin\pgsql18\data")
PGLOG = Path(r"C:\Users\admin\pgsql18\pg.log")
BACKEND = Path(r"D:\ai_devops\backend")

PG_CTL = PGROOT / "bin" / "pg_ctl.exe"
PSQL = PGROOT / "bin" / "psql.exe"

os.environ["PGPASSWORD"] = "copilot_dev_pw"


def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, text=True, capture_output=True, **kw)  # type: ignore[arg-type]


def show(proc: subprocess.CompletedProcess[str], keep: tuple[str, ...] = ()) -> None:
    for stream in (proc.stdout, proc.stderr):
        for line in (stream or "").splitlines():
            if not keep or any(k in line for k in keep):
                print("   ", line, flush=True)


def pg_running() -> bool:
    """True when PostgreSQL actually answers a query, not merely accepts TCP.

    During crash recovery the port is open but every query is rejected with
    "the database system is starting up", so a bare socket probe is not enough.
    """
    proc = subprocess.run(
        [str(PSQL), "-U", "copilot", "-h", "127.0.0.1", "-p", "5432", "-d", "postgres",
         "-tAc", "SELECT 1"],
        text=True, capture_output=True, env={**os.environ, "PGPASSWORD": "copilot_dev_pw"},
    )
    return proc.returncode == 0 and (proc.stdout or "").strip() == "1"


def start_postgres() -> None:
    """Start PostgreSQL and block until it accepts connections.

    ``pg_ctl -w`` is unreliable here: the sandbox reaps the daemonised child as
    soon as the tool call returns, so pg_ctl reports a start that never sticks.
    Launching ``postgres.exe`` directly inside the same process tree keeps the
    server alive for the duration of this script.
    """
    import socket
    import time

    if pg_running():
        print("    already accepting queries on 5432", flush=True)
        return

    log = open(PGLOG, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [str(PGROOT / "bin" / "postgres.exe"), "-D", str(PGDATA), "-p", "5432"],
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=str(PGROOT),
    )
    print("    postgres.exe pid:", proc.pid, flush=True)

    for _ in range(90):
        if proc.poll() is not None:
            print("    postgres.exe exited early with code", proc.returncode, flush=True)
            break
        if pg_running():
            print("    accepting queries on 5432", flush=True)
            return
        time.sleep(1)
    else:
        print("    timed out waiting for PostgreSQL to become queryable", flush=True)

    # Keep the handle alive; the caller runs the rest of the pipeline.
    globals()["_PG_PROC"] = proc


def main() -> int:
    print("== 1. start PostgreSQL ==", flush=True)
    start_postgres()

    print("== 2. pgvector extension ==", flush=True)
    ext = run([str(PSQL), "-U", "copilot", "-h", "127.0.0.1", "-p", "5432", "-d", "ai_devops",
               "-c", "CREATE EXTENSION IF NOT EXISTS vector;"])
    show(ext, ("CREATE EXTENSION", "ERROR", "already exists"))
    ver = run([str(PSQL), "-U", "copilot", "-h", "127.0.0.1", "-p", "5432", "-d", "ai_devops",
               "-tAc", "SELECT extname || ' ' || extversion FROM pg_extension WHERE extname = 'vector';"])
    print("    vector extension:", (ver.stdout or "").strip() or "(missing)", flush=True)

    print("== 3. django migrate ==", flush=True)
    mig = subprocess.run([sys.executable, "manage.py", "migrate"], cwd=BACKEND,
                         text=True, capture_output=True)
    show(mig, ("Applying", "No migrations", "OK", "ERROR", "Error", "Traceback"))
    print("    migrate exit code:", mig.returncode, flush=True)

    print("== 4. django check ==", flush=True)
    chk = subprocess.run([sys.executable, "manage.py", "check"], cwd=BACKEND,
                         text=True, capture_output=True)
    show(chk, ("System check", "issues", "ERROR"))
    return mig.returncode


if __name__ == "__main__":
    raise SystemExit(main())
