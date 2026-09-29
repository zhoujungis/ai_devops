"""Smoke-check the seeded demo data through the public HTTP API.

Reading through HTTP rather than the ORM is the point: it proves the UI's
contract is satisfied, not merely that rows exist. Run with the dev services up.

    backend\\.venv\\Scripts\\python.exe scripts/verify_demo_api.py
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = "me@example.com"
PASSWORD = "devpass12345"
ORG = "42f7b0a3-0e36-405b-93e6-8a0fbb2d3346"
PROJECT = "303d93cf-fa4c-4538-b868-6a35bd52e88d"


def call(path: str, token: str | None = None, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:300].decode(errors="replace")}


def main() -> None:
    status, body = call("/auth/login", method="POST", body={"email": EMAIL, "password": PASSWORD})
    assert status == 200, f"login failed: {status} {body}"
    token = body["access"]
    print(f"login                      {status}")

    status, body = call(f"/orgs/{ORG}/git-connections", token)
    print(f"git-connections            {status} count={body.get('count')}")
    for row in body.get("results", []):
        print(f"    {row['label']:<18} {row['provider']}/{row['auth_type']} "
              f"has_token={row['has_token']} status={row['status']}")

    status, body = call(f"/orgs/{ORG}/projects/{PROJECT}/repositories", token)
    print(f"repositories               {status} count={body.get('count')}")
    for row in body.get("results", []):
        print(f"    {row['full_name']:<26} {row['default_branch']} "
              f"sync={row['sync_status']} window={row['sync_window_days']}d")

    status, body = call(f"/orgs/{ORG}/projects/{PROJECT}/modules", token)
    print(f"modules                    {status} count={body.get('count')}")

    status, body = call(f"/orgs/{ORG}/projects/{PROJECT}/commits", token)
    print(f"commits                    {status} count={body.get('count')}")
    commits = body.get("results", [])
    for row in commits[:3]:
        print(f"    {row['short_sha']} {row['message'][:52]}")

    if commits:
        first = commits[0]["id"]
        status, body = call(f"/orgs/{ORG}/projects/{PROJECT}/commits/{first}/risk", token)
        print(f"commit risk                {status}")
        print(f"    score={body.get('score')} level={body.get('level')}")
        for item in body.get("breakdown", []):
            print(
                f"      {item['signal']:<26} contrib={item['contribution']:>5} "
                f"w={item['weight']:<4} {item['detail']}"
            )

        status, body = call(f"/orgs/{ORG}/projects/{PROJECT}/commits/{first}/explain", token)
        print(f"commit explain             {status}")
        if status == 200:
            print(f"    modules={len(body.get('modules', []))} "
                  f"regression_candidates={len(body.get('regression_candidates', []))} "
                  f"historical_bugs={len(body.get('historical_bugs', []))}")
            for gap in body.get("data_gaps", []):
                print(f"    gap: {gap}")

    status, body = call(f"/orgs/{ORG}/projects/{PROJECT}/ai/findings", token)
    print(f"findings                   {status} count={body.get('count')}")
    for row in body.get("results", []):
        print(f"    [{row['severity']:<8}] {row['agent_code']:<20} {row['title'][:56]}")

    status, body = call(f"/orgs/{ORG}/projects/{PROJECT}/ai/recommendations", token)
    print(f"recommendations            {status} count={body.get('count')}")
    for row in body.get("results", []):
        print(f"    {row['status']:<10} {row['type']:<24} {row['title'][:50]}")

    for path, label in (
        (f"/orgs/{ORG}/projects/{PROJECT}/bugs", "bugs"),
        (f"/orgs/{ORG}/projects/{PROJECT}/test-cases", "test-cases"),
        (f"/orgs/{ORG}/projects/{PROJECT}/test-runs", "test-runs"),
        (f"/orgs/{ORG}/projects/{PROJECT}/requirements", "requirements"),
        (f"/orgs/{ORG}/projects/{PROJECT}/releases", "releases"),
    ):
        status, body = call(path, token)
        print(f"{label:<26} {status} count={body.get('count')}")


if __name__ == "__main__":
    main()
