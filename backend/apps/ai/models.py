"""AI bookkeeping: providers, pricing, analysis jobs and their traces.

Nothing here calls a model. These are the tables that make an AI answer
*auditable*: which provider, which model, which prompt version, what it cost, and
which tools it called while deciding.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.db import models

from apps.accounts.models import Organization, Project
from apps.core.models import BaseModel, EncryptedTextField
from apps.core.scoping import ScopedModel


class AIProviderType(models.TextChoices):
    OPENAI = "openai", "OpenAI"
    DEEPSEEK = "deepseek", "DeepSeek"
    QWEN = "qwen", "Qwen"
    OLLAMA = "ollama", "Ollama"
    #: Anything that speaks the OpenAI wire format, including self-hosted gateways.
    OPENAI_COMPATIBLE = "openai_compatible", "OpenAI-compatible"


class ProviderStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INVALID = "invalid", "Invalid credentials"
    DISABLED = "disabled", "Disabled"


class AIProviderConfig(BaseModel, ScopedModel):
    """One configured model endpoint, owned by an organization.

    Business code never names a model. It asks for a *capability* ("chat",
    "embedding") and this row decides which provider and model serve it, which is
    what keeps a model swap out of the business layer.
    """

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="ai_providers")
    provider_type = models.CharField(max_length=32, choices=AIProviderType.choices)
    label = models.CharField(max_length=100)
    #: Empty means "use the provider preset's default endpoint".
    base_url = models.URLField(blank=True, default="")
    api_key = EncryptedTextField(blank=True, default="")
    #: capability -> model name, e.g. {"chat": "gpt-4o", "embedding": "text-embedding-3-large"}.
    capability_models = models.JSONField(default=dict, blank=True)
    #: Must match the pgvector column dimension of whatever it embeds into.
    embedding_dim = models.PositiveIntegerField(default=1536)
    is_default = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16, choices=ProviderStatus.choices, default=ProviderStatus.ACTIVE
    )
    last_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("label",)
        constraints = [
            models.UniqueConstraint(fields=["org", "label"], name="uniq_ai_provider_org_label"),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.provider_type})"

    def model_for(self, capability: str) -> str:
        models_by_capability: dict[str, str] = self.capability_models or {}
        return models_by_capability.get(capability, "")

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[AIProviderConfig]:
        return cls.objects.filter(org__in=Organization.objects.accessible_to(user))


class AIModelPricing(BaseModel):
    """Reference prices, so a run's cost is computed rather than guessed.

    Global rather than per-organization: prices come from the vendor and are the
    same for everyone. A missing row means the cost is unknown, which is recorded
    as such instead of being reported as zero.
    """

    provider_type = models.CharField(max_length=32, choices=AIProviderType.choices)
    model = models.CharField(max_length=120)
    input_per_1k = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    output_per_1k = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    cached_input_per_1k = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    embedding_per_1k = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    currency = models.CharField(max_length=8, default="USD")
    effective_from = models.DateTimeField()

    class Meta(BaseModel.Meta):
        ordering = ("-effective_from",)
        constraints = [
            models.UniqueConstraint(
                fields=["provider_type", "model", "effective_from"],
                name="uniq_ai_pricing_provider_model_from",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider_type}/{self.model}"


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class AIAnalysisJob(BaseModel, ScopedModel):
    """One unit of asynchronous analysis.

    An HTTP request only ever creates one of these and returns; the work itself
    happens on a worker, because an LLM call has no business blocking a request.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="ai_jobs")
    agent_code = models.CharField(max_length=64)
    #: What the agent is being run against. A string rather than a UUID because the
    #: target may be any entity, and not all of them are guaranteed to be UUIDs.
    target_type = models.CharField(max_length=64, blank=True, default="")
    target_id = models.CharField(max_length=64, blank=True, default="")
    params = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=16, choices=JobStatus.choices, default=JobStatus.QUEUED)
    progress = models.FloatField(default=0.0)
    error = models.TextField(blank=True, default="")
    #: The structured agent output, stored so the UI can re-read it without re-running.
    result = models.JSONField(default=dict, blank=True)

    requested_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_jobs",
    )
    celery_task_id = models.CharField(max_length=120, blank=True, default="")
    #: Callers that retry a POST should get the same job back, not a second one.
    idempotency_key = models.CharField(max_length=128, blank=True, default="")

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_ai_job_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"], name="idx_aijob_project_status"),
        ]

    def __str__(self) -> str:
        return f"{self.agent_code} ({self.status})"

    @property
    def is_finished(self) -> bool:
        return self.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[AIAnalysisJob]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class RunStatus(models.TextChoices):
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class AIAnalysisRun(BaseModel):
    """One model interaction inside a job.

    An agent may make several calls (a tool loop, a repair retry), so a job has
    many runs. This is the row that answers "what exactly did we send, to which
    model, and what did it cost".
    """

    job = models.ForeignKey(AIAnalysisJob, on_delete=models.CASCADE, related_name="runs")
    sequence = models.PositiveSmallIntegerField(default=1)
    provider_type = models.CharField(max_length=32)
    model = models.CharField(max_length=120)
    capability = models.CharField(max_length=32, default="chat")

    prompt_id = models.CharField(max_length=64, blank=True, default="")
    prompt_version = models.PositiveSmallIntegerField(default=0)

    #: Digest identifies the exact input; the text is capped so one huge diff
    #: cannot bloat the table. `input_truncated` says whether the cap bit.
    input_digest = models.CharField(max_length=64, blank=True, default="")
    input_text = models.TextField(blank=True, default="")
    input_truncated = models.BooleanField(default=False)

    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cached_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    #: Null means "pricing unknown", which must not be reported as free.
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)

    status = models.CharField(max_length=16, choices=RunStatus.choices)
    error = models.TextField(blank=True, default="")
    output_text = models.TextField(blank=True, default="")

    class Meta(BaseModel.Meta):
        ordering = ("job", "sequence")
        constraints = [
            models.UniqueConstraint(fields=["job", "sequence"], name="uniq_ai_run_job_sequence"),
        ]

    def __str__(self) -> str:
        return f"{self.model} #{self.sequence}"


class ToolCallStatus(models.TextChoices):
    OK = "ok", "Ok"
    ERROR = "error", "Error"
    DENIED = "denied", "Denied"


class AIToolCall(BaseModel):
    """One tool invocation, recorded whether it succeeded or not.

    ``scope_denied`` is the record of an agent trying to reach something outside the
    requesting user's tenant. It should never be true; when it is, it is an
    incident worth looking at rather than a silent failure.
    """

    run = models.ForeignKey(AIAnalysisRun, on_delete=models.CASCADE, related_name="tool_calls")
    sequence = models.PositiveSmallIntegerField(default=1)
    tool_name = models.CharField(max_length=64)
    arguments = models.JSONField(default=dict, blank=True)
    result_summary = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=ToolCallStatus.choices)
    duration_ms = models.PositiveIntegerField(default=0)
    scope_denied = models.BooleanField(default=False)
    error = models.TextField(blank=True, default="")

    class Meta(BaseModel.Meta):
        ordering = ("run", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=["run", "sequence"], name="uniq_ai_tool_call_run_sequence"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tool_name} ({self.status})"


class FindingSeverity(models.TextChoices):
    INFO = "info", "Info"
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class FindingStatus(models.TextChoices):
    NEW = "new", "New"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    DISMISSED = "dismissed", "Dismissed"
    CONVERTED = "converted", "Converted into work"


class AIFinding(BaseModel, ScopedModel):
    """Something an agent thinks the team should look at.

    ``dedupe_hash`` is what stops a re-analysis of the same commit from producing the
    same finding twice: the hash covers the agent, the target and the substance, so an
    unchanged conclusion updates the existing row instead of piling up.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="ai_findings")
    job = models.ForeignKey(
        AIAnalysisJob,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="findings",
    )
    agent_code = models.CharField(max_length=64)
    severity = models.CharField(
        max_length=16, choices=FindingSeverity.choices, default=FindingSeverity.MEDIUM
    )
    category = models.CharField(max_length=64, blank=True, default="")
    title = models.CharField(max_length=500)
    summary = models.TextField(blank=True, default="")
    #: The agent's structured output, kept verbatim so the UI can render it without
    #: re-running the analysis.
    payload = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(default=0.0)
    #: Validated references only: anything that did not resolve to a real row was
    #: dropped before this was written.
    evidence = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=16, choices=FindingStatus.choices, default=FindingStatus.NEW
    )
    dedupe_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta(BaseModel.Meta):
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "dedupe_hash"],
                condition=~models.Q(dedupe_hash=""),
                name="uniq_ai_finding_dedupe",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"], name="idx_aifinding_project_status"),
            models.Index(fields=["project", "severity"], name="idx_aifinding_project_severity"),
        ]

    def __str__(self) -> str:
        return f"[{self.severity}] {self.title}"

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[AIFinding]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class RecommendationStatus(models.TextChoices):
    PENDING = "pending", "Pending confirmation"
    EXECUTED = "executed", "Executed"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Execution failed"
    EXPIRED = "expired", "Expired"


class AIRecommendation(BaseModel, ScopedModel):
    """A proposed state change, waiting for a human.

    This is the mechanism behind "AI proposes, the user disposes". The model cannot
    reach a state-changing tool at all; the most it can do is produce one of these,
    and only a confirmed recommendation is ever handed to an executor.
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="ai_recommendations"
    )
    job = models.ForeignKey(
        AIAnalysisJob,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recommendations",
    )
    finding = models.ForeignKey(
        AIFinding,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recommendations",
    )
    agent_code = models.CharField(max_length=64)
    #: Key into the executor registry. Never a free-form instruction: the set of
    #: things an AI can propose is fixed in code.
    type = models.CharField(max_length=64)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    risk_level = models.CharField(max_length=16, default="medium")
    status = models.CharField(
        max_length=16, choices=RecommendationStatus.choices, default=RecommendationStatus.PENDING
    )
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["project", "status"], name="idx_airec_project_status"),
        ]

    def __str__(self) -> str:
        return f"{self.type} ({self.status})"

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[AIRecommendation]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class ConfirmationDecision(models.TextChoices):
    CONFIRMED = "confirmed", "Confirmed"
    REJECTED = "rejected", "Rejected"


class AIConfirmation(BaseModel):
    """The human decision on a recommendation, and what came of it."""

    recommendation = models.OneToOneField(
        AIRecommendation, on_delete=models.CASCADE, related_name="confirmation"
    )
    requested_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_confirmations",
    )
    decision = models.CharField(max_length=16, choices=ConfirmationDecision.choices)
    #: What the user actually approved, which may differ from the proposal.
    edited_payload = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True, default="")
    executor_code = models.CharField(max_length=64, blank=True, default="")
    result = models.JSONField(default=dict, blank=True)
    executed_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.decision} {self.recommendation_id}"


class ActorType(models.TextChoices):
    USER = "user", "User"
    AI = "ai", "AI"
    SYSTEM = "system", "System"


class AuditLog(BaseModel):
    """Who did what, to which row, before and after.

    Covers confirmed AI actions as well as ordinary user actions, so "the AI created
    12 test cases" is answerable from the same place as "a person deleted this bug".
    """

    org = models.ForeignKey(
        Organization, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    actor_id = models.CharField(max_length=64, blank=True, default="")
    action = models.CharField(max_length=64)
    target_type = models.CharField(max_length=64, blank=True, default="")
    target_id = models.CharField(max_length=64, blank=True, default="")
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    #: Ties the record back to the HTTP request that caused it.
    request_id = models.CharField(max_length=64, blank=True, default="")
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"], name="idx_audit_org_created"),
        ]

    def __str__(self) -> str:
        return f"{self.actor_type}:{self.action}"
