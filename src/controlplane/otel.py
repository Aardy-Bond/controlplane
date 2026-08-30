"""OpenTelemetry GenAI span emission (FR-2).

Spans are emitted in the OTel GenAI semantic-convention shape (``gen_ai.*``,
agent spans, tool-execution spans) and, just as importantly, *consumed* in the
same shape. That symmetry is the interoperability claim: an agent already
instrumented by a third party produces the telemetry this supervisor needs,
without adopting our client wrapper.

This is an in-process recorder rather than a full SDK export. It writes the same
attribute names an exporter would, so wiring a real exporter is a change of sink
and not a change of schema.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Span", "SpanRecorder", "spans_from_otel"]


@dataclass
class Span:
    span_id: str
    trace_id: str
    name: str
    kind: str
    start_ns: int
    end_ns: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "UNSET"

    @property
    def duration_ms(self) -> float:
        if self.end_ns is None:
            return 0.0
        return (self.end_ns - self.start_ns) / 1e6

    def as_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "name": self.name,
            "kind": self.kind,
            "duration_ms": round(self.duration_ms, 3),
            "status": self.status,
            "attributes": self.attributes,
        }


class SpanRecorder:
    def __init__(self, run_id: str) -> None:
        self.trace_id = uuid.uuid4().hex
        self.run_id = run_id
        self.spans: list[Span] = []
        self._open: dict[int, Span] = {}

    def _new(self, name: str, kind: str, attributes: dict[str, Any]) -> Span:
        span = Span(
            span_id=uuid.uuid4().hex[:16],
            trace_id=self.trace_id,
            name=name,
            kind=kind,
            start_ns=time.perf_counter_ns(),
            attributes={"gen_ai.conversation.id": self.run_id, **attributes},
        )
        self.spans.append(span)
        return span

    def start_tool_span(self, tool: str, step: int) -> str:
        span = self._new(
            f"execute_tool {tool}",
            "INTERNAL",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": tool,
                "controlplane.step": step,
            },
        )
        self._open[step] = span
        return span.span_id

    def end_tool_span(self, step: int, source_class: str) -> None:
        span = self._open.pop(step, None)
        if span is None:
            return
        span.end_ns = time.perf_counter_ns()
        span.attributes["controlplane.source_class"] = source_class
        span.status = "ERROR" if source_class in {"error_tagged", "denied"} else "OK"

    def record_model_call(
        self, model: str, prompt_tokens: int, completion_tokens: int, latency_ms: float
    ) -> None:
        span = self._new(
            f"chat {model}",
            "CLIENT",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": model,
                "gen_ai.usage.input_tokens": prompt_tokens,
                "gen_ai.usage.output_tokens": completion_tokens,
            },
        )
        span.end_ns = span.start_ns + int(latency_ms * 1e6)
        span.status = "OK"

    def record_check(self, invariant_id: str, ms: float, path: str, holds: bool) -> None:
        span = self._new(
            f"assert {invariant_id}",
            "INTERNAL",
            {
                "controlplane.invariant": invariant_id,
                "controlplane.path": path,
                "controlplane.holds": holds,
            },
        )
        span.end_ns = span.start_ns + int(ms * 1e6)
        span.status = "OK" if holds else "ERROR"

    def export(self) -> list[dict[str, Any]]:
        return [s.as_dict() for s in self.spans]


def spans_from_otel(raw_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise third-party OTel GenAI spans into the fields the ledger needs.

    Accepting the standard schema as *input* is what lets the supervisor sit
    behind an agent instrumented by someone else (FR-2).
    """
    out = []
    for s in raw_spans:
        attrs = s.get("attributes", {})
        op = attrs.get("gen_ai.operation.name", "")
        if op == "execute_tool":
            out.append(
                {
                    "kind": "tool",
                    "tool": attrs.get("gen_ai.tool.name", ""),
                    "step": attrs.get("controlplane.step"),
                    "status": s.get("status", "UNSET"),
                }
            )
        elif op == "chat":
            out.append(
                {
                    "kind": "model",
                    "model": attrs.get("gen_ai.request.model", ""),
                    "input_tokens": attrs.get("gen_ai.usage.input_tokens", 0),
                    "output_tokens": attrs.get("gen_ai.usage.output_tokens", 0),
                }
            )
    return out
