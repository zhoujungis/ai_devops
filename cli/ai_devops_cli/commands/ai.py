"""The AI surface: findings, proposals, the confirmation flow, and analyses.

``approve`` and ``reject`` are the terminal face of the product's central promise —
an AI suggestion changes nothing until a human says so. They call the same
confirm/reject endpoints the web client would, so the executor registry and the
audit trail are the backend's, not the client's.
"""

from __future__ import annotations

import time
from typing import Any

import typer

from ai_devops_cli import context, output
from ai_devops_cli.errors import CliError

_FINISHED = {"succeeded", "failed", "cancelled"}

#: Mirrors the backend's FindingStatus choices.
_FINDING_STATUSES = ("new", "acknowledged", "dismissed", "converted")


def findings(
    severity: str | None = typer.Option(None, "--severity", help="info|low|medium|high|critical"),
    status: str | None = typer.Option(None, "--status", help="new|acknowledged|dismissed|converted"),
    agent: str | None = typer.Option(None, "--agent", help="Filter by agent code."),
    worst_first: bool = typer.Option(
        False, "--worst-first", help="Order by severity, critical first, instead of recency."
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="How many findings to list."),
) -> None:
    """List AI findings for the selected project."""
    params: dict[str, Any] = {}
    if severity:
        params["severity"] = severity
    if status:
        params["status"] = status
    if agent:
        params["agent_code"] = agent
    if worst_first:
        # The API ranks this field, so the server returns critical first — not the
        # alphabetical order the raw column would give.
        params["ordering"] = "-severity"

    rows = context.client().results(f"{context.project_base()}/ai/findings", limit=limit, **params)
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Findings",
        ["SEVERITY", "AGENT", "TITLE", "CONF", "STATUS"],
        [
            [
                finding["severity"],
                finding["agent_code"],
                finding["title"],
                f"{finding['confidence'] * 100:.0f}%",
                finding["status"],
            ]
            for finding in rows
        ],
        empty="No findings yet. Run an analysis, for example: copilot analyse -a code_impact --target-id <sha>.",
    )


def finding(finding_id: str = typer.Argument(..., help="Finding id.")) -> None:
    """Show one finding in full, including its evidence and payload."""
    record = context.client().get(f"{context.project_base()}/ai/findings/{finding_id}")
    if output.is_json():
        output.emit(record)
        return
    output.kv(
        [
            ("title", record["title"]),
            ("severity", record["severity"]),
            ("agent", record["agent_code"]),
            ("confidence", f"{record['confidence'] * 100:.0f}%"),
            ("status", record["status"]),
            ("summary", record["summary"]),
        ]
    )
    output.table(
        "Evidence",
        ["KIND", "REF", "NOTE"],
        [[item["kind"], item["ref_id"], item.get("note", "")] for item in record.get("evidence", [])],
        empty="No evidence references.",
    )


def triage(
    finding_id: str = typer.Argument(..., help="Finding id (see 'copilot findings')."),
    status: str = typer.Argument(..., help="new|acknowledged|dismissed|converted."),
) -> None:
    """Record what you decided to do about an AI finding.

    The finding's content is the model's and is never edited; its status is the human
    judgement, and the change is written to the audit log server-side.
    """
    if status not in _FINDING_STATUSES:
        raise CliError(f"Unknown status {status!r}. One of: {', '.join(_FINDING_STATUSES)}.")

    result = context.client().post(
        f"{context.project_base()}/ai/findings/{finding_id}/status", json={"status": status}
    )
    if output.is_json():
        output.emit(result)
        return
    output.success(f"Finding {finding_id} is now {result['status']}.")


def proposals(
    status: str = typer.Option("pending", "--status", help="pending|executed|rejected|failed|expired"),
    limit: int = typer.Option(50, "--limit", "-n", help="How many proposals to list."),
) -> None:
    """List AI proposals and their state."""
    rows = context.client().results(
        f"{context.project_base()}/ai/recommendations",
        limit=limit,
        **({"status": status} if status else {}),
    )
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Proposals",
        ["ID", "TYPE", "TITLE", "RISK", "STATUS"],
        [
            [row["id"], row["type"], row["title"], row["risk_level"], row["status"]]
            for row in rows
        ],
        empty="Nothing here.",
    )


def approve(
    recommendation_id: str = typer.Argument(..., help="Proposal id (see 'copilot proposals')."),
) -> None:
    """Confirm a proposal: a registered executor runs it and the audit log records it."""
    result = context.client().post(
        f"{context.project_base()}/ai/recommendations/{recommendation_id}/confirm", json={}
    )
    if output.is_json():
        output.emit(result)
        return
    output.success(f"Approved. Proposal is now {result['status']}.")
    confirmation = result.get("confirmation") or {}
    created = (confirmation.get("result") or {}).get("created") or []
    if created:
        output.table(
            "Created",
            ["TYPE", "KEY", "ID"],
            [[item.get("type", ""), item.get("key", ""), item.get("id", "")] for item in created],
        )


def reject(
    recommendation_id: str = typer.Argument(..., help="Proposal id."),
    reason: str = typer.Option("", "--reason", "-r", help="Why it was declined."),
) -> None:
    """Decline a proposal. Nothing is written to the domain."""
    result = context.client().post(
        f"{context.project_base()}/ai/recommendations/{recommendation_id}/reject",
        json={"reason": reason},
    )
    if output.is_json():
        output.emit(result)
        return
    output.success(f"Rejected. Proposal is now {result['status']}; no changes were made.")


def agents() -> None:
    """List the analysis agents that can be run."""
    rows = context.client().get(f"{context.project_base()}/ai/analyses/agents")
    if output.is_json():
        output.emit(rows)
        return
    output.table("Agents", ["CODE", "DESCRIPTION"], [[row["code"], row["description"]] for row in rows])


def analyse(
    agent: str = typer.Option(..., "--agent", "-a", help="Agent code (see 'copilot agents')."),
    target_type: str = typer.Option(
        "", "--target-type", help="Target kind, e.g. commit / requirement / module / bug."
    ),
    target_id: str = typer.Option("", "--target-id", help="Target id, or a commit sha."),
    param: list[str] | None = typer.Option(
        None, "--param", "-P", help="Extra analyser param 'key=value' (repeatable)."
    ),
    wait: bool = typer.Option(False, "--wait", "-w", help="Poll until the job finishes."),
    interval: float = typer.Option(2.0, "--interval", help="Seconds between polls."),
    timeout: float = typer.Option(600.0, "--timeout", help="Give up after this many seconds."),
) -> None:
    """Queue an analysis. Returns a job id immediately unless --wait is given."""
    params: dict[str, Any] = {}
    for item in param or []:
        if "=" not in item:
            raise CliError(f"--param expects key=value, got {item!r}.")
        key, value = item.split("=", 1)
        params[key] = value

    body: dict[str, Any] = {"agent": agent, "params": params}
    if target_type:
        body["target_type"] = target_type
    if target_id:
        body["target_id"] = target_id

    client = context.client()
    job = client.post(f"{context.project_base()}/ai/analyses", json=body)
    job_id = job["id"]

    if not wait:
        if output.is_json():
            output.emit(job)
            return
        output.success(f"Queued job {job_id} (status {job['status']}). Poll with: copilot job {job_id}")
        return

    deadline = time.monotonic() + timeout
    # A spinner writes to stdout. In --json mode that would sit inside the payload a
    # script is about to parse, so it is only shown when the output is for a human.
    spinner = None if output.is_json() else output.console.status(f"Waiting for job {job_id}…")
    if spinner is not None:
        spinner.start()
    try:
        while job["status"] not in _FINISHED:
            if time.monotonic() >= deadline:
                raise CliError(f"Job {job_id} did not finish within {timeout:.0f}s.")
            time.sleep(interval)
            job = client.get(f"{context.project_base()}/ai/jobs/{job_id}")
            if spinner is not None:
                spinner.update(f"Waiting for job {job_id}… ({job['status']})")
    finally:
        if spinner is not None:
            spinner.stop()

    if output.is_json():
        output.emit(job)
    elif job["status"] == "succeeded":
        output.success(f"Job {job_id} succeeded.")
        output.kv(
            [
                ("findings", ", ".join(job.get("findings") or []) or "(none)"),
                ("tokens", job.get("usage", {}).get("input_tokens", 0)),
            ]
        )
    else:
        output.warn(f"Job {job_id} ended as {job['status']}: {job.get('error') or '(no detail)'}")

    if job["status"] != "succeeded":
        # `--wait` exists for scripting, and a failed analysis must not look like a
        # successful command: the exit code is the only signal a shell reads.
        raise SystemExit(1)


def job(job_id: str = typer.Argument(..., help="Job id.")) -> None:
    """Show the status and result of an analysis job."""
    record = context.client().get(f"{context.project_base()}/ai/jobs/{job_id}")
    if output.is_json():
        output.emit(record)
        return
    output.kv(
        [
            ("id", record["id"]),
            ("agent", record["agent_code"]),
            ("status", record["status"]),
            ("progress", record.get("progress")),
            ("error", record.get("error") or "-"),
            ("findings", ", ".join(record.get("findings") or []) or "-"),
        ]
    )


def analyses(
    status: str | None = typer.Option(
        None, "--status", help="queued|running|succeeded|failed|cancelled."
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="How many jobs to list."),
) -> None:
    """List past analysis jobs, newest first."""
    params: dict[str, Any] = {"ordering": "-created_at"}
    if status:
        params["status"] = status

    rows = context.client().results(
        f"{context.project_base()}/ai/analyses", limit=limit, **params
    )
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Analyses",
        ["ID", "AGENT", "TARGET", "STATUS", "CREATED"],
        [
            [
                job["id"],
                job["agent_code"],
                job.get("target_type") or "-",
                job["status"],
                output.timestamp(job["created_at"]),
            ]
            for job in rows
        ],
        empty="No analyses yet. Run one with 'copilot analyse'.",
    )


def trace(job_id: str = typer.Argument(..., help="Job id (see 'copilot analyses').")) -> None:
    """Show what an analysis actually did: every model run and tool call.

    The job endpoint reports the outcome; this reports how it was reached — prompt
    version, model, tokens, latency, which tools read which rows. It is what makes an
    answer checkable instead of something to take on faith.
    """
    runs = context.client().get(f"{context.project_base()}/ai/jobs/{job_id}/trace")
    if output.is_json():
        output.emit(runs)
        return

    output.table(
        "Model runs",
        ["#", "MODEL", "PROMPT", "VER", "IN", "OUT", "MS", "STATUS"],
        [
            [
                run["sequence"],
                run["model"],
                run["prompt_id"] or "-",
                run["prompt_version"],
                run["input_tokens"],
                run["output_tokens"],
                run["latency_ms"],
                run["status"],
            ]
            for run in runs
        ],
        empty="No model runs recorded for this job.",
    )

    calls: list[list[Any]] = []
    for run in runs:
        for call in run.get("tool_calls", []):
            calls.append(
                [
                    run["sequence"],
                    call["sequence"],
                    call["tool_name"],
                    call["status"],
                    "yes" if call["scope_denied"] else "",
                    call["duration_ms"],
                ]
            )
    output.table(
        "Tool calls",
        ["RUN", "#", "TOOL", "STATUS", "DENIED", "MS"],
        calls,
        empty="No tool calls were made.",
    )
