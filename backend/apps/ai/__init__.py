"""The AI layer.

Layering, enforced by convention and by the architectural tests:

    agents     orchestrate; they never import models
    tools      the only way an agent reaches data, always scoped to a tenant
    schemas    what an agent is allowed to return
    prompts    versioned text, never inline in business code
    providers  the only place that knows a vendor exists
"""
