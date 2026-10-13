"""Bring the whole local stack up and keep it alive for the life of this process.

PostgreSQL and Redis both run as Windows services now, so they survive on their
own; this script only starts them if they are down, then holds Django and the two
Celery processes, which are not services.

Why a single long-lived process: the sandbox reaps child processes when a tool
call returns, so each service must be started and *held* inside one command that
stays in the foreground. This script blocks until interrupted, so as long as the
background task is alive the services are too.

Celery is part of the stack rather than optional: repository sync and every AI
analysis are queued, so with no worker a sync sits in ``queued`` forever and the
CLI looks broken.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PGROOT = Path(r"C:\Users\admin\pgsql18\pgsql")
PGDATA = Path(r"C:\Users\admin\pgsql18\data")
BACKEND = Path(r"D:\ai_devops\backend")
VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"
RUN = Path(r"D:\ai_devops\.run")

PG_SERVICE = "ai_devops_pg"
REDIS_SERVICE = "Redis"

os.environ["PGPASSWORD"] = "copilot_dev_pw"

PROCS: list[tuple[str, subprocess.Popen[bytes]]] = []


def port_open(port: int) -> bool:
    sock = socket.socket()
    sock.settimeout(1.2)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def pg_queryable() -> bool:
    """Port-open is not enough: during crash recovery every query is rejected."""
    try:
        proc = subprocess.run(
            [str(PGROOT / "bin" / "psql.exe"), "-U", "copilot", "-h", "127.0.0.1",
             "-d", "postgres", "-tAc", "SELECT 1"],
            text=True, capture_output=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip() == "1"


def spawn(name: str, cmd: list[str], cwd: Path, log_path: Path) -> None:
    log = open(log_path, "a", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)
    PROCS.append((name, proc))
    print(f"  started {name} (pid {proc.pid}) -> {log_path}", flush=True)


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)

    print("== 1/4 PostgreSQL ==", flush=True)
    if pg_queryable():
        print("  already accepting queries on 5432", flush=True)
    else:
        # It is registered as a service, so the SCM starts it with a service
        # token -- which is the only way it can run, since PostgreSQL refuses to
        # start under an administrator's token.
        subprocess.run(["net", "start", PG_SERVICE], capture_output=True, text=True)
        for _ in range(60):
            if pg_queryable():
                break
            time.sleep(1)
        print(f"  ready: {pg_queryable()}", flush=True)

    print("== 2/4 Redis ==", flush=True)
    if port_open(6379):
        print("  port 6379 open", flush=True)
    else:
        subprocess.run(["net", "start", REDIS_SERVICE], capture_output=True, text=True)
        for _ in range(30):
            if port_open(6379):
                break
            time.sleep(1)
        print(f"  open: {port_open(6379)}", flush=True)

    print("== 3/4 Django ==", flush=True)
    if port_open(8000):
        print("  already listening on 8000", flush=True)
    else:
        spawn("django",
              [str(VENV_PY), "manage.py", "runserver", "127.0.0.1:8000", "--noreload"],
              BACKEND, RUN / "django.log")
        for _ in range(45):
            if port_open(8000):
                break
            time.sleep(1)
        print(f"  listening: {port_open(8000)}", flush=True)

    print("== 4/4 Celery ==", flush=True)
    spawn("celery-worker",
          [str(VENV_PY), "-m", "celery", "-A", "config", "worker",
           "-Q", "ai,sync,default", "-l", "info", "--pool=solo"],
          BACKEND, RUN / "celery-worker.log")
    spawn("celery-beat",
          [str(VENV_PY), "-m", "celery", "-A", "config", "beat", "-l", "info",
           # Keep the schedule shelve out of the repo; by default beat writes it
           # into the working directory, which here is backend/.
           f"--schedule={RUN / 'celerybeat-schedule'}"],
          BACKEND, RUN / "celery-beat.log")
    time.sleep(8)

    print("", flush=True)
    print("=== services ===", flush=True)
    for name, port in (("PostgreSQL", 5432), ("Redis", 6379), ("Django", 8000)):
        print(f"  {name:11} {port}  {'UP' if port_open(port) else 'DOWN'}", flush=True)
    for name, proc in PROCS:
        print(f"  {name:14} {'RUNNING' if proc.poll() is None else 'EXITED'}", flush=True)
    print("", flush=True)
    print("  API docs: http://127.0.0.1:8000/api/docs/", flush=True)
    print("  readyz  : http://127.0.0.1:8000/readyz", flush=True)
    print("", flush=True)
    print("  client  : D:\\ai_devops\\cli\\.venv\\Scripts\\aidevops.exe", flush=True)

    while True:
        time.sleep(30)
        dead = [n for n, p in PROCS if p.poll() is not None]
        if dead:
            print(f"  !! exited: {dead}", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        for _, proc in PROCS:
            proc.terminate()
        sys.exit(0)
