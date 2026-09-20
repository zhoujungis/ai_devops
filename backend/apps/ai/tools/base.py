"""The tool contract.

Every tool receives a :class:`ToolContext` and is required to filter by
``context.project``. That single rule is what makes "the AI cannot see another
tenant's data" a property of the code rather than a promise in a prompt: the model
never gets a queryset, only the rows a tool decides to hand back.

Tools are also the only place an agent touches data at all. The agent layer is
checked (by test) never to import models.

``Tool`` is generic over its argument model so ``run`` receives a validated, fully
typed object: one place validates, and no implementation unpacks ``**kwargs``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from apps.accounts.models import Project, User

TArgs = TypeVar("TArgs", bound=BaseModel)


class ToolError(RuntimeError):
    """A tool could not complete, for a reason the model can be told about."""


class ToolScopeError(ToolError):
    """The requested row is outside the requesting user's project.

    Deliberately distinct from "not found": reaching for another tenant is an
    incident, and it is recorded as ``scope_denied`` rather than as a miss.
    """


class ToolArgumentsError(ToolError):
    """The model called the tool with arguments that do not fit its schema."""


@dataclass(frozen=True)
class ToolContext:
    """Who is asking, and on behalf of which project.

    Tools take no ``project_id`` argument, so a model cannot widen its own scope by
    asking nicely. ``user`` is optional because a job can outlive the account that
    requested it; the project is the boundary that matters for data access, and it
    was resolved through that user's scoped queryset when the job was created.
    """

    project: Project
    user: User | None = None
    job_id: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class Citation:
    """A reference the answer can point at, resolvable to a real row."""

    kind: str
    ref_id: str
    label: str = ""


@dataclass(frozen=True)
class ToolResult:
    data: Any
    citations: tuple[Citation, ...] = field(default_factory=tuple)

    def summary(self) -> Any:
        """A bounded form for the trace, so one big result cannot bloat the row."""
        if isinstance(self.data, list):
            return {"count": len(self.data), "sample": self.data[:3]}
        return self.data


class Tool(Generic[TArgs], ABC):
    """One capability an agent may invoke."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    args_model: type[TArgs]
    #: Read-only tools are the only ones exposed to the model. A write tool exists
    #: solely so a user-confirmed action can call it after the fact.
    read_only: ClassVar[bool] = True

    def spec(self) -> dict[str, Any]:
        """The function-calling description handed to the model."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        """Validate raw model arguments and run the tool."""
        try:
            parsed = self.args_model.model_validate(arguments)
        except Exception as exc:  # pydantic raises ValidationError
            raise ToolArgumentsError(f"{self.name}: {exc}") from exc
        return self.run(context, parsed)

    @abstractmethod
    def run(self, context: ToolContext, args: TArgs) -> ToolResult:
        """Do the work. Implementations must filter by ``context.project``."""
