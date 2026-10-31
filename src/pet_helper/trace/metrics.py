import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_P95_CUTS = 20


@dataclass(frozen=True)
class ModelRow:
    calls: int
    ttft_p50: float | None
    ttft_p95: float | None
    ttft_missing: int
    tps_median: float | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    cache_hit_rate: float | None


@dataclass(frozen=True)
class ToolRow:
    calls: int
    error_rate: float


@dataclass(frozen=True)
class ConversationRow:
    cost_usd: float
    latency_s: float


@dataclass(frozen=True)
class Summary:
    models: dict[tuple[str, str], ModelRow]
    tools: dict[str, ToolRow]
    fallback_count: int
    fallback_rate: float
    escalations: dict[str, int]
    conversations: dict[str, ConversationRow]


def _events(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def _percentile_95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    # inclusive keeps the result inside the observed range
    return statistics.quantiles(values, n=_P95_CUTS, method="inclusive")[-1]


def _tokens_per_second(call: dict[str, Any]) -> float | None:
    ttft = call.get("gen_ai.response.time_to_first_chunk")
    output = call.get("gen_ai.usage.output_tokens")
    if ttft is None or output is None:
        return None
    generation_s = call["duration_ms"] / 1000 - ttft
    return output / generation_s if generation_s > 0 else None


def _sum(calls: list[dict[str, Any]], field: str) -> int | float:
    return sum(call.get(field) or 0 for call in calls)


def _model_row(calls: list[dict[str, Any]]) -> ModelRow:
    ttfts = [
        ttft
        for call in calls
        if (ttft := call.get("gen_ai.response.time_to_first_chunk")) is not None
    ]
    rates = [rate for call in calls if (rate := _tokens_per_second(call)) is not None]
    input_tokens = _sum(calls, "gen_ai.usage.input_tokens")
    cache_read = _sum(calls, "gen_ai.usage.cache_read.input_tokens")
    return ModelRow(
        calls=len(calls),
        ttft_p50=statistics.median(ttfts) if ttfts else None,
        ttft_p95=_percentile_95(ttfts) if ttfts else None,
        ttft_missing=len(calls) - len(ttfts),
        tps_median=statistics.median(rates) if rates else None,
        input_tokens=input_tokens,
        output_tokens=_sum(calls, "gen_ai.usage.output_tokens"),
        cache_read_tokens=cache_read,
        cache_write_tokens=_sum(calls, "gen_ai.usage.cache_creation.input_tokens"),
        cost_usd=_sum(calls, "pet_helper.cost_usd"),
        cache_hit_rate=cache_read / input_tokens if input_tokens else None,
    )


def _tool_row(calls: list[dict[str, Any]]) -> ToolRow:
    errors = sum(call.get("pet_helper.status") == "error" for call in calls)
    return ToolRow(calls=len(calls), error_rate=errors / len(calls))


def _turn_latencies(events: list[dict[str, Any]]) -> dict[str, float]:
    bounds: dict[tuple[str, int, str], datetime] = {
        (event["session_id"], event["turn"], event["event"]): datetime.fromisoformat(
            event["ts"]
        )
        for event in events
        if event["event"] in ("turn.start", "turn.end")
    }
    latency: dict[str, float] = defaultdict(float)
    for session_id, turn, kind in bounds:
        if kind == "turn.end" and (session_id, turn, "turn.start") in bounds:
            start = bounds[(session_id, turn, "turn.start")]
            latency[session_id] += (
                bounds[(session_id, turn, kind)] - start
            ).total_seconds()
    return latency


def summarize(paths: Iterable[Path]) -> Summary:
    events = list(_events(paths))
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_kind[event["event"]].append(event)

    model_calls = by_kind["model.call"]
    per_model: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for call in model_calls:
        key = (call["gen_ai.provider.name"], call["gen_ai.request.model"])
        per_model[key].append(call)
    per_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in by_kind["tool.call"]:
        per_tool[call["gen_ai.tool.name"]].append(call)
    fallbacks = sum(bool(call.get("pet_helper.fallback")) for call in model_calls)

    latency = _turn_latencies(events)
    cost: dict[str, float] = defaultdict(float)
    for call in model_calls:
        cost[call["session_id"]] += call.get("pet_helper.cost_usd") or 0
    return Summary(
        models={key: _model_row(calls) for key, calls in per_model.items()},
        tools={name: _tool_row(calls) for name, calls in per_tool.items()},
        fallback_count=fallbacks,
        fallback_rate=fallbacks / len(model_calls) if model_calls else 0.0,
        escalations=dict(
            Counter(
                event["pet_helper.escalation_reason"] for event in by_kind["escalation"]
            )
        ),
        conversations={
            session_id: ConversationRow(cost[session_id], latency[session_id])
            for session_id in cost.keys() | latency.keys()
        },
    )
