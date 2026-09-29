"""The Root Cause Analysis agent.

Bug investigation asks "what might have caused this, and how would I check"; RCA asks
"which cause is best supported, and in what order did things happen". The facts that
answer both are the same — the defect, the modules it lives in, what changed before it
was first seen, similar past defects — so this reuses that gathering and changes only
the prompt and the output schema. Two copies of the fact-gathering would be two things
to keep in step.

The output has no single "root cause" field on purpose: candidates carry their own
confidence and verification steps, because one asserted cause is a hypothesis dressed
as a conclusion.
"""

from __future__ import annotations

from apps.ai.agents.base import register_agent
from apps.ai.agents.bug_investigation import BugInvestigationAgent
from apps.ai.schemas.bug_investigation import RcaOutput


@register_agent
class RcaAgent(BugInvestigationAgent):
    code = "rca"
    prompt_id = "rca"
    capability = "chat"
    description = "Ranks root-cause candidates for a defect from its change timeline."
    schema = RcaOutput
    finding_category = "rca"
