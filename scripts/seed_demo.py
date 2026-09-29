"""Seed a demo dataset for the QA Copilot UI.

The product's read APIs are deliberately read-only (findings and recommendations
can only be produced by an agent run), so the interesting screens have nothing
to show until real data has been synced. This script writes a plausible slice
straight through the ORM: git connection -> repository -> modules -> commits ->
risks/tests/bugs/releases -> AI findings and recommendations.

Idempotent: every row is upserted on its natural key, so re-running is safe.

    python manage.py shell < scripts/seed_demo.py

or

    ..\\backend\\.venv\\Scripts\\python.exe scripts\\seed_demo.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import Organization, Project, User  # noqa: E402
from apps.ai.models import (  # noqa: E402
    AIAnalysisJob,
    AIConfirmation,
    AIFinding,
    AIRecommendation,
    ConfirmationDecision,
    FindingSeverity,
    FindingStatus,
    JobStatus,
    RecommendationStatus,
)
from apps.ai.services.findings import finding_dedupe_hash  # noqa: E402
from apps.bugs.models import Bug, BugModuleLink, BugStatus, Severity  # noqa: E402
from apps.codebase.models import (  # noqa: E402
    ChangeType,
    Commit,
    CommitFile,
    CommitModuleImpact,
    Module,
    ModuleKind,
)
from apps.core.enums import Priority  # noqa: E402
from apps.integrations.models import (  # noqa: E402
    ConnectionStatus,
    GitAuthType,
    GitConnection,
    GitProviderKind,
    Repository,
    SyncStatus,
)
from apps.releases.models import Release, ReleaseStatus  # noqa: E402
from apps.requirements.models import (  # noqa: E402
    Requirement,
    RequirementSource,
    RequirementStatus,
)
from apps.testing.models import (  # noqa: E402
    AutomationKind,
    CoverageSnapshot,
    TestCase,
    TestCaseModuleLink,
    TestCaseOrigin,
    TestCaseStatus,
    TestRun,
    TestRunStatus,
    TestRunTrigger,
    TestSuite,
    TestSuiteKind,
)

now = timezone.now()
EMAIL = "me@example.com"


def hr(n: int) -> str:
    """A deterministic fake sha, 40 hex chars.

    Real shas are opaque; what matters for a demo is only that they are stable
    across runs, look plausible, and differ in their first 12 characters (which
    is what the UI displays as ``short_sha``). Deriving them from sha256 of the
    index gives all three without inventing a fake-looking counter.
    """
    return hashlib.sha256(f"demo-commit-{n}".encode()).hexdigest()


@transaction.atomic
def main() -> None:
    user = User.objects.filter(email=EMAIL).first()
    if user is None:
        raise SystemExit(f"user {EMAIL} not found; register it first")

    org = Organization.objects.filter(slug="demo-org").first()
    if org is None:
        raise SystemExit("organization demo-org not found")

    for slug in ("qa-copilot-platform", "payments-gateway"):
        project = Project.objects.filter(org=org, slug=slug).first()
        if project is None:
            print(f"skip {slug}: not found")
            continue
        pruned = prune(project)
        seed_project(project, user)
        print(f"seeded {slug}" + (f" (pruned {pruned} stale rows)" if pruned else ""))


def prune(project: Project) -> int:
    """Drop rows a previous version of this script keyed differently.

    Re-seeding is meant to be idempotent, so anything this script owns but does
    not recognise -- a commit written under an earlier fake-sha scheme, a test
    run anchored to a timestamp that has since moved -- is removed rather than
    left to accumulate.
    """
    removed = 0

    # Only commits this script could have written are considered, and among
    # those only the ones whose sha is not in the current scheme: an earlier
    # revision emitted a readable counter ("000000000001000...") instead of a
    # digest, and those rows are orphans that would otherwise linger in the
    # commit list.
    expected = {hr(n) for n, *_ in COMMIT_SPECS}
    orphans = [
        commit.pk
        for commit in Commit.objects.filter(repository__project=project)
        if commit.sha not in expected
    ]
    if orphans:
        removed += len(orphans)
        Commit.objects.filter(pk__in=orphans).delete()  # cascades to impacts and files

    # Runs are rewritten from scratch rather than matched, because their anchor
    # timestamp shifts between runs and there is no natural key to update on.
    runs = TestRun.objects.filter(project=project)
    removed += runs.count()
    runs.delete()

    return removed


def seed_project(project: Project, user: User) -> None:
    connection, _ = GitConnection.objects.update_or_create(
        org=project.org,
        label="internal-github",
        defaults={
            "provider": GitProviderKind.GITHUB,
            "auth_type": GitAuthType.PAT,
            "base_url": "https://github.com",
            "token": "ghp_demo_token_not_a_real_credential",
            "webhook_secret": "demo-webhook-secret",
            "status": ConnectionStatus.ACTIVE,
            "owner": user,
            "last_verified_at": now - timedelta(minutes=12),
        },
    )

    full_name = (
        "zhoujungis/era_1978" if project.slug == "qa-copilot-platform" else "zhoujungis/blog"
    )
    repository, _ = Repository.objects.update_or_create(
        project=project,
        full_name=full_name,
        defaults={
            "connection": connection,
            "provider": GitProviderKind.GITHUB,
            # Stable rather than hash(): the row is identified by (project, external_id),
            # and Python's hash() is salted per process, which would create a new row
            # on every run.
            "external_id": f"demo-{project.slug}",
            "default_branch": "main",
            "is_private": True,
            "sync_status": SyncStatus.SUCCEEDED,
            "sync_window_days": 90,
            "module_depth": 2,
            "last_synced_at": now - timedelta(minutes=18),
            "sync_error": "",
        },
    )

    modules = seed_modules(project)
    commits = seed_commits(project, repository, modules)
    seed_testing(project, user, modules)
    seed_requirements(project, user)
    seed_bugs(project, user, modules)
    seed_releases(project, user)
    # After bugs/tests/releases exist, so the density, coverage and release
    # signals the AI findings talk about are actually present in the database.
    seed_ai(project, user, commits, modules)


MODULE_SPECS = [
    ("api-gateway", ModuleKind.SERVICE, "apps/gateway/", "python", 0.86, "platform"),
    ("risk-engine", ModuleKind.SERVICE, "apps/risk/", "python", 0.79, "platform"),
    ("sync-worker", ModuleKind.SERVICE, "apps/integrations/", "python", 0.63, "data"),
    ("web-console", ModuleKind.PACKAGE, "frontend/src/", "typescript", 0.55, "web"),
    ("billing-core", ModuleKind.SERVICE, "apps/billing/", "python", 0.92, "payments"),
    ("public-api", ModuleKind.MODULE, "apps/public_api/", "python", 0.48, "platform"),
]


def seed_modules(project: Project) -> dict[str, Module]:
    out: dict[str, Module] = {}
    for name, kind, prefix, language, centrality, team in MODULE_SPECS:
        module, _ = Module.objects.update_or_create(
            project=project,
            path_prefix=prefix,
            defaults={
                "name": name,
                "kind": kind,
                "language": language,
                "centrality_score": centrality,
                "owner_team": team,
            },
        )
        out[prefix] = module
    return out


COMMIT_SPECS = [
    (0, "fix: bound the retry storm when the gateway upstream 504s", 412, 168, 9, ["apps/gateway/"]),
    (1, "feat(risk): weight test failure rate by recency", 268, 74, 6, ["apps/risk/"]),
    (2, "refactor: extract correlation chain into a service", 520, 610, 22, ["apps/risk/", "apps/integrations/"]),
    (3, "chore(deps): bump pgvector driver to 0.8.6", 34, 12, 3, ["apps/integrations/"]),
    (4, "feat(billing): prorate plan changes mid-cycle", 780, 96, 14, ["apps/billing/"]),
    (5, "perf: batch module impact writes on sync", 190, 240, 7, ["apps/integrations/"]),
    (6, "fix(billing): idempotency key collision on retried charge", 145, 58, 5, ["apps/billing/"]),
    (7, "feat(web): risk breakdown table on code impact page", 320, 88, 11, ["frontend/src/"]),
    (8, "test: cover gateway timeout backoff", 96, 8, 4, ["apps/gateway/"]),
    (9, "feat(public-api): cursor pagination for findings", 210, 42, 8, ["apps/public_api/"]),
    (10, "fix(risk): avoid double counting churn for renames", 64, 31, 4, ["apps/risk/"]),
    (11, "docs: document webhook signature verification", 22, 4, 2, ["apps/integrations/"]),
]

AUTHORS = [
    ("周军", EMAIL),
    ("Li Wei", "liwei@example.com"),
    ("Chen Hao", "chenhao@example.com"),
]


def seed_commits(
    project: Project, repository: Repository, modules: dict[str, Module]
) -> list[Commit]:
    out: list[Commit] = []
    for n, message, additions, deletions, files_changed, prefixes in COMMIT_SPECS:
        sha = hr(n)
        author_name, author_email = AUTHORS[n % len(AUTHORS)]
        commit, _ = Commit.objects.update_or_create(
            repository=repository,
            sha=sha,
            defaults={
                "parent_shas": [hr(n - 1)] if n else [],
                "author_name": author_name,
                "author_email": author_email,
                "committer_email": author_email,
                "message": message,
                "committed_at": now - timedelta(hours=6 + n * 9),
                "additions": additions,
                "deletions": deletions,
                "files_changed": files_changed,
                "pull_request_external_id": str(1400 + n) if n % 3 == 0 else "",
            },
        )
        for prefix in prefixes:
            module = modules[prefix]
            weight = round((additions + deletions) / max(1, len(prefixes)) / 400, 3)
            CommitModuleImpact.objects.update_or_create(
                commit=commit,
                module=module,
                defaults={
                    "churn_lines": (additions + deletions) // max(1, len(prefixes)),
                    "file_count": max(1, files_changed // len(prefixes)),
                    "weight": weight,
                    "is_test_change": message.startswith("test:"),
                },
            )
        for i in range(min(3, files_changed)):
            path = f"{prefixes[i % len(prefixes)]}{_file_for(message, i)}"
            CommitFile.objects.update_or_create(
                commit=commit,
                path=path,
                defaults={
                    "change_type": ChangeType.MODIFY if n % 4 else ChangeType.ADD,
                    "additions": max(1, additions // 4),
                    "deletions": max(0, deletions // 4),
                    # No patch text: a real sync stores the provider's diff, and
                    # inventing one here would make `has_patch` lie.
                    "patch": "",
                    "has_patch": False,
                    "language": "python" if path.endswith(".py") else "typescript",
                },
            )
        out.append(commit)
    return out


def _file_for(message: str, i: int) -> str:
    stems = ["views", "services", "models", "tasks", "serializers"]
    stem = stems[i % len(stems)]
    ext = "ts" if message.startswith("feat(web)") else "py"
    return f"{stem}.{ext}"


REQUIREMENT_SPECS = [
    ("QAC-101", "Risk score must explain every signal", RequirementStatus.IMPLEMENTED, Priority.P0),
    ("QAC-102", "AI findings never execute without human confirmation", RequirementStatus.VERIFIED, Priority.P0),
    ("QAC-103", "Sync window configurable per repository", RequirementStatus.IN_PROGRESS, Priority.P1),
    ("QAC-104", "Correlation chain exposes data gaps", RequirementStatus.APPROVED, Priority.P1),
    ("QAC-105", "Coverage badge per module", RequirementStatus.DRAFT, Priority.P2),
]


def seed_requirements(project: Project, user: User) -> None:
    for key, title, status, priority in REQUIREMENT_SPECS:
        Requirement.objects.update_or_create(
            project=project,
            external_key=key,
            defaults={
                "title": title,
                "description": f"{title}. Seeded for local UI review.",
                "source": RequirementSource.MANUAL,
                "status": status,
                "priority": priority,
                "sprint": "2026-Q3",
                "acceptance_criteria": ["Documented", "Covered by a test"],
                "created_by": user,
            },
        )


TEST_CASE_SPECS = [
    ("T-1", "risk score saturates change volume", "apps/risk/", AutomationKind.UNIT),
    ("T-2", "risk breakdown weights sum to the score", "apps/risk/", AutomationKind.UNIT),
    ("T-3", "gateway retries with exponential backoff", "apps/gateway/", AutomationKind.API),
    ("T-4", "gateway gives up after the retry budget", "apps/gateway/", AutomationKind.API),
    ("T-5", "sync writes module impacts once per commit", "apps/integrations/", AutomationKind.UNIT),
    ("T-6", "webhook replays are idempotent", "apps/integrations/", AutomationKind.API),
    ("T-7", "billing proration rounds in the customer's favour", "apps/billing/", AutomationKind.UNIT),
    ("T-8", "duplicate charge is rejected by the idempotency key", "apps/billing/", AutomationKind.UNIT),
    ("T-9", "risk table renders every signal", "frontend/src/", AutomationKind.UI),
]


def seed_testing(project: Project, user: User, modules: dict[str, Module]) -> TestSuite:
    for key, title, prefix, automation in TEST_CASE_SPECS:
        case, _ = TestCase.objects.update_or_create(
            project=project,
            key=key,
            defaults={
                "title": title,
                "precondition": "Seeded fixture data is loaded.",
                "expected": f"{title}.",
                "priority": Priority.P1,
                "tags": ["seed", "regression"],
                "automation": automation,
                "origin": TestCaseOrigin.MANUAL,
                "status": TestCaseStatus.ACTIVE,
                "version": 1,
                "created_by": user,
            },
        )
        TestCaseModuleLink.objects.update_or_create(test_case=case, module=modules[prefix])

    suite, _ = TestSuite.objects.update_or_create(
        project=project,
        name="Nightly regression",
        defaults={
            "kind": TestSuiteKind.REGRESSION,
            "description": "Everything that must stay green before a release.",
        },
    )

    # `prune()` clears runs first, so these are plain inserts. The anchor is
    # rounded to the day: a timestamp with minutes in it would differ on every
    # run, which is fine now but would make the rows impossible to correlate
    # with anything later.
    anchor = now.replace(hour=2, minute=0, second=0, microsecond=0)
    for i in range(6):
        total = 42
        failed = TEST_RUN_FAILURES[i]
        TestRun.objects.create(
            project=project,
            suite=suite,
            environment="ci",
            trigger=TestRunTrigger.CI,
            status=TestRunStatus.FAILED if failed else TestRunStatus.PASSED,
            started_at=anchor - timedelta(days=i),
            finished_at=anchor - timedelta(days=i) + timedelta(minutes=20),
            total=total,
            passed=total - failed - 2,
            failed=failed,
            skipped=2,
        )

    for j, prefix in enumerate(COVERAGE_SPECS):
        CoverageSnapshot.objects.update_or_create(
            project=project,
            module=modules[prefix],
            captured_at=now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=j),
            defaults={
                "line_rate": [0.71, 0.44, 0.88, 0.62][j],
                "branch_rate": [0.60, 0.38, 0.80, 0.51][j],
                "source": "ci",
            },
        )

    return suite


#: Failures per nightly run, oldest first. A couple of red runs make the
#: test-failure-rate signal meaningful rather than a constant zero.
TEST_RUN_FAILURES = [2, 0, 5, 1, 0, 4]

#: Modules that have coverage data, and the rate for each.
COVERAGE_SPECS = ("apps/risk/", "apps/gateway/", "apps/billing/", "apps/integrations/")


BUG_SPECS = [
    ("QAC-201", "Gateway returns 502 on a cold upstream", Severity.S1, BugStatus.OPEN, "apps/gateway/"),
    ("QAC-202", "Proration is off by one day on leap years", Severity.S2, BugStatus.IN_PROGRESS, "apps/billing/"),
    ("QAC-203", "Sync drops the last commit of a window", Severity.S2, BugStatus.OPEN, "apps/integrations/"),
    ("QAC-204", "Risk score ignores renamed files", Severity.S3, BugStatus.RESOLVED, "apps/risk/"),
    ("QAC-205", "Coverage snapshot labelled with the wrong module", Severity.S3, BugStatus.CLOSED, "apps/risk/"),
]


def seed_bugs(project: Project, user: User, modules: dict[str, Module]) -> None:
    for key, title, severity, status, prefix in BUG_SPECS:
        bug, _ = Bug.objects.update_or_create(
            project=project,
            key=key,
            defaults={
                "title": title,
                "description": f"{title}. Seeded for local UI review.",
                "severity": severity,
                "priority": Priority.P1 if severity.rank <= Severity.S2.rank else Priority.P2,
                "status": status,
                "error_type": "TimeoutError" if severity == Severity.S1 else "AssertionError",
                "stack_trace": "Traceback (most recent call last):\n  ...\n"
                if severity == Severity.S1
                else "",
                "environment": "prod" if severity == Severity.S1 else "staging",
                "first_seen_at": now - timedelta(days=9),
                "last_seen_at": now - timedelta(hours=5),
                "occurrence_count": 17 if severity == Severity.S1 else 3,
                "reporter": user,
                "assignee": user,
            },
        )
        BugModuleLink.objects.update_or_create(bug=bug, module=modules[prefix])


RELEASE_SPECS = [
    ("1.4.0", "Release 1.4.0", ReleaseStatus.RELEASED, -12),
    ("1.5.0", "Release 1.5.0", ReleaseStatus.PLANNED, 6),
    ("1.6.0", "Release 1.6.0", ReleaseStatus.PLANNED, 34),
]


def seed_releases(project: Project, user: User) -> None:
    for version, name, status, offset_days in RELEASE_SPECS:
        planned = now + timedelta(days=offset_days)
        Release.objects.update_or_create(
            project=project,
            version=version,
            defaults={
                "name": name,
                "status": status,
                "planned_at": planned,
                "released_at": planned if status == ReleaseStatus.RELEASED else None,
                "notes": f"{name} seeded for local UI review.",
                "created_by": user,
            },
        )


FINDING_SPECS = [
    (
        "code_impact",
        "apps/risk/",
        FindingSeverity.CRITICAL,
        "Risk scoring",
        "apps/risk/ carries 3 of the 5 highest-impact commits this window",
        "Two of them touch the weighting table that every score depends on. "
        "The module has 71% line coverage and no test asserting that the "
        "breakdown still sums to the score.",
        0.82,
    ),
    (
        "bug_investigation",
        "apps/gateway/",
        FindingSeverity.HIGH,
        "Reliability",
        "QAC-201 and the retry-storm fix share a module but no test",
        "The 502 on a cold upstream and the backoff change touch the same retry "
        "path. The regression suite exercises the happy path only.",
        0.74,
    ),
    (
        "test_generation",
        "apps/billing/",
        FindingSeverity.MEDIUM,
        "Test gap",
        "Proration has one case covering a 31-day month",
        "Mid-cycle plan changes on uneven months are untested, and QAC-202 is "
        "already an open S2 in the same code.",
        0.68,
    ),
    (
        "requirement_analysis",
        "apps/public_api/",
        FindingSeverity.LOW,
        "Traceability",
        "QAC-103 has no commit referencing it",
        "The requirement is in progress but nothing in the sync window mentions "
        "its key, so release notes will be inferred rather than traced.",
        0.55,
    ),
    (
        "code_impact",
        "apps/integrations/",
        FindingSeverity.INFO,
        "Hygiene",
        "Repository data is 18 minutes old",
        "Fresh enough to trust, noted only because the freshness penalty is "
        "part of the score.",
        0.9,
    ),
]

RECOMMENDATION_SPECS = [
    (
        "code_impact",
        0,
        "add_guard_test",
        "Add a test asserting breakdown contributions sum to the score",
        "A property test over the default weights pins the invariant that makes "
        "the risk breakdown auditable.",
        "low",
        RecommendationStatus.PENDING,
        None,
    ),
    (
        "bug_investigation",
        1,
        "expand_regression_suite",
        "Cover the gateway retry budget with a regression case",
        "Extend T-4 to assert the second retry waits longer than the first and "
        "that the fifth attempt fails fast.",
        "medium",
        RecommendationStatus.PENDING,
        None,
    ),
    (
        "test_generation",
        2,
        "generate_test_cases",
        "Generate proration cases for 28/30/31-day months",
        "Three cases covering each month length, one of them crossing a leap "
        "year boundary.",
        "low",
        RecommendationStatus.EXECUTED,
        ConfirmationDecision.CONFIRMED,
    ),
]


def seed_ai(
    project: Project,
    user: User,
    commits: list[Commit],
    modules: dict[str, Module],
) -> None:
    jobs: dict[str, AIAnalysisJob] = {}
    for agent_code, prefix, *_rest in FINDING_SPECS:
        job, _ = AIAnalysisJob.objects.update_or_create(
            project=project,
            agent_code=agent_code,
            target_type="module",
            target_id=str(modules[prefix].pk),
            defaults={
                "params": {"window_days": 90},
                "status": JobStatus.SUCCEEDED,
                "progress": 100.0,
                "result": {"findings": 1},
                "requested_by": user,
                "started_at": now - timedelta(minutes=40),
                "finished_at": now - timedelta(minutes=38),
            },
        )
        jobs[f"{agent_code}:{prefix}"] = job

    findings: list[AIFinding] = []
    for i, (agent_code, prefix, severity, category, title, summary, confidence) in enumerate(
        FINDING_SPECS
    ):
        job = jobs[f"{agent_code}:{prefix}"]
        # Same helper the agents use, so a re-run of this script updates the row
        # rather than piling up duplicates under the (project, dedupe_hash) key.
        dedupe = finding_dedupe_hash(
            agent_code=agent_code,
            target_type=job.target_type,
            target_id=job.target_id,
            title=title,
        )
        commit = commits[i % len(commits)]
        finding, _ = AIFinding.objects.update_or_create(
            project=project,
            dedupe_hash=dedupe,
            defaults={
                "job": job,
                "agent_code": agent_code,
                "severity": severity,
                "category": category,
                "title": title,
                "summary": summary,
                "payload": {
                    "module": modules[prefix].path_prefix,
                    "observed": summary,
                    "window_days": 90,
                },
                "confidence": confidence,
                "evidence": [
                    {
                        "kind": "module",
                        "ref_id": str(modules[prefix].pk),
                        "note": modules[prefix].name,
                    },
                    {"kind": "commit", "ref_id": str(commit.pk), "note": commit.short_sha},
                ],
                "status": FindingStatus.NEW,
            },
        )
        findings.append(finding)

    for i, (
        agent_code,
        finding_index,
        rec_type,
        title,
        description,
        risk_level,
        status,
        decision,
    ) in enumerate(RECOMMENDATION_SPECS):
        finding = findings[finding_index]
        recommendation, _ = AIRecommendation.objects.update_or_create(
            project=project,
            finding=finding,
            type=rec_type,
            defaults={
                "job": finding.job,
                "agent_code": agent_code,
                "title": title,
                "description": description,
                "payload": {"action": rec_type, "target": finding.title},
                "risk_level": risk_level,
                "status": status,
                "expires_at": now + timedelta(days=7),
            },
        )
        if decision is None:
            continue
        # The decision lives in its own one-to-one row; the recommendation only
        # records the resulting state.
        AIConfirmation.objects.update_or_create(
            recommendation=recommendation,
            defaults={
                "requested_by": user,
                "decision": decision,
                "edited_payload": {},
                "reason": "Reviewed by the on-call engineer.",
                "executor_code": "test_generation",
                "result": {"created": 3, "skipped": 0},
                "executed_at": now - timedelta(hours=i + 1),
            },
        )


if __name__ == "__main__":
    main()
    print("done")
