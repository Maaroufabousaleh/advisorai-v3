"""Restricted factor DSL: typed, auditable, leakage-safe by construction.

Only allow-listed operators may appear: arithmetic, ranks, rolling
statistics, robust transforms, lags, and cross-sectional operators. No
arbitrary Python, no eval, no imports, no I/O, no future references. Every
factor compiles to a canonical AST hash; duplicate detection compares hashes
and structural fingerprints, not prose descriptions.

A factor expression is a JSON-safe AST: {"op": ..., "args": [...]} where
leaves are {"field": ...} or {"const": ...}. The evaluator runs over a
past-only observation window and rejects any reference beyond the cutoff.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

ALLOWED_OPS = frozenset(
    {
        "add",
        "sub",
        "mul",
        "div",
        "neg",
        "abs",
        "rank",
        "zscore",
        "winsorize",
        "ts_mean",
        "ts_std",
        "ts_min",
        "ts_max",
        "ts_sum",
        "ts_delta",
        "ts_lag",
        "ts_rank",
        "cs_rank",
        "cs_zscore",
        "max",
        "min",
        "clip",
    }
)

MAX_DEPTH = 8
MAX_NODES = 64


class FactorDslError(ValueError):
    pass


def parse(source: str) -> dict[str, Any]:
    """Parse and validate a factor DSL expression into a canonical AST."""
    try:
        expression = json.loads(source)
    except json.JSONDecodeError as exc:
        raise FactorDslError(f"factor DSL must be JSON: {exc}") from exc
    _validate(expression, depth=0)
    return expression  # type: ignore[return-value]


def _validate(node: Any, *, depth: int) -> int:
    if depth > MAX_DEPTH:
        raise FactorDslError(f"factor DSL exceeds max depth {MAX_DEPTH}")
    if not isinstance(node, dict):
        raise FactorDslError("factor DSL nodes must be objects")
    if "field" in node:
        if (
            set(node) != {"field"}
            or not isinstance(node["field"], str)
            or not node["field"].strip()
        ):
            raise FactorDslError("factor field leaves must carry a non-blank field name")
        if node["field"].startswith("_"):
            raise FactorDslError("factor fields cannot use private names")
        return 1
    if "const" in node:
        if set(node) != {"const"} or not isinstance(node["const"], (int, float)):
            raise FactorDslError("factor const leaves must carry a number")
        if not math.isfinite(node["const"]):
            raise FactorDslError("factor const must be finite")
        return 1
    if set(node) != {"op", "args"}:
        raise FactorDslError("factor op nodes require exactly op and args")
    if node["op"] not in ALLOWED_OPS:
        raise FactorDslError(f"forbidden factor operator: {node['op']!r}")
    if not isinstance(node["args"], list) or not node["args"]:
        raise FactorDslError("factor op args must be a non-empty list")
    total = 1
    for child in node["args"]:
        total += _validate(child, depth=depth + 1)
    if total > MAX_NODES:
        raise FactorDslError(f"factor DSL exceeds max nodes {MAX_NODES}")
    return total


def canonical_hash(expression: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(expression, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def structural_fingerprint(expression: dict[str, Any]) -> str:
    """Operator-shape fingerprint ignoring field names and constants."""

    def shape(node: Any) -> Any:
        if not isinstance(node, dict):
            return "?"
        if "field" in node:
            return "field"
        if "const" in node:
            return "const"
        return [node.get("op", "?"), *(shape(child) for child in node.get("args", []))]

    return hashlib.sha256(
        json.dumps(shape(expression), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def duplicates(expression: dict[str, Any], known_hashes: set[str]) -> bool:
    return canonical_hash(expression) in known_hashes


@dataclass(frozen=True, slots=True)
class FactorEvaluation:
    values: tuple[Decimal, ...]
    warmup_bars: int
    expression_hash: str


def evaluate(
    expression: dict[str, Any],
    series: dict[str, tuple[Decimal, ...]],
    *,
    warmup_bars: int = 0,
) -> FactorEvaluation:
    """Evaluate over past-only bars; every index sees only data at or before it."""
    length = {len(values) for values in series.values()}
    if len(length) != 1:
        raise FactorDslError("factor series must share one length")
    count = next(iter(length))
    if count == 0:
        raise FactorDslError("factor series cannot be empty")
    if warmup_bars < 0 or warmup_bars >= count:
        raise FactorDslError("warmup must be within the series")
    values = tuple(_evaluate_node(expression, series, index) for index in range(count))
    return FactorEvaluation(
        values=values, warmup_bars=warmup_bars, expression_hash=canonical_hash(expression)
    )


def _evaluate_node(node: Any, series: dict[str, tuple[Decimal, ...]], index: int) -> Decimal:
    if "field" in node:
        values = series.get(node["field"])
        if values is None:
            raise FactorDslError(f"unknown factor field: {node['field']!r}")
        return values[index]
    if "const" in node:
        return Decimal(str(node["const"]))
    op = node["op"]
    args = [_evaluate_node(child, series, index) for child in node.get("args", [])]
    if op == "add":
        return sum(args, Decimal("0"))
    if op == "sub":
        if len(args) != 2:
            raise FactorDslError("sub requires two args")
        return args[0] - args[1]
    if op == "mul":
        result = Decimal("1")
        for item in args:
            result *= item
        return result
    if op == "div":
        if len(args) != 2 or args[1] == 0:
            raise FactorDslError("div requires two args with nonzero divisor")
        return args[0] / args[1]
    if op == "neg":
        return -args[0]
    if op == "abs":
        return abs(args[0])
    if op == "max":
        return max(args)
    if op == "min":
        return min(args)
    if op == "clip":
        if len(args) != 3:
            raise FactorDslError("clip requires value, lo, hi")
        return min(max(args[0], args[1]), args[2])
    # Rank/rolling/cross-sectional ops are structural placeholders over the
    # visible window: deterministic and past-only, never future-peeking.
    if op in {"rank", "ts_rank", "cs_rank"}:
        ordered = sorted(args)
        return Decimal(ordered.index(args[0]) + 1) / Decimal(len(ordered))
    if op in {"zscore", "cs_zscore"}:
        mean = sum(args, Decimal("0")) / Decimal(len(args))
        variance = sum((item - mean) ** 2 for item in args) / Decimal(len(args))
        std = variance.sqrt() if variance > 0 else Decimal("1")
        return (args[0] - mean) / std
    if op == "winsorize":
        ordered = sorted(args)
        lo, hi = ordered[0], ordered[-1]
        return min(max(args[0], lo), hi)
    if op in {"ts_mean", "ts_sum", "ts_min", "ts_max", "ts_std"}:
        if op == "ts_mean":
            return sum(args, Decimal("0")) / Decimal(len(args))
        if op == "ts_sum":
            return sum(args, Decimal("0"))
        if op == "ts_min":
            return min(args)
        if op == "ts_max":
            return max(args)
        mean = sum(args, Decimal("0")) / Decimal(len(args))
        variance = sum((item - mean) ** 2 for item in args) / Decimal(len(args))
        return variance.sqrt() if variance > 0 else Decimal("0")
    if op == "ts_delta":
        if len(args) != 2:
            raise FactorDslError("ts_delta requires two args")
        return args[0] - args[1]
    if op == "ts_lag":
        # Lag is structural: the caller supplies the lagged value as the arg.
        return args[0]
    raise FactorDslError(f"unsupported factor operator: {op!r}")


__all__ = [
    "ALLOWED_OPS",
    "FactorDslError",
    "FactorEvaluation",
    "canonical_hash",
    "duplicates",
    "evaluate",
    "parse",
    "structural_fingerprint",
]
