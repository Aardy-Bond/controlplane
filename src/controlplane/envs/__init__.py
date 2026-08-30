"""Test environments (PRD 7.1). Each is a real tool estate with mutable state
whose success is verified against final world state, not agent self-report."""

from .base import Environment, FaultInjector, FaultSpec, ToolResult
from .env_a_support import SupportEnv
from .env_b_knowledge import KnowledgeEnv
from .env_c_underwriting import UnderwritingEnv

ENVIRONMENTS = {"A": SupportEnv, "B": KnowledgeEnv, "C": UnderwritingEnv}

__all__ = [
    "ENVIRONMENTS",
    "Environment",
    "FaultInjector",
    "FaultSpec",
    "KnowledgeEnv",
    "SupportEnv",
    "ToolResult",
    "UnderwritingEnv",
]
