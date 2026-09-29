"""Start PostgreSQL and Django and keep them alive for the life of this process.

Redis already runs as a Windows service, so it is only probed. The terminal client
(``copilot``) talks to Django over HTTP, so these two are the whole stack the CLI
needs.

Why a single long-lived process: the sandbox reaps child processes when a tool
call returns, so each service must be started and *held* inside one command that
stays in the foreground. This script does exactly that -- it blocks until
interrupted, so as long as the background task is alive, the services are too.
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
PGLOG = Path(r"C:\Users\admin\pgsql18\pg.log")
BACKEND = Path(r"D:\ai_devops\backend")
VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"

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
    proc = subprocess.run(
        [str(PGROOT / "bin" / "psql.exe"), "-U", "copilot", "-h", "127.0.0.1",
         "-d", "postgres", "-tAc", "SELECT 1"],
        text=True, capture_output=True,
    )
    return proc.returncode == 0 and (proc.stdout or "").strip() == "1"


def spawn(name: str, cmd: list[str], cwd: Path, log_path: Path) -> None:
    log = open(log_path, "a", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)
    PROCS.append((name, proc))
    print(f"  started {name} (pid {proc.pid}) -> {log_path}", flush=True)


def main() -> int:
    print("== 1/3 PostgreSQL ==", flush=True)
    if pg_queryable():
        print("  already accepting queries on 5432", flush=True)
    else:
        spawn("postgres", [str(PGROOT / "bin" / "postgres.exe"),
                           "-D", str(PGDATA), "-p", "5432"],
              PGROOT, Path(r"C:\Users\admin\pgsql18\pg.log"))
        for _ in range(90):
            if pg_queryable():
                break
            time.sleep(1)
        print("  ready:", pg_queryable(), flush=True)

    print("== 2/3 Redis ==", flush=True)
    print("  port 6379 open:", port_open(6379), flush=True)

    print("== 3/3 Django ==", flush=True)
    if port_open(8000):
        print("  already listening on 8000", flush=True)
    else:
        spawn("django", [str(VENV_PY), "manage.py", "runserver", "127.0.0.1:8000", "--noreload"],
              BACKEND, Path(r"D:\ai_devops\.run\django.log"))
        for _ in range(45):
            if port_open(8000):
                break
            time.sleep(1)
        print("  listening:", port_open(8000), flush=True)

    print("", flush=True)
    print("=== services up ===", flush=True)
    for name, port in (("PostgreSQL", 5432), ("Redis", 6379), ("Django", 8000)):
        print(f"  {name:11} {port}  {'UP' if port_open(port) else 'DOWN'}", flush=True)
    print("", flush=True)
    print("  API docs: http://127.0.0.1:8000/api/docs/", flush=True)
    print("  client:   copilot login && copilot projects && copilot use <slug>", flush=True)

    # Hold the process open so the children are not reaped.
    while True:
        time.sleep(30)
        alive = [n for n, p in PROCS if p.poll() is None]
        dead = [n for n, p in PROCS if p.poll() is not None]
        if dead:
            print(f"  !! exited: {dead}; still alive: {alive}", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        for name, proc in PROCS:
            proc.terminate()
        sys.exit(0)
