"""OpenRouter client with disk caching, cost accounting and a hard spend ceiling.

Cost discipline matters here for a reason beyond thrift: the ablation ladder
(PRD 7.6) reruns the same scenarios under many conditions, and the statistical
protocol wants n>=20-30 per condition. Caching on a canonical hash of the
request makes every rerun after the first free, which is what makes a paired
off/on design affordable at all.

FR-3 is enforced structurally: nothing here reads or returns logprobs, attention
or any vendor-specific internal signal. Only message text and tool calls cross
this boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

__all__ = [
    "BACKENDS",
    "BudgetExhausted",
    "LLMClient",
    "LLMResponse",
    "ToolCall",
    "UsageMeter",
]

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class BudgetExhausted(RuntimeError):
    """Raised when the configured USD ceiling for this process is reached."""


# Three backends across three vendors, satisfying FR-4/G5. All are cheap and
# support tool calling; prices are USD per 1M tokens at time of selection.
BACKENDS: dict[str, dict[str, Any]] = {
    "primary": {
        "model": "qwen/qwen3.7-flash",
        "vendor": "alibaba",
        "in_per_mtok": 0.030,
        "out_per_mtok": 0.130,
    },
    "gemini": {
        "model": "google/gemini-2.5-flash-lite",
        "vendor": "google",
        "in_per_mtok": 0.100,
        "out_per_mtok": 0.400,
    },
    # Kept as a third vendor for the portability matrix (T-801). It is a
    # reasoning model and noticeably weaker at sustaining a long tool workflow,
    # which makes it a useful stress case rather than a bad one: the supervisor
    # has to behave identically on a backend that fails more often.
    "oss": {
        "model": "openai/gpt-oss-120b",
        "vendor": "openai-oss",
        "in_per_mtok": 0.037,
        "out_per_mtok": 0.170,
        "reasoning": {"effort": "low"},
    },
    # Adjudication is the only place an LLM may influence a control decision,
    # and only on an already-flagged conflict (FR-15). Kept separate so the
    # judge can be varied independently of the agent brain.
    "judge": {
        "model": "google/gemini-2.5-flash-lite",
        "vendor": "google",
        "in_per_mtok": 0.100,
        "out_per_mtok": 0.400,
    },
}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0
    model: str = ""
    latency_ms: float = 0.0
    cached: bool = False
    finish_reason: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class UsageMeter:
    """Process-wide spend tracker. Shared by every client instance."""

    usd: float = 0.0
    calls: int = 0
    cache_hits: int = 0
    tokens: int = 0
    ceiling: float = 2.0
    by_model: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def charge(self, model: str, usd: float, tokens: int) -> None:
        with self._lock:
            self.usd += usd
            self.calls += 1
            self.tokens += tokens
            self.by_model[model] = self.by_model.get(model, 0.0) + usd
            if self.usd > self.ceiling:
                raise BudgetExhausted(
                    f"spend ${self.usd:.4f} exceeded ceiling ${self.ceiling:.2f}; "
                    "raise CONTROLPLANE_USD_BUDGET to continue"
                )

    def note_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def summary(self) -> dict[str, Any]:
        return {
            "usd": round(self.usd, 6),
            "live_calls": self.calls,
            "cache_hits": self.cache_hits,
            "tokens": self.tokens,
            "by_model": {k: round(v, 6) for k, v in self.by_model.items()},
        }


METER = UsageMeter(ceiling=float(os.getenv("CONTROLPLANE_USD_BUDGET", "2.00")))


class LLMClient:
    """Thin OpenRouter wrapper. Deterministic-by-cache, budget-capped."""

    def __init__(
        self,
        backend: str = "primary",
        cache_dir: Path | None = None,
        meter: UsageMeter | None = None,
        timeout: float = 90.0,
    ) -> None:
        if backend not in BACKENDS:
            raise KeyError(f"unknown backend {backend!r}; have {sorted(BACKENDS)}")
        self.backend = backend
        self.spec = BACKENDS[backend]
        self.model = self.spec["model"]
        self.meter = meter or METER
        self.cache_dir = Path(cache_dir or ".cache/llm")
        self.cache_enabled = os.getenv("CONTROLPLANE_LLM_CACHE", "1") == "1"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._api_key = os.getenv("OPENROUTER_API_KEY", "")
        self._client = httpx.Client(timeout=timeout)

    # -- cache ------------------------------------------------------------

    def _cache_key(self, payload: dict[str, Any]) -> str:
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def _cache_read(self, key: str) -> LLMResponse | None:
        if not self.cache_enabled:
            return None
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
        self.meter.note_cache_hit()
        return LLMResponse(
            text=raw["text"],
            tool_calls=[ToolCall(**tc) for tc in raw["tool_calls"]],
            prompt_tokens=raw["prompt_tokens"],
            completion_tokens=raw["completion_tokens"],
            usd=0.0,
            model=raw["model"],
            latency_ms=raw.get("latency_ms", 0.0),
            cached=True,
            finish_reason=raw.get("finish_reason", ""),
        )

    def _cache_write(self, key: str, resp: LLMResponse) -> None:
        if not self.cache_enabled:
            return
        (self.cache_dir / f"{key}.json").write_text(
            json.dumps(
                {
                    "text": resp.text,
                    "tool_calls": [tc.__dict__ for tc in resp.tool_calls],
                    "prompt_tokens": resp.prompt_tokens,
                    "completion_tokens": resp.completion_tokens,
                    "model": resp.model,
                    "latency_ms": resp.latency_ms,
                    "finish_reason": resp.finish_reason,
                }
            )
        )

    # -- call -------------------------------------------------------------

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1200,
        seed: int | None = 7,
        cache_salt: str = "",
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if seed is not None:
            payload["seed"] = seed
        if "reasoning" in self.spec:
            payload["reasoning"] = self.spec["reasoning"]

        key = self._cache_key({**payload, "_salt": cache_salt})
        hit = self._cache_read(key)
        if hit is not None:
            return hit

        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set; put it in .env")

        resp = self._post_with_retry(payload)
        self.meter.charge(self.model, resp.usd, resp.total_tokens)
        self._cache_write(key, resp)
        return resp

    def _post_with_retry(self, payload: dict[str, Any], attempts: int = 4) -> LLMResponse:
        last: Exception | None = None
        for i in range(attempts):
            started = time.perf_counter()
            try:
                r = self._client.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/controlplane-prototype",
                        "X-Title": "ControlPlane",
                    },
                    json=payload,
                )
                if r.status_code in (429, 500, 502, 503, 529):
                    raise httpx.HTTPStatusError(
                        f"retryable {r.status_code}", request=r.request, response=r
                    )
                r.raise_for_status()
                return self._parse(r.json(), (time.perf_counter() - started) * 1000)
            except Exception as exc:  # noqa: BLE001 - retried, then re-raised
                last = exc
                if i < attempts - 1:
                    time.sleep(1.5 * (2**i))
        raise RuntimeError(f"OpenRouter call failed after {attempts} attempts: {last}")

    def _parse(self, data: dict[str, Any], latency_ms: float) -> LLMResponse:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {"__unparsed__": raw_args}
            calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))

        usage = data.get("usage") or {}
        pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        usd = usage.get("cost")
        if usd is None:
            usd = (pt * self.spec["in_per_mtok"] + ct * self.spec["out_per_mtok"]) / 1e6

        return LLMResponse(
            text=msg.get("content") or "",
            tool_calls=calls,
            prompt_tokens=pt,
            completion_tokens=ct,
            usd=float(usd),
            model=data.get("model", self.model),
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason", ""),
        )

    def close(self) -> None:
        self._client.close()
