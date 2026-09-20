"""Code entity serializers."""

from __future__ import annotations

from rest_framework import serializers

from apps.bugs.models import Bug
from apps.codebase.models import Commit, CommitFile, CommitModuleImpact, Module
from apps.releases.models import Release
from apps.requirements.models import Requirement
from apps.testing.models import TestCase


class ModuleRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Module
        fields = ("id", "name", "kind", "path_prefix")


class CommitModuleImpactSerializer(serializers.ModelSerializer):
    module = ModuleRefSerializer(read_only=True)

    class Meta:
        model = CommitModuleImpact
        fields = ("module", "churn_lines", "file_count", "weight", "is_test_change")


class CommitFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommitFile
        fields = (
            "id",
            "path",
            "old_path",
            "change_type",
            "additions",
            "deletions",
            "patch",
            "has_patch",
            "truncated",
            "language",
        )


class CommitSerializer(serializers.ModelSerializer):
    """Commit with the modules it touched, which is what impact analysis reads."""

    short_sha = serializers.CharField(read_only=True)
    module_impacts = CommitModuleImpactSerializer(many=True, read_only=True)

    class Meta:
        model = Commit
        # Annotated so the detail serializer can extend the tuple without mypy
        # inferring a fixed-length type from this one.
        fields: tuple[str, ...] = (
            "id",
            "repository",
            "sha",
            "short_sha",
            "message",
            "author_name",
            "author_email",
            "committed_at",
            "additions",
            "deletions",
            "files_changed",
            "pull_request_external_id",
            "module_impacts",
            "created_at",
        )


class CommitDetailSerializer(CommitSerializer):
    files = CommitFileSerializer(many=True, read_only=True)

    class Meta(CommitSerializer.Meta):
        fields = (*CommitSerializer.Meta.fields, "files")


class ModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Module
        fields = (
            "id",
            "name",
            "kind",
            "path_prefix",
            "language",
            "centrality_score",
            "owner_team",
            "created_at",
        )


# ---------------------------------------------------------------------------
# Correlation payloads
#
# These serialise the dataclasses returned by services.correlation, not models,
# which is why several are plain Serializers: the service deliberately returns
# its own view of the chain rather than ORM rows.
# ---------------------------------------------------------------------------
class CommitRefSerializer(serializers.ModelSerializer):
    short_sha = serializers.CharField(read_only=True)

    class Meta:
        model = Commit
        fields = ("id", "repository", "sha", "short_sha", "message", "committed_at")


class TestCaseRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestCase
        fields = ("id", "key", "title", "priority", "automation", "status")


class BugRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bug
        fields = ("id", "key", "title", "severity", "priority", "status")


class RequirementRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Requirement
        fields = ("id", "external_key", "title", "status", "priority")


class ReleaseRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Release
        fields = ("id", "version", "name", "status", "released_at")


class ExplainedModuleSerializer(serializers.Serializer):
    module = ModuleRefSerializer()
    weight = serializers.FloatField()
    churn_lines = serializers.IntegerField()
    file_count = serializers.IntegerField()
    is_test_change = serializers.BooleanField()


class RegressionCandidateSerializer(serializers.Serializer):
    test_case = TestCaseRefSerializer()
    score = serializers.FloatField()
    #: Why this case was selected. Every candidate must be able to explain itself.
    reasons = serializers.ListField(child=serializers.CharField())


class HistoricalBugSerializer(serializers.Serializer):
    bug = BugRefSerializer()
    shared_modules = serializers.ListField(child=serializers.CharField())
    shared_confidence = serializers.FloatField()


class CommitExplanationSerializer(serializers.Serializer):
    """The correlation chain for one commit, plus what it could not resolve."""

    commit = CommitRefSerializer()
    modules = ExplainedModuleSerializer(many=True)
    regression_candidates = RegressionCandidateSerializer(many=True)
    historical_bugs = HistoricalBugSerializer(many=True)
    requirement = RequirementRefSerializer(allow_null=True)
    requirement_source = serializers.CharField()
    releases = ReleaseRefSerializer(many=True)
    data_gaps = serializers.ListField(child=serializers.CharField())
