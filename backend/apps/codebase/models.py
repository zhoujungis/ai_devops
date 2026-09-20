"""Code entities: branches, commits, changed files and resolved modules.

``CommitModuleImpact`` is the materialised "commit touched this module" edge. It is
what lets the correlation chain answer "what did this commit affect" with a single
join instead of re-deriving modules from paths on every query.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from apps.accounts.models import Project
from apps.core.models import BaseModel
from apps.core.scoping import ScopedModel
from apps.integrations.models import Repository


class ChangeType(models.TextChoices):
    ADD = "add", "Added"
    MODIFY = "modify", "Modified"
    DELETE = "delete", "Deleted"
    RENAME = "rename", "Renamed"


class ModuleKind(models.TextChoices):
    PACKAGE = "package", "Package"
    MODULE = "module", "Module"
    SERVICE = "service", "Service"
    DIR = "dir", "Directory"


class Branch(BaseModel):
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name="branches")
    name = models.CharField(max_length=255)
    # The sha is stored rather than a foreign key because a branch routinely points
    # at a commit outside the synced window, and losing the pointer would be worse
    # than losing the join.
    head_sha = models.CharField(max_length=64, blank=True, default="")
    is_default = models.BooleanField(default=False)
    is_protected = models.BooleanField(default=False)
    last_commit_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["repository", "name"], name="uniq_branch_repo_name"),
        ]

    def __str__(self) -> str:
        return self.name

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[Branch]:
        return cls.objects.filter(repository__project__in=Project.objects.accessible_to(user))


class Commit(BaseModel, ScopedModel):
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name="commits")
    sha = models.CharField(max_length=64)
    parent_shas = models.JSONField(default=list, blank=True)
    author_name = models.CharField(max_length=255, blank=True, default="")
    author_email = models.CharField(max_length=320, blank=True, default="")
    committer_email = models.CharField(max_length=320, blank=True, default="")
    message = models.TextField(blank=True, default="")
    committed_at = models.DateTimeField(db_index=True)
    additions = models.PositiveIntegerField(default=0)
    deletions = models.PositiveIntegerField(default=0)
    files_changed = models.PositiveIntegerField(default=0)
    # Set when a PR reference is later imported from the git host.
    pull_request_external_id = models.CharField(max_length=32, blank=True, default="")
    # Inferred from the commit message by the requirement linker; the correlation
    # chain reads it as the fast path before falling back to module associations.
    requirement = models.ForeignKey(
        "requirements.Requirement",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="commits",
    )

    class Meta(BaseModel.Meta):
        ordering = ("-committed_at",)
        constraints = [
            models.UniqueConstraint(fields=["repository", "sha"], name="uniq_commit_repo_sha"),
        ]
        indexes = [
            models.Index(fields=["repository", "committed_at"], name="idx_commit_repo_when"),
        ]

    def __str__(self) -> str:
        return self.sha[:12]

    @property
    def short_sha(self) -> str:
        return self.sha[:12]

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[Commit]:
        return cls.objects.filter(repository__project__in=Project.objects.accessible_to(user))


class CommitFile(BaseModel):
    commit = models.ForeignKey(Commit, on_delete=models.CASCADE, related_name="files")
    path = models.CharField(max_length=1024)
    old_path = models.CharField(max_length=1024, blank=True, default="")
    change_type = models.CharField(max_length=16, choices=ChangeType.choices)
    additions = models.PositiveIntegerField(default=0)
    deletions = models.PositiveIntegerField(default=0)
    patch = models.TextField(blank=True, default="")
    # False when the provider returned no textual diff (binary, or over its size
    # limit); `truncated` means we stored only part of one.
    has_patch = models.BooleanField(default=False)
    truncated = models.BooleanField(default=False)
    language = models.CharField(max_length=32, blank=True, default="")

    class Meta(BaseModel.Meta):
        ordering = ("path",)
        indexes = [models.Index(fields=["commit", "path"], name="idx_commitfile_commit_path")]

    def __str__(self) -> str:
        return self.path

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[CommitFile]:
        return cls.objects.filter(
            commit__repository__project__in=Project.objects.accessible_to(user)
        )


class Module(BaseModel, ScopedModel):
    """A logical unit of the codebase that impact analysis reasons about."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="modules")
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=16, choices=ModuleKind.choices, default=ModuleKind.MODULE)
    path_prefix = models.CharField(max_length=512)
    language = models.CharField(max_length=32, blank=True, default="")
    centrality_score = models.FloatField(default=0.0)
    owner_team = models.CharField(max_length=100, blank=True, default="")

    class Meta(BaseModel.Meta):
        ordering = ("path_prefix",)
        constraints = [
            # Identity is the path prefix, not the label: two modules may legitimately
            # be named alike, but no two may claim the same paths.
            models.UniqueConstraint(
                fields=["project", "path_prefix"], name="uniq_module_project_path_prefix"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[Module]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class CommitModuleImpact(BaseModel):
    """Materialised edge: this commit touched this module, by this much."""

    commit = models.ForeignKey(Commit, on_delete=models.CASCADE, related_name="module_impacts")
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="commit_impacts")
    churn_lines = models.PositiveIntegerField(default=0)
    file_count = models.PositiveIntegerField(default=0)
    # Share of the commit's churn that landed in this module (0..1).
    weight = models.FloatField(default=0.0)
    is_test_change = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["commit", "module"], name="uniq_commit_module_impact"),
        ]

    def __str__(self) -> str:
        return f"{self.commit} -> {self.module}"

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[CommitModuleImpact]:
        return cls.objects.filter(
            commit__repository__project__in=Project.objects.accessible_to(user)
        )
