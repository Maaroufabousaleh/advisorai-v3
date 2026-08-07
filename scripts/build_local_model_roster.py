#!/usr/bin/env python3
"""Build the strict Phase-0 local-model roster from immutable bake-off evidence."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from advisorai.phase0 import (
    LocalModelRosterEntry,
    Phase0LocalModelRoster,
    RosterMetric,
    RosterState,
    StabilityState,
    default_runtime_candidates,
    write_immutable_json,
)

RUNTIMES = {
    "ttm-r3": "granite-tsfm/0.3.8",
    "ttm-r2": "granite-tsfm/0.3.8",
    "chronos-2-small": "chronos-forecasting/2.3.1",
    "kronos-mini": "advisorai-kronos-runtime/67b630e",
    "kronos-small": "advisorai-kronos-runtime/67b630e",
    "tspulse": "granite-tsfm/0.3.8",
    "modern-finbert": "transformers/5.5.4",
    "finbert-minilm": "transformers/5.5.4",
    "finsentiment-deberta-v3": "transformers/5.5.4",
    "tabpfn-ts": "tabpfn-time-series/1.2.0",
}


def _hash_payload(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _metric(name: str, value: float, unit: str) -> RosterMetric:
    return RosterMetric(name=name, value=value, unit=unit)


def _external_entry(
    candidate_name: str,
    *,
    role: str,
    state: RosterState,
    stability: StabilityState,
    evidence_reference: str | None,
    evidence_hash: str | None,
    metrics: tuple[RosterMetric, ...] = (),
    notes: tuple[str, ...] = (),
) -> LocalModelRosterEntry:
    candidates = {candidate.name: candidate for candidate in default_runtime_candidates()}
    candidate = candidates[candidate_name]
    checkpoint = candidate.external_checkpoint
    if checkpoint is None:
        raise ValueError(f"external roster candidate lacks a checkpoint: {candidate_name}")
    return LocalModelRosterEntry(
        role=role,
        candidate=candidate_name,
        repository_id=checkpoint.repository.repository_id,
        revision=checkpoint.repository.revision,
        declared_license=checkpoint.repository.license,
        runtime_class=RUNTIMES[candidate_name],
        device="cuda" if candidate.gpu else "cpu",
        state=state,
        stability=stability,
        qualification_status=(
            "waiting_for_user_acceptance"
            if stability == StabilityState.WAITING_FOR_USER_ACCEPTANCE
            else "measured"
        ),
        evidence_reference=evidence_reference,
        evidence_hash=evidence_hash,
        metrics=metrics,
        notes=notes,
    )


def _builtin_entry(name: str, metrics: dict[str, object]) -> LocalModelRosterEntry:
    return LocalModelRosterEntry(
        role="mandatory_forecast_baseline",
        candidate=name,
        declared_license="project-code",
        runtime_class="advisorai-deterministic",
        device="cpu",
        state=RosterState.QUALIFIED,
        qualification_status="measured",
        metrics=(
            _metric("mae", float(metrics["mae"]), "price_units"),
            _metric("rmse", float(metrics["rmse"]), "price_units"),
            _metric("mase", float(metrics["mase"]), "ratio"),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    claimed_hash = str(report.pop("report_hash"))
    if _hash_payload(report) != claimed_hash:
        raise ValueError("local bake-off report hash is inconsistent")
    report["report_hash"] = claimed_hash
    forecasts = {item["model_name"]: item for item in report["forecast_metrics"]}
    sentiments = {item["model_name"]: item for item in report["sentiment_metrics"]}
    manifest_hashes = report["qualification_manifest_hashes"]
    qualification_root = args.report.parent / "qualification"

    def forecast_entry(name: str, role: str, state: RosterState) -> LocalModelRosterEntry:
        item = forecasts[name]
        return _external_entry(
            name,
            role=role,
            state=state,
            stability=(
                StabilityState.NOT_STARTED
                if state == RosterState.PENDING_STABILITY
                else StabilityState.NOT_STARTED
            ),
            evidence_reference=str(qualification_root / f"{name}.json"),
            evidence_hash=manifest_hashes[name],
            metrics=(
                _metric("mae", item["mae"], "price_units"),
                _metric("rmse", item["rmse"], "price_units"),
                _metric("mase", item["mase"], "ratio"),
                _metric("directional_accuracy", item["directional_accuracy"], "ratio"),
                _metric("latency_p50", item["latency_p50_ms"], "ms_per_batch"),
                _metric("peak_rss", item["peak_rss_mib"], "MiB"),
                _metric("peak_vram", item["peak_vram_mib"], "MiB"),
            )
            + (
                (_metric("interval_coverage", item["interval_coverage"], "ratio"),)
                if item["interval_coverage"] is not None
                else ()
            ),
        )

    def sentiment_entry(name: str, role: str, state: RosterState) -> LocalModelRosterEntry:
        item = sentiments[name]
        return _external_entry(
            name,
            role=role,
            state=state,
            stability=StabilityState.NOT_STARTED,
            evidence_reference=str(qualification_root / f"{name}.json"),
            evidence_hash=manifest_hashes[name],
            metrics=(
                _metric("accuracy", item["accuracy"], "ratio"),
                _metric("macro_f1", item["macro_f1"], "ratio"),
                _metric("expected_calibration_error", item["expected_calibration_error"], "ratio"),
                _metric("throughput", item["throughput_per_second"], "items_per_second"),
                _metric("peak_rss", item["peak_rss_mib"], "MiB"),
            ),
        )

    tspulse = report["tspulse_characterization"]
    roster = Phase0LocalModelRoster(
        roster_version="2026-08-07.1",
        generated_at=datetime.fromisoformat(report["measured_at"]),
        benchmark_evidence_reference=str(args.report),
        benchmark_evidence_hash=claimed_hash,
        forecast_dataset_hash=report["forecast_snapshot"]["content_hash"],
        sentiment_dataset_hash=report["sentiment_snapshot"]["content_hash"],
        forecast_primary=forecast_entry("ttm-r2", "forecast_primary", RosterState.PENDING_STABILITY),
        forecast_fast=forecast_entry("ttm-r2", "forecast_fast", RosterState.PENDING_STABILITY),
        forecast_challengers=(
            forecast_entry("ttm-r3", "forecast_challenger", RosterState.QUALIFIED),
            forecast_entry("kronos-mini", "forecast_challenger", RosterState.QUALIFIED),
            forecast_entry("kronos-small", "forecast_challenger", RosterState.QUALIFIED),
        ),
        probabilistic_forecast=forecast_entry(
            "chronos-2-small", "probabilistic_forecast", RosterState.QUALIFIED
        ),
        feature_regime_model=_external_entry(
            "tspulse",
            role="anomaly_integrity_representation_regime_features",
            state=RosterState.QUALIFIED,
            stability=StabilityState.NOT_STARTED,
            evidence_reference=str(qualification_root / "tspulse.json"),
            evidence_hash=manifest_hashes["tspulse"],
            metrics=(
                _metric("feature_dimension", tspulse["feature_dimension"], "features"),
                _metric("latency_p50", tspulse["latency_p50_ms"], "ms_per_batch"),
                _metric("peak_rss", tspulse["peak_rss_mib"], "MiB"),
            ),
            notes=("price forecasting is prohibited",),
        ),
        finance_sentiment_primary=sentiment_entry(
            "finsentiment-deberta-v3",
            "finance_sentiment_primary",
            RosterState.PENDING_STABILITY,
        ),
        finance_sentiment_fast=sentiment_entry(
            "finbert-minilm",
            "finance_sentiment_fast",
            RosterState.PENDING_STABILITY,
        ),
        finance_sentiment_challengers=(
            sentiment_entry(
                "modern-finbert", "finance_sentiment_challenger", RosterState.QUALIFIED
            ),
        ),
        mandatory_baselines=tuple(
            _builtin_entry(name, forecasts[name])
            for name in ("naive", "drift", "seasonal-7", "linear", "lightgbm")
        ),
        inactive_or_waiting=(
            _external_entry(
                "tabpfn-ts",
                role="later_forecast_challenger",
                state=RosterState.QUARANTINED,
                stability=StabilityState.WAITING_FOR_USER_ACCEPTANCE,
                evidence_reference=(
                    "artifacts/phase0/model-runtime-qualification/"
                    "acquisition-20260807T222217.331347Z/tabpfn-ts/acquisition-failure.json"
                ),
                evidence_hash=(
                    "1a6257a9d59ff3848001d4db1f126301cadc58c4bb223e7e88f8b2ee1dcc2436"
                ),
                notes=("upstream access is gated and requires personal terms acceptance",),
            ),
        ),
    )
    write_immutable_json(args.output, roster.model_dump(mode="json"))
    print(json.dumps({"output": str(args.output), "roster_version": roster.roster_version}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
