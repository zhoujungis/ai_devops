"""The AI layer.

Layering:

    tools      the only place *the model* reaches data, always scoped to one tenant
    services   tracing, cost, findings, confirmation, tool execution
    agents     orchestrate: deterministic facts up front, then the tool loop
    schemas    what an agent is allowed to return
    prompts    versioned text, never inline in business code
    providers  the only place that knows a vendor exists
    views      HTTP

An agent may query the database for the facts it hands over up front — they are the
same queries the correlation engine makes, and reading them directly is what keeps the
prompt bounded. What the tool boundary guarantees is narrower and checkable: a *model*
only ever sees rows a tool chose to return, and every tool filters by ``context.project``.
"""
