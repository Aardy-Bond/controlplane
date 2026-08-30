"""ControlPlane — runtime supervisor for tool-augmented multi-step agents.

Re-exports are resolved lazily (PEP 562). Importing them eagerly meant that
touching *any* part of the package pulled in the OpenRouter client, and with it
an HTTP library and a `load_dotenv()` call at import time. The read-only
dashboard needs none of that, and a deployment that never calls a model should
not have to install one — or run an import-time side effect that reads a file
it will never use.

`from controlplane import Supervisor` still works exactly as before; the
submodule is imported on first attribute access instead of at package load.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

_EXPORTS: dict[str, str] = {
    "REGISTRY": "invariants",
    "EvalContext": "invariants",
    "Ledger": "ledger",
    "LedgerStore": "ledger",
    "LocalizationEngine": "localize",
    "ToolManifest": "manifest",
    "ToolSpec": "manifest",
    "PolicyRegistry": "policy",
    "RecoveryEngine": "recover",
    "Decision": "supervisor",
    "RunSupervisor": "supervisor",
    "Supervisor": "supervisor",
    "Checkpoint": "types",
    "Incident": "types",
    "Reversibility": "types",
    "SourceClass": "types",
    "Violation": "types",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value  # resolve once, then it is a plain global
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


if TYPE_CHECKING:  # keeps editors and type checkers seeing the real symbols
    from .invariants import REGISTRY, EvalContext
    from .ledger import Ledger, LedgerStore
    from .localize import LocalizationEngine
    from .manifest import ToolManifest, ToolSpec
    from .policy import PolicyRegistry
    from .recover import RecoveryEngine
    from .supervisor import Decision, RunSupervisor, Supervisor
    from .types import Checkpoint, Incident, Reversibility, SourceClass, Violation

__all__ = [
    "REGISTRY",
    "Checkpoint",
    "Decision",
    "EvalContext",
    "Incident",
    "Ledger",
    "LedgerStore",
    "LocalizationEngine",
    "PolicyRegistry",
    "RecoveryEngine",
    "Reversibility",
    "RunSupervisor",
    "SourceClass",
    "Supervisor",
    "ToolManifest",
    "ToolSpec",
    "Violation",
]
