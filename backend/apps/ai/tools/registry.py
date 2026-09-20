"""The tool registry.

Two things this enforces:

* **Only read-only tools reach the model.** ``model_specs`` filters them, so a
  state-changing tool is not merely discouraged in a prompt — it is absent from the
  function list the model is given, which means it cannot be called at all.
* **A duplicate tool name is a startup error**, not a silent override that makes one
  of two implementations dead code.
"""

from __future__ import annotations

from typing import Any

from apps.ai.tools.base import Tool, ToolError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ToolError(f"{type(tool).__name__} has no name.")
        if tool.name in self._tools:
            raise ToolError(
                f"Tool {tool.name!r} is already registered by "
                f"{type(self._tools[tool.name]).__name__}."
            )
        self._tools[tool.name] = tool

    def register_all(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(
                f"No tool named {name!r}. Available: {', '.join(sorted(self._tools))}"
            ) from exc

    def names(self) -> list[str]:
        return sorted(self._tools)

    def model_specs(self) -> list[dict[str, Any]]:
        """The function list handed to the model: read-only tools only."""
        return [self._tools[name].spec() for name in self.names() if self._tools[name].read_only]


registry = ToolRegistry()


def register_tool(tool: Tool) -> Tool:
    registry.register(tool)
    return tool


def load_default_tools() -> ToolRegistry:
    """Populate the process-wide registry with the built-in read tools.

    Imported lazily so the registry module has no import-time dependency on the
    domain apps, which keeps the dependency arrow pointing one way.
    """
    from apps.ai.tools.read.bugs import GetBugTool, SearchBugTool
    from apps.ai.tools.read.codebase import (
        GetCommitTool,
        GetDiffTool,
        ListChangedFilesTool,
        ListModulesTool,
        SearchCommitTool,
    )
    from apps.ai.tools.read.correlation import ExplainCommitTool
    from apps.ai.tools.read.requirements import GetRequirementTool, SearchRequirementTool
    from apps.ai.tools.read.testing import (
        ListFailingTestsTool,
        ListTestRunsTool,
        SearchTestCaseTool,
    )

    if not registry.names():
        registry.register_all(
            [
                ExplainCommitTool(),
                GetCommitTool(),
                ListChangedFilesTool(),
                GetDiffTool(),
                SearchCommitTool(),
                ListModulesTool(),
                SearchTestCaseTool(),
                ListTestRunsTool(),
                ListFailingTestsTool(),
                SearchBugTool(),
                GetBugTool(),
                SearchRequirementTool(),
                GetRequirementTool(),
            ]
        )
    return registry
