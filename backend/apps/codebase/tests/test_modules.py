"""Module resolution: the mapping every impact claim ultimately rests on."""

from __future__ import annotations

import pytest

from apps.codebase.models import ModuleKind
from apps.codebase.modules import ModuleResolver, is_test_path, language_for_path


@pytest.mark.parametrize(
    ("path", "expected_prefix", "expected_name", "expected_reason"),
    [
        # File-tree languages: the first `depth` directories below the source root.
        (
            "src/features/checkout/Cart.tsx",
            "src/features/checkout",
            "features/checkout",
            "source_root",
        ),
        (
            "src/features/checkout/components/Cart.tsx",
            "src/features/checkout",
            "features/checkout",
            "source_root",
        ),
        (
            "frontend/src/pages/Login.vue",
            "frontend/src/pages",
            "src/pages",
            "source_root",
        ),
        # Package-rooted languages: the whole package is the identity, otherwise
        # the leaf package — the thing that actually matters — would be lost.
        (
            "src/main/java/com/acme/payment/PaymentService.java",
            "src/main/java/com/acme/payment",
            "com.acme.payment",
            "source_root",
        ),
        (
            "backend/apps/testing/models.py",
            "backend/apps/testing",
            "apps.testing",
            "source_root",
        ),
        # A file directly in a source root makes that root the module.
        ("src/index.ts", "src", "src", "source_root"),
        # No convention to lean on: the top-level directory still beats one bucket.
        ("docs/guide.md", "docs", "docs", "fallback"),
        ("README.md", ".", "(repository root)", "fallback"),
    ],
)
def test_paths_resolve_to_the_expected_module(
    path: str, expected_prefix: str, expected_name: str, expected_reason: str
) -> None:
    match = ModuleResolver().resolve(path)

    assert match.path_prefix == expected_prefix
    assert match.name == expected_name
    assert match.reason == expected_reason
    assert match.kind in {ModuleKind.PACKAGE, ModuleKind.DIR}


def test_an_override_beats_every_heuristic() -> None:
    resolver = ModuleResolver(overrides={"backend/apps/testing": "Testing"})

    match = resolver.resolve("backend/apps/testing/tests/test_models.py")

    assert match.name == "Testing"
    assert match.path_prefix == "backend/apps/testing"
    assert match.reason == "override"


def test_the_longest_matching_override_wins() -> None:
    resolver = ModuleResolver(overrides={"backend/apps": "Apps", "backend/apps/testing": "Testing"})

    assert resolver.resolve("backend/apps/testing/models.py").name == "Testing"
    assert resolver.resolve("backend/apps/accounts/models.py").name == "Apps"


def test_depth_controls_the_granularity_for_file_tree_languages() -> None:
    path = "src/features/checkout/components/Cart.tsx"

    assert ModuleResolver(depth=1).resolve(path).path_prefix == "src/features"
    assert ModuleResolver(depth=3).resolve(path).path_prefix == "src/features/checkout/components"


def test_the_language_drives_the_label_style() -> None:
    resolver = ModuleResolver()

    assert resolver.resolve("src/main/java/com/acme/payment/A.java").language == "java"
    assert resolver.resolve("src/app/main.py").language == "python"
    assert resolver.resolve("Makefile").language == ""


def test_an_empty_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty path"):
        ModuleResolver().resolve("   ")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("backend/apps/testing/tests/test_models.py", True),
        ("backend/apps/accounts/tests/factories.py", True),
        ("src/features/checkout/Cart.test.tsx", True),
        ("src/features/checkout/Cart.spec.ts", True),
        ("src/main/java/com/acme/payment/PaymentServiceTest.java", True),
        ("src/main/java/com/acme/payment/PaymentIT.java", True),
        ("tests/integration/test_github_live.py", True),
        ("src/features/checkout/Cart.tsx", False),
        ("backend/apps/accounts/models.py", False),
        ("src/main/java/com/acme/payment/PaymentService.java", False),
    ],
)
def test_test_paths_are_recognised(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected


def test_language_detection_ignores_case_and_missing_extensions() -> None:
    assert language_for_path("src/App.TSX") == "typescript"
    assert language_for_path("Makefile") == ""
    assert language_for_path("a/b/c") == ""
