"""Running a tool on an agent's behalf, with every attempt recorded.

The recording matters as much as the result. A tool call that was refused because it
reached outside the requesting project is an incident, and it has to leave a trace —
otherwise the only evidence that an agent tried would be a gap in the transcript.
"""

from __future__ import annotations

import time
from typing import Any

from apps.ai.models import AIAnalysisRun, ToolCallStatus
from apps.ai.services.tracing import record_tool_call
from apps.ai.tools.base import (
    ToolContext,
    ToolError,
    ToolResult,
    ToolScopeError,
)
from apps.ai.tools.registry import registry


def run_tool(
    *,
    run: AIAnalysisRun,
    tool_name: str,
    arguments: dict[str, Any],
    context: ToolContext,
) -> ToolResult:
    """Execute a read tool, recording the attempt whether it succeeded or not."""
    tool = registry.get(tool_name)
    started = time.perf_counter()

    try:
        result = tool.execute(context, arguments)
    except ToolScopeError as exc:
        _record(
            run=run,
            tool_name=tool_name,
            arguments=arguments,
            status=ToolCallStatus.DENIED,
            started=started,
            scope_denied=True,
            error=str(exc),
        )
        raise
    except ToolError as exc:
        _record(
            run=run,
            tool_name=tool_name,
            arguments=arguments,
            status=ToolCallStatus.ERROR,
            started=started,
            error=str(exc),
        )
        raise

    _record(
        run=run,
        tool_name=tool_name,
        arguments=arguments,
        status=ToolCallStatus.OK,
        started=started,
        result_summary=result.summary(),
    )
    return result


def _record(
    *,
    run: AIAnalysisRun,
    tool_name: str,
    arguments: dict[str, Any],
    status: ToolCallStatus,
    started: float,
    result_summary: Any = None,
    scope_denied: bool = False,
    error: str = "",
) -> None:
    record_tool_call(
        run=run,
        tool_name=tool_name,
        arguments=arguments,
        result_summary=result_summary,
        status=status,
        duration_ms=int((time.perf_counter() - started) * 1000),
        scope_denied=scope_denied,
        error=error,
    )
