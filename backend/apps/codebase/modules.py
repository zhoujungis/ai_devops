"""Deterministic path -> module resolution.

Impact analysis is only as trustworthy as this mapping, so it is deliberately
rule-based and explainable rather than clever: every match carries the *reason* it
won, and the Code Impact Agent cites that reason instead of asserting an impact
out of thin air.

Resolution order, first match wins:

1. **override** — an explicit path prefix configured on the repository. Beats
   every heuristic, because a human said so.
2. **source root** — skip conventional source roots (``src``, ``lib``, ``app`` …)
   and derive the module below it. The retained depth depends on the language,
   because the two conventions genuinely differ:

   * *package-rooted* languages (Java, Kotlin, Python, Go, C#) — the containing
     package **is** the module identity, so the whole package path is kept:
     ``src/main/java/com/acme/payment/PaymentService.java`` -> ``com.acme.payment``.
     Truncating that to two segments would collapse the module into ``com.acme``
     and lose exactly the thing impact analysis needs.
   * *file-tree* languages (JS/TS/Vue/…) — the first ``depth`` directories below
     the source root, which is where feature folders live:
     ``src/features/checkout/Cart.tsx`` -> ``features/checkout``.

3. **fallback** — the top-level directory, so a repository with no conventions
   still produces stable, useful modules rather than one giant bucket.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from apps.codebase.models import ModuleKind

#: Longest first, so ``src/main/java`` wins over ``src``.
SOURCE_ROOTS: Final[tuple[str, ...]] = (
    "src/main/java",
    "src/main/kotlin",
    "src/main/scala",
    "src/main/resources",
    "src/test/java",
    "src/test/kotlin",
    "src/main",
    "src/test",
    "src",
    "lib",
    "app",
    "pkg",
    "internal",
    "packages",
    "services",
    "crates",
    "backend",
    "frontend",
)

TEST_DIRECTORY_NAMES: Final[frozenset[str]] = frozenset(
    {"test", "tests", "testing", "spec", "specs", "__tests__", "testdata", "e2e"}
)

#: Languages whose directory structure mirrors a package namespace.
_PACKAGE_ROOTED_LANGUAGES: Final[frozenset[str]] = frozenset(
    {"java", "kotlin", "scala", "python", "go", "csharp", "php"}
)

#: Languages where the module label reads better as a dotted package.
_DOTTED_LANGUAGES: Final[frozenset[str]] = frozenset(
    {"java", "kotlin", "scala", "python", "csharp"}
)

_LANGUAGE_BY_EXTENSION: Final[dict[str, str]] = {
    ".py": "python",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".vue": "vue",
    ".svelte": "svelte",
    ".sql": "sql",
    ".sh": "shell",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
}

_ROOT_PREFIX: Final[str] = "."
_ROOT_NAME: Final[str] = "(repository root)"


@dataclass(frozen=True)
class ModuleMatch:
    """The module a path belongs to, and why."""

    name: str
    kind: str
    path_prefix: str
    language: str
    reason: str


def language_for_path(path: str) -> str:
    """Best-effort language from the file extension; ``""`` when unknown."""
    filename = path.rpartition("/")[2]
    stem, dot, extension = filename.rpartition(".")
    # `rpartition` returns the whole filename as "extension" when there is no dot,
    # which would make a file called `c` look like C source.
    if not dot or not stem:
        return ""
    return _LANGUAGE_BY_EXTENSION.get(f".{extension.lower()}", "")


def is_test_path(path: str) -> bool:
    """Whether a path is test code, so impact can separate it from production."""
    parts = [part for part in path.split("/") if part]
    if not parts:
        return False

    filename = parts[-1]
    if any(segment.lower() in TEST_DIRECTORY_NAMES for segment in parts[:-1]):
        return True

    lowered = filename.lower()
    if lowered.startswith("test_") or lowered.startswith("test."):
        return True
    if ".test." in lowered or ".spec." in lowered:
        return True

    stem = lowered.rsplit(".", 1)[0] if "." in lowered else lowered
    if stem.endswith("_test") or stem.endswith("_spec"):
        return True

    return filename.endswith(("Test.java", "Tests.java", "IT.java"))


class ModuleResolver:
    """Maps repository paths onto :class:`~apps.codebase.models.Module` prefixes."""

    def __init__(self, *, depth: int = 2, overrides: Mapping[str, str] | None = None) -> None:
        self._depth = max(1, depth)
        self._overrides = {
            str(key).strip("/"): str(value) for key, value in (overrides or {}).items()
        }

    def resolve(self, path: str) -> ModuleMatch:
        normalized = path.strip().strip("/")
        if not normalized:
            raise ValueError("Cannot resolve an empty path.")

        language = language_for_path(normalized)

        override = self._match_override(normalized)
        if override is not None:
            name, prefix = override
            return ModuleMatch(
                name=name,
                kind=ModuleKind.MODULE,
                path_prefix=prefix,
                language=language,
                reason="override",
            )

        below_root = self._match_below_source_root(normalized, language)
        if below_root is not None:
            return below_root

        parts = normalized.split("/")
        if len(parts) == 1:
            return ModuleMatch(
                name=_ROOT_NAME,
                kind=ModuleKind.DIR,
                path_prefix=_ROOT_PREFIX,
                language=language,
                reason="fallback",
            )
        return ModuleMatch(
            name=parts[0],
            kind=ModuleKind.DIR,
            path_prefix=parts[0],
            language=language,
            reason="fallback",
        )

    # ------------------------------------------------------------------
    def _match_override(self, path: str) -> tuple[str, str] | None:
        """Longest matching override prefix, so nested overrides win."""
        best: tuple[str, str] | None = None
        for prefix, name in self._overrides.items():
            if not prefix:
                continue
            if (path == prefix or path.startswith(f"{prefix}/")) and (
                best is None or len(prefix) > len(best[1])
            ):
                best = (name, prefix)
        return best

    def _match_below_source_root(self, path: str, language: str) -> ModuleMatch | None:
        for root in SOURCE_ROOTS:
            if not path.startswith(f"{root}/"):
                continue
            remainder = path[len(root) + 1 :]
            directories = remainder.split("/")[:-1]
            if not directories:
                # The file sits directly in the source root, so that root is the module.
                return ModuleMatch(
                    name=root,
                    kind=ModuleKind.PACKAGE,
                    path_prefix=root,
                    language=language,
                    reason="source_root",
                )

            segments = self._segments_for(directories, language)
            return ModuleMatch(
                name=self._label(segments, language),
                kind=ModuleKind.PACKAGE,
                path_prefix=f"{root}/{'/'.join(segments)}",
                language=language,
                reason="source_root",
            )
        return None

    def _segments_for(self, directories: list[str], language: str) -> list[str]:
        if language in _PACKAGE_ROOTED_LANGUAGES:
            return directories
        return directories[: self._depth]

    @staticmethod
    def _label(segments: list[str], language: str) -> str:
        return ".".join(segments) if language in _DOTTED_LANGUAGES else "/".join(segments)
