"""Agents. Registered by code; looked up when a job runs.

Importing this package registers every built-in agent, which is why the imports below
look unused.
"""

from apps.ai.agents.analysis import AnalysisAgent
from apps.ai.agents.base import (
    AGENTS,
    BaseAgent,
    agent_for,
    available_agents,
    register_agent,
)
from apps.ai.agents.bug_investigation import BugInvestigationAgent
from apps.ai.agents.code_impact import CodeImpactAgent
from apps.ai.agents.requirement import RequirementAgent
from apps.ai.agents.test_generation import TestGenerationAgent

__all__ = [
    "AGENTS",
    "AnalysisAgent",
    "BaseAgent",
    "BugInvestigationAgent",
    "CodeImpactAgent",
    "RequirementAgent",
    "TestGenerationAgent",
    "agent_for",
    "available_agents",
    "register_agent",
]
