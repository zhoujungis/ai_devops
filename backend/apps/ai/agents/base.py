"""The agent contract.

An agent orchestrates: it decides what to ask the model, runs the tool loop, and
returns a payload. It does **not** query the database — that is what tools are for,
and the boundary is enforced by an architectural test rather than by good intentions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from apps.ai.providers.base import AIProvider
from apps.ai.tools.base import ToolContext

if TYPE_CHECKING:
    from apps.ai.models import AIAnalysisJob


class AgentError(RuntimeError):
    """An agent could not complete."""


class UnknownAgentError(AgentError):
    """No agent is registered under the requested code."""


class BaseAgent(ABC):
    """One specialised unit of analysis."""

    #: Stable identifier recorded on every job, so history stays readable.
    code: ClassVar[str] = ""
    #: Which prompt (and therefore which output schema) this agent uses.
    prompt_id: ClassVar[str] = ""
    #: Which model capability it needs.
    capability: ClassVar[str] = "chat"
    #: Shown to users choosing what to run.
    description: ClassVar[str] = ""

    @abstractmethod
    def run(
        self,
        job: AIAnalysisJob,
        *,
        context: ToolContext,
        provider: AIProvider,
        model: str,
    ) -> dict[str, Any]:
        """Do the analysis and return the payload to store on the job."""


AGENTS: dict[str, type[BaseAgent]] = {}


def register_agent(agent_class: type[BaseAgent]) -> type[BaseAgent]:
    if not agent_class.code:
        raise AgentError(f"{agent_class.__name__} has no code.")
    if agent_class.code in AGENTS:
        raise AgentError(
            f"Agent code {agent_class.code!r} is already registered by "
            f"{AGENTS[agent_class.code].__name__}."
        )
    AGENTS[agent_class.code] = agent_class
    return agent_class


def agent_for(code: str) -> BaseAgent:
    try:
        return AGENTS[code]()
    except KeyError as exc:
        raise UnknownAgentError(
            f"No agent registered as {code!r}. Available: "
            f"{', '.join(sorted(AGENTS)) or '(none)'}"
        ) from exc


def available_agents() -> dict[str, str]:
    return {code: agent_class.description for code, agent_class in sorted(AGENTS.items())}
