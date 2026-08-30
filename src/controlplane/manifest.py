"""Tool manifest (FR-16, FR-24).

Every tool declares, alongside its schema: a reversibility class, its
preconditions, its compensator, and whether it is an egress point.

The default matters more than the schema. An unclassified tool defaults to
``irreversible``, so forgetting to classify a tool makes the supervisor
*more* cautious rather than silently less.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .types import Reversibility

__all__ = ["Precondition", "ToolSpec", "ToolManifest"]


@dataclass(frozen=True)
class Precondition:
    """A declared, machine-checkable condition on a tool call.

    ``expr`` is a restricted comparison over argument names and world-state
    lookups, e.g. ``amount <= state.policy[policy_id].premium_paid``. Kept
    declarative so preconditions are data, not supervisor code.
    """

    name: str
    expr: str
    message: str = ""


@dataclass
class ToolSpec:
    name: str
    description: str = ""
    schema: dict[str, Any] = field(default_factory=dict)
    reversibility: Reversibility = Reversibility.IRREVERSIBLE
    preconditions: list[Precondition] = field(default_factory=list)
    compensator: str | None = None
    # Sends data outside the trust boundary; subject to egress allowlist.
    egress: bool = False
    # Entity types this tool resolves, e.g. lookup_policy resolves policy_id.
    resolves: list[str] = field(default_factory=list)
    schema_version: str = "v1"

    @property
    def required_args(self) -> list[str]:
        return list(self.schema.get("required", []))

    @property
    def properties(self) -> dict[str, Any]:
        return self.schema.get("properties", {})

    def openai_tool(self) -> dict[str, Any]:
        """Render as an OpenAI-compatible tool definition for the agent."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema or {"type": "object", "properties": {}, "required": []},
            },
        }


class ToolManifest:
    def __init__(self, tools: dict[str, ToolSpec] | None = None) -> None:
        self._tools: dict[str, ToolSpec] = dict(tools or {})
        self.unclassified_seen: set[str] = set()

    def add(self, spec: ToolSpec) -> ToolSpec:
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            # Unknown tool: treat as irreversible rather than assume safety.
            self.unclassified_seen.add(name)
            return ToolSpec(name=name, reversibility=Reversibility.IRREVERSIBLE)
        return spec

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def openai_tools(self) -> list[dict[str, Any]]:
        return [self._tools[n].openai_tool() for n in self.names]

    def reversibility(self, name: str) -> Reversibility:
        return self.get(name).reversibility

    @classmethod
    def from_yaml(cls, path: Path) -> ToolManifest:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        manifest = cls()
        for name, body in (raw.get("tools") or {}).items():
            manifest.add(
                ToolSpec(
                    name=name,
                    description=body.get("description", ""),
                    schema=body.get("schema", {}),
                    reversibility=Reversibility(body.get("reversibility", "irreversible")),
                    preconditions=[Precondition(**p) for p in (body.get("preconditions") or [])],
                    compensator=body.get("compensator"),
                    egress=bool(body.get("egress", False)),
                    resolves=list(body.get("resolves") or []),
                    schema_version=body.get("schema_version", "v1"),
                )
            )
        return manifest
