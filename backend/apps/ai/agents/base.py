"""The agent contract.

An agent orchestrates: it works out which deterministic facts to hand over up front,
runs the tool loop that lets the model ask for more, and returns a validated payload.

It *may* read the database directly for those up-front facts — they are the same
queries the correlation engine makes — and the read tools exist so the model can follow
up on its own, not to keep the agent model-free. What every agent shares is
:meth:`BaseAgent.answer`: the tool list the model is shown, the iteration bound, and the
trace are decided in one place, so no agent can quietly diverge.
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from django.conf import settings
from pydantic import BaseModel, ValidationError

from apps.ai.providers.base import (
    AIProvider,
    ChatMessage,
    ChatResult,
    ToolCall,
    schema_instructions,
)
from apps.ai.services.tooling import run_tool
from apps.ai.services.tracing import record_run
from apps.ai.tools.base import ToolContext, ToolError
from apps.ai.tools.registry import registry

if TYPE_CHECKING:
    from apps.ai.models import AIAnalysisJob

TAnswer = TypeVar("TAnswer", bound=BaseModel)


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

    # ------------------------------------------------------------------
    def answer(
        self,
        job: AIAnalysisJob,
        *,
        context: ToolContext,
        provider: AIProvider,
        model: str,
        messages: list[ChatMessage],
        schema: type[TAnswer],
        prompt_id: str = "",
        prompt_version: int = 0,
    ) -> tuple[TAnswer, ChatResult]:
        """Answer in ``schema``, letting the model consult the read tools on the way.

        Two guarantees live here, so no agent can diverge from them:

        * the tool list is ``registry.model_specs()`` — read-only tools only — so a
          state-changing action is not merely discouraged in a prompt, it is absent
          from the function list the model is given;
        * every model interaction is recorded as an ``AIAnalysisRun`` carrying its tool
          calls as ``AIToolCall`` rows, which is what makes the trace answer "how did it
          reach this?" rather than only "what did it say?".

        The schema instruction is injected for the *first* call too, so a model that
        needs no tools answers in one request rather than two. At most
        ``AI_ANALYSIS_MAX_TOOL_ITERATIONS`` tool round trips are allowed, after which the
        answer is requested regardless — the bound is a budget guard, not a correctness
        requirement, so a model that keeps asking for tools is cut off rather than
        failing the analysis.
        """
        specs = registry.model_specs()
        conversation = list(messages)

        for _ in range(max(0, settings.AI_ANALYSIS_MAX_TOOL_ITERATIONS)):
            result = provider.chat(
                _with_schema_instruction(conversation, schema), model=model, tools=specs or None
            )
            run = self._record_interaction(
                job=job,
                provider=provider,
                model=model,
                conversation=conversation,
                result=result,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
            )

            if not result.tool_calls:
                try:
                    return schema.model_validate_json(result.content), result
                except ValidationError:
                    # Almost-JSON is normal. Fall through to the repairing path, which
                    # makes one more attempt against the same schema.
                    break

            conversation.append(
                ChatMessage(
                    role="assistant", content=result.content, tool_calls=result.tool_calls
                )
            )
            for call in result.tool_calls:
                conversation.append(
                    ChatMessage(
                        role="tool",
                        content=_tool_output(run, call, context),
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )

        output, result = provider.structured_output(conversation, schema=schema, model=model)
        self._record_interaction(
            job=job,
            provider=provider,
            model=model,
            conversation=conversation,
            result=result,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
        )
        return output, result

    def _record_interaction(
        self,
        *,
        job: AIAnalysisJob,
        provider: AIProvider,
        model: str,
        conversation: list[ChatMessage],
        result: ChatResult,
        prompt_id: str,
        prompt_version: int,
    ) -> Any:
        return record_run(
            job=job,
            provider_type=provider.provider_type,
            model=result.model or model,
            capability=self.capability,
            input_text="\n\n".join(message.content for message in conversation),
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            result=result,
        )


def _with_schema_instruction(messages: list[ChatMessage], schema: type[BaseModel]) -> list[ChatMessage]:
    """A copy of the conversation carrying the output-schema instruction."""
    instructions = schema_instructions(schema)
    prepared = list(messages)
    if prepared and prepared[0].role == "system":
        prepared[0] = ChatMessage(role="system", content=f"{prepared[0].content}\n\n{instructions}")
    else:
        prepared.insert(0, ChatMessage(role="system", content=instructions))
    return prepared


def _tool_output(run: Any, call: ToolCall, context: ToolContext) -> str:
    """Run one requested tool and render its result for the model.

    A refused or failed tool is information, not a crash: the attempt is already
    recorded, and the model is free to try something else.
    """
    try:
        result = run_tool(
            run=run, tool_name=call.name, arguments=call.arguments, context=context
        )
        payload: Any = result.data
    except ToolError as exc:
        payload = {"error": str(exc)}
    return json.dumps(payload, ensure_ascii=False, default=str)


AGENTS: dict[str, type[BaseAgent]] = {}


def matches_uuid(value: str) -> bool:
    """Whether ``value`` could be a primary key.

    An agent's target is often a human key (``BUG-1``) or a sha rather than a uuid.
    Filtering a UUID column by one of those raises ``ValidationError`` instead of
    returning nothing, so a pk lookup has to be guarded — the fallback to the human key
    is what the callers say they support.
    """
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


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
