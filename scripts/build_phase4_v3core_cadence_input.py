#!/usr/bin/env python3
"""Build a typed V3-Core 5-minute/1-hour Phase-4 input from frozen local bars.

The input is deliberately separate from the current daily Phase-4 schema. The
command is offline and accepts only a caller-supplied immutable bar artifact;
it never acquires data or invokes a model. Missing bars become retained
rejections, while source changes and duplicate bars fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from advisorai.gates import GateDecision, PhaseGateRecord
from advisorai.phase4 import (
    EVALUATION_INPUT_SCHEMA,
    V3CoreBar,
    V3CoreEvaluationInput,
    build_v3core_cases,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BAR_INPUT_SCHEMA = "advisorai.phase4.v3-core-bars.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()


def _canonical(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _write_new(path: Path, payload: object) -> str:
    encoded = _canonical(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(encoded).hexdigest()


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def _load_bars(path: Path) -> tuple[dict[str, Any], tuple[V3CoreBar, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != BAR_INPUT_SCHEMA:
        raise ValueError("bar input schema is not the reviewed V3-Core bar schema")
    source = payload.get("source")
    raw_bars = payload.get("bars")
    if not isinstance(source, dict) or not isinstance(raw_bars, list) or not raw_bars:
        raise ValueError("bar input requires a source object and non-empty bars")
    required = ("source_id", "provider_identity", "endpoint", "source_snapshot_hash")
    if any(not isinstance(source.get(key), str) for key in required):
        raise ValueError("bar input source identity is incomplete")
    bars = tuple(V3CoreBar.model_validate(item) for item in raw_bars)
    return source, bars


def _load_phase3_gate(path: Path, *, at: datetime) -> tuple[PhaseGateRecord, str]:
    record = PhaseGateRecord.model_validate_json(path.read_text(encoding="utf-8"))
    if record.phase != 3 or record.decision is not GateDecision.PASSED:
        raise ValueError("a passed Phase-3 gate record is required")
    if not record.is_valid_at(at):
        raise ValueError("the Phase-3 gate record is not valid at input-build time")
    return record, _sha256(path)


def build_input(
    *,
    bars_path: Path,
    phase3_gate_path: Path,
    output_root: Path,
    source_id: str,
    provider_identity: str,
    endpoint: str,
    spread_bps: str = "2",
    slippage_bps: str = "2",
    phase3_admitted: bool = True,
) -> dict[str, str | int | bool]:
    if output_root.exists():
        raise FileExistsError("output root must be new; cadence input is immutable")
    generated_at = datetime.now(UTC)
    source, bars = _load_bars(bars_path.resolve())
    if (
        source["source_id"] != source_id
        or source["provider_identity"] != provider_identity
        or source["endpoint"] != endpoint
    ):
        raise ValueError("requested source identity does not match the bar artifact")
    gate, gate_sha256 = _load_phase3_gate(phase3_gate_path.resolve(), at=generated_at)
    build = build_v3core_cases(
        bars,
        source_id=source_id,
        provider_identity=provider_identity,
        endpoint=endpoint,
        source_snapshot_hash=source["source_snapshot_hash"],
        spread_bps=Decimal(spread_bps),
        slippage_bps=Decimal(slippage_bps),
        phase3_admitted=phase3_admitted,
    )
    typed = V3CoreEvaluationInput(
        plan_id="phase4-v3-core-1h-5m-v1",
        phase3_gate_record_sha256=gate_sha256,
        build=build,
    )
    payload = {
        "schema": EVALUATION_INPUT_SCHEMA,
        "generated_at": generated_at.isoformat(),
        "repository_commit": _git_head(),
        "bars_input": {
            "path": _relative(bars_path),
            "sha256": _sha256(bars_path),
            "bar_count": len(bars),
        },
        "phase3_gate": {
            "path": _relative(phase3_gate_path),
            "sha256": gate_sha256,
            "decision": gate.decision.value,
        },
        "input": typed.model_dump(mode="json"),
        "case_counts": typed.case_counts(),
        "minimum_case_status": (
            "READY_FOR_MEASUREMENT" if typed.meets_minimum() else "PENDING_FRESH_PIT_DATA"
        ),
        "network_calls": 0,
        "credentials_loaded": False,
        "model_weights_loaded": False,
        "order_writes_attempted": False,
        "execution_authority": {
            "risk_kernel": "unchanged_external_authority",
            "oms": "unchanged_external_authority",
            "model_order_authority": False,
            "dashboard_order_authority": False,
        },
    }
    output_root.mkdir(parents=True)
    evidence_path = output_root / "phase4-v3core-cadence-input.json"
    evidence_sha256 = _write_new(evidence_path, payload)
    digest_path = output_root / "phase4-v3core-cadence-input.sha256"
    digest_path.write_text(f"{evidence_sha256}  {evidence_path.name}\n", encoding="ascii")
    return {
        "evidence": _relative(evidence_path),
        "sha256": evidence_sha256,
        "case_count": len(build.cases),
        "ready": typed.meets_minimum(),
        "network_calls": False,
        "order_writes_attempted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument("--phase3-gate", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--provider-identity", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--spread-bps", default="2")
    parser.add_argument("--slippage-bps", default="2")
    args = parser.parse_args()
    try:
        result = build_input(
            bars_path=args.bars,
            phase3_gate_path=args.phase3_gate,
            output_root=args.output_root.resolve(),
            source_id=args.source_id,
            provider_identity=args.provider_identity,
            endpoint=args.endpoint,
            spread_bps=args.spread_bps,
            slippage_bps=args.slippage_bps,
        )
    except (FileExistsError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"V3-Core cadence input refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
