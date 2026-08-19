from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from advisorai.collectors.sources import HttpResponse
from advisorai.phase4 import (
    STABILITY_RULE_VERSION,
    ForwardHealthLedger,
    ForwardNormalizedBarSpool,
    ForwardPredictionLedger,
    ForwardPredictionOutcomeLinkLedger,
    ForwardPredictionRecord,
    ForwardRawSpool,
    IntegrityAuditError,
    audit_forward_root,
    build_exclusion_overlay,
    build_v3core_cases,
    parse_binance_klines,
)
from advisorai.phase4.v3core_integrity import (
    IntegrityAuditReport,
    _hash_payload,
    _normalized_identity_payload,
)
from scripts.audit_phase4_v3core_integrity import (
    _ensure_output_is_separate,
)
from scripts.audit_phase4_v3core_integrity import (
    main as audit_integrity_cli,
)
from scripts.link_phase4_v3core_prediction_outcomes import (
    OutcomeLinkRefused,
    link_predictions_to_cases,
)

HASH = "a" * 64
PHASE3_HASH = "b" * 64
START = datetime(2026, 8, 17, 22, 0, tzinfo=UTC)
ENDPOINT = "https://data-api.binance.vision/api/v3/klines"


def _row(index: int = 0, *, close: str | None = None) -> list[object]:
    interval_start = START + timedelta(minutes=5 * index)
    interval_end = interval_start + timedelta(minutes=5)
    close_value = Decimal(close or str(100 + index))
    return [
        int(interval_start.timestamp() * 1000),
        str(close_value - 1),
        str(close_value + 1),
        str(close_value - 2),
        str(close_value),
        "2",
        int(interval_end.timestamp() * 1000) - 1,
        "200",
        4,
        "1",
        "100",
    ]


def _response(row: list[object], received_at: datetime) -> HttpResponse:
    return HttpResponse(
        status_code=200,
        body=json.dumps([row]).encode(),
        fetched_at=received_at,
        url=f"{ENDPOINT}?interval=5m&limit=2&symbol=BTCUSDT",
    )


def _health_ledger(tmp_path: Path, *, state: str = "HEALTHY") -> Path:
    path = tmp_path / "source-health.jsonl"
    ledger = ForwardHealthLedger(path)
    ledger.append(
        symbol="BTCUSDT",
        observed_at=START + timedelta(minutes=6),
        to_state=state,
        reason="closed_bar_received",
        last_valid_interval_end=START + timedelta(minutes=5),
        last_collected_at=START + timedelta(minutes=6),
    )
    return path


def _single_bar_audit(
    tmp_path: Path,
    rows: list[list[object]],
    *,
    canonical_row: list[object],
    symbol: str = "BTCUSDT",
    request_url: str | None = None,
):
    raw_path = tmp_path / "raw-responses.jsonl"
    normalized_path = tmp_path / "normalized-bars.jsonl"
    raw = ForwardRawSpool(raw_path)
    for offset, row in enumerate(rows, start=1):
        raw.append(
            _response(row, START + timedelta(minutes=6, seconds=offset)),
            symbol=symbol,
            request_url=request_url or f"{ENDPOINT}?interval=5m&limit=2&symbol={symbol}",
        )
    normalized = ForwardNormalizedBarSpool(normalized_path)
    canonical = parse_binance_klines(
        json.dumps([canonical_row]).encode(),
        symbol=symbol,
        collected_at=START + timedelta(minutes=6),
        source_snapshot_hash=HASH,
    )[0]
    assert normalized.append(canonical)
    report = audit_forward_root(
        raw_path,
        normalized_path,
        terminal_observed_at=START + timedelta(minutes=10),
    )
    return report, raw_path, normalized_path


@pytest.mark.parametrize(
    ("name", "rows", "canonical", "expected"),
    (
        (
            "stable",
            [_row(), _row()],
            _row(),
            "STABLE",
        ),
        (
            "revised_then_canonical_final",
            [_row(), _row(close="101"), _row(close="101")],
            _row(close="101"),
            "REVISED_BUT_CANONICAL_FINAL",
        ),
        (
            "alternating_unresolved",
            [_row(), _row(close="101"), _row(), _row(close="101")],
            _row(),
            "UNRESOLVED",
        ),
        (
            "canonical_first_is_terminal",
            [_row(), _row(close="101"), _row(), _row()],
            _row(),
            "REVISED_BUT_CANONICAL_FINAL",
        ),
        (
            "canonical_first_differs_from_terminal",
            [_row(), _row(close="101"), _row(close="101")],
            _row(),
            "REVISED_CANONICAL_DISAGREES",
        ),
    ),
)
def test_terminal_stability_classification_is_deterministic(
    tmp_path: Path,
    name: str,
    rows: list[list[object]],
    canonical: list[object],
    expected: str,
) -> None:
    del name
    report, _, _ = _single_bar_audit(tmp_path, rows, canonical_row=canonical)
    record = report.bar_records[0]
    assert record.classification == expected
    assert report.stability_rule_version == STABILITY_RULE_VERSION


def test_audit_scientific_fingerprint_is_reproducible_across_output_runs(
    tmp_path: Path,
) -> None:
    first, _raw_one, _normalized_one = _single_bar_audit(
        tmp_path / "first",
        [_row(), _row()],
        canonical_row=_row(),
    )
    second, _raw_two, _normalized_two = _single_bar_audit(
        tmp_path / "second",
        [_row(), _row()],
        canonical_row=_row(),
    )
    assert first.audit_fingerprint == second.audit_fingerprint


def test_malformed_numeric_ohlcv_in_http_200_fails_closed(tmp_path: Path) -> None:
    malformed = _row()
    malformed[4] = "not-a-number"
    with pytest.raises(IntegrityAuditError, match="non-numeric kline OHLCV"):
        _single_bar_audit(tmp_path, [malformed], canonical_row=_row())


def test_auditor_records_changed_fields_versions_and_repeated_observations(tmp_path: Path) -> None:
    report, _, _ = _single_bar_audit(
        tmp_path,
        [_row(), _row(close="101"), _row(close="101")],
        canonical_row=_row(),
    )
    record = report.bar_records[0]
    assert record.classification == "REVISED_CANONICAL_DISAGREES"
    assert record.revision_count == 1
    assert record.terminal_consecutive_observations == 2
    assert record.repeated_identical_observation_count == 1
    assert record.changed_ohlcv_fields == ("open", "high", "low", "close")
    assert len(record.raw_versions) == 2
    assert record.final_observed_value.ohlcv["close"] == "101"
    assert record.first_normalized_observation is not None


def test_source_health_chain_is_validated_and_bound_to_normalized_state(
    tmp_path: Path,
) -> None:
    report, raw_path, normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
    )
    assert report.source_health_ledger_valid is False

    health_path = _health_ledger(tmp_path, state="HEALTHY")
    checked = audit_forward_root(
        raw_path,
        normalized_path,
        source_health_path=health_path,
        terminal_observed_at=START + timedelta(minutes=10),
    )
    assert checked.source_health_ledger_valid is True
    assert checked.source_health_ledger_sha256 is not None

    health_record = json.loads(health_path.read_text(encoding="utf-8"))
    health_record["record_hash"] = "b" * 64
    health_path.write_text(json.dumps(health_record) + "\n", encoding="utf-8")
    with pytest.raises(IntegrityAuditError, match="source-health"):
        audit_forward_root(
            raw_path,
            normalized_path,
            source_health_path=health_path,
            terminal_observed_at=START + timedelta(minutes=10),
        )


def test_missing_prediction_ledger_cannot_be_admission_ready(tmp_path: Path) -> None:
    report, _raw_path, _normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
    )
    assert report.prediction_ledgers_valid is False
    assert report.integrity_ready is False
    assert report.admission_evidence_ready is False


def test_metadata_only_raw_revision_is_recorded_separately_from_ohlcv(
    tmp_path: Path,
) -> None:
    first = _row()
    revised = _row()
    revised[8] = 5  # Provider trade-count metadata changes; OHLCV does not.
    report, _raw_path, _normalized_path = _single_bar_audit(
        tmp_path,
        [first, revised, revised],
        canonical_row=first,
    )
    record = report.bar_records[0]
    assert len(record.raw_versions) == 2
    assert record.raw_versions[0].raw_ohlcv_hash == record.raw_versions[1].raw_ohlcv_hash
    assert record.revision_count == 1
    assert record.changed_ohlcv_fields == ()
    assert record.classification == "REVISED_BUT_CANONICAL_FINAL"


def test_open_observation_is_retained_but_does_not_prove_terminal_stability(tmp_path: Path) -> None:
    row = _row()
    raw_path = tmp_path / "raw.jsonl"
    normalized_path = tmp_path / "normalized.jsonl"
    raw = ForwardRawSpool(raw_path)
    raw.append(
        _response(row, START + timedelta(minutes=4, seconds=59)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    ForwardNormalizedBarSpool(normalized_path)
    # The normalized plane cannot contain an open row; the empty normalized
    # input makes the terminal audit explicitly unresolved.
    normalized_path.touch()
    report = audit_forward_root(
        raw_path,
        normalized_path,
        terminal_observed_at=START + timedelta(minutes=5),
    )
    assert report.raw_observation_count == 1
    assert report.bar_records[0].raw_observations[0].closed_at_receipt is False
    assert report.bar_records[0].classification == "UNRESOLVED"


def test_terminal_boundary_cannot_exclude_later_raw_receipts(tmp_path: Path) -> None:
    report, raw_path, normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
    )
    del report
    with pytest.raises(IntegrityAuditError, match="terminal boundary"):
        audit_forward_root(
            raw_path,
            normalized_path,
            terminal_observed_at=START + timedelta(minutes=6),
        )


def _write_multi_symbol_case_fixture(
    tmp_path: Path, *, include_prediction_source_snapshot: bool = True
) -> tuple[Path, Path, Path, Path, Path]:
    raw_path = tmp_path / "raw.jsonl"
    normalized_path = tmp_path / "normalized.jsonl"
    cases_path = tmp_path / "completed-cases.jsonl"
    predictions_path = tmp_path / "predictions.jsonl"
    links_path = tmp_path / "outcome-links.jsonl"
    raw = ForwardRawSpool(raw_path)
    normalized = ForwardNormalizedBarSpool(normalized_path)
    btc_bars = []
    for index in range(-1, 72):
        first = _row(index)
        variants = [first, _row(index, close=str(101 + index)), _row(index, close=str(101 + index))]
        if index not in {0, 50}:
            variants = [first, first]
        for offset, row in enumerate(variants, start=1):
            raw.append(
                _response(row, START + timedelta(minutes=5 * (index + 1), seconds=offset)),
                symbol="BTCUSDT",
                request_url=ENDPOINT,
            )
        bar = parse_binance_klines(
            json.dumps([first]).encode(),
            symbol="BTCUSDT",
            collected_at=START + timedelta(minutes=5 * (index + 1), seconds=1),
            source_snapshot_hash=HASH,
        )[0]
        normalized.append(bar)
        btc_bars.append(bar)
    eth_bars = []
    for index in range(-1, 60):
        row = _row(index)
        for offset in (1, 2):
            raw.append(
                _response(row, START + timedelta(minutes=5 * (index + 1), seconds=offset)),
                symbol="ETHUSDT",
                request_url=ENDPOINT,
            )
        bar = parse_binance_klines(
            json.dumps([row]).encode(),
            symbol="ETHUSDT",
            collected_at=START + timedelta(minutes=5 * (index + 1), seconds=1),
            source_snapshot_hash=HASH,
        )[0]
        normalized.append(bar)
        eth_bars.append(bar)
    btc_build = build_v3core_cases(
        btc_bars,
        evidence_class="forward_pit_admission",
        source_id="binance_spot_public_market_data",
        provider_identity="binance_spot_public_market_data",
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=True,
    )
    eth_build = build_v3core_cases(
        eth_bars,
        evidence_class="forward_pit_admission",
        source_id="binance_spot_public_market_data",
        provider_identity="binance_spot_public_market_data",
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=True,
    )
    cases_path.write_text(
        "".join(
            json.dumps(
                {
                    "schema": "advisorai.phase4.v3-core-forward.case.v1",
                    "case": case.model_dump(mode="json"),
                    "case_hash": _case_hash(case.model_dump(mode="json")),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for case in (*btc_build.cases, *eth_build.cases)
        ),
        encoding="utf-8",
    )
    btc_case = btc_build.cases[0]
    eth_case = eth_build.cases[0]
    prediction_ledger = ForwardPredictionLedger(predictions_path)
    for model, case in (("lightgbm", btc_case), ("lightgbm", eth_case)):
        prediction_fields = {
            "prediction_id": f"{case.case_id}:{model}",
            "instrument": case.instrument,
            "model": model,
            "model_identity_hash": HASH,
            "cutoff": case.cutoff,
            "input_snapshot_hash": HASH,
            "predicted_return_bps": Decimal("1"),
            "generated_at": case.cutoff - timedelta(seconds=1),
            "runtime_latency_ms": Decimal("1"),
        }
        if include_prediction_source_snapshot:
            prediction_fields["source_snapshot_hash"] = HASH
        prediction_ledger.append(ForwardPredictionRecord(**prediction_fields))
    outcome_links = ForwardPredictionOutcomeLinkLedger(links_path)
    for entry, case in zip(prediction_ledger.records, (btc_case, eth_case), strict=True):
        outcome_links.append(
            prediction_id=entry.prediction.prediction_id,
            outcome_case_id=case.case_id,
            linked_at=case.realized_at,
        )
    return raw_path, normalized_path, cases_path, predictions_path, links_path


def _case_hash(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def test_prediction_outcome_linker_creates_later_immutable_links(
    tmp_path: Path,
) -> None:
    _raw, _normalized, cases_path, predictions_path, _links = _write_multi_symbol_case_fixture(
        tmp_path
    )
    output_path = tmp_path / "new-outcome-links.jsonl"
    result = link_predictions_to_cases(
        prediction_ledger_paths=(predictions_path,),
        completed_cases_path=cases_path,
        output_path=output_path,
    )
    assert result["prediction_count"] == 2
    assert result["linked_count"] == 2
    assert len(output_path.read_text(encoding="utf-8").splitlines()) == 2


def test_prediction_outcome_linker_refuses_missing_case_without_output(
    tmp_path: Path,
) -> None:
    _raw, _normalized, cases_path, predictions_path, _links = _write_multi_symbol_case_fixture(
        tmp_path
    )
    first_case = cases_path.read_text(encoding="utf-8").splitlines()[0] + "\n"
    cases_path.write_text(first_case, encoding="utf-8")
    output_path = tmp_path / "missing-outcome-links.jsonl"
    with pytest.raises(OutcomeLinkRefused, match="outcomes are not available"):
        link_predictions_to_cases(
            prediction_ledger_paths=(predictions_path,),
            completed_cases_path=cases_path,
            output_path=output_path,
        )
    assert not output_path.exists()


def test_context_and_outcome_contamination_excludes_only_affected_predictions(
    tmp_path: Path,
) -> None:
    raw_path, normalized_path, cases_path, predictions_path, links_path = (
        _write_multi_symbol_case_fixture(tmp_path)
    )
    report = audit_forward_root(
        raw_path,
        normalized_path,
        completed_cases_path=cases_path,
        prediction_ledger_paths=(predictions_path,),
        outcome_link_ledger_paths=(links_path,),
        terminal_observed_at=START + timedelta(days=2),
    )
    assert report.raw_completed_case_counts == {"BTCUSDT": 2, "ETHUSDT": 1}
    assert report.integrity_eligible_case_counts == {"BTCUSDT": 0, "ETHUSDT": 1}
    assert len(report.contaminated_cases) == 2
    btc_contaminated = next(
        item for item in report.contaminated_cases if item.instrument == "BTCUSDT"
    )
    assert set(btc_contaminated.affected_segments) == {"context", "outcome"}
    assert len(report.excluded_predictions) == 1
    assert report.excluded_predictions[0].status == "EXCLUDED_DATA_INTEGRITY"
    assert report.excluded_predictions[0].instrument == "BTCUSDT"
    assert report.admission_minimum_met is False


def test_prediction_source_identity_is_bound_to_source_manifest(tmp_path: Path) -> None:
    raw_path, normalized_path, cases_path, predictions_path, links_path = (
        _write_multi_symbol_case_fixture(tmp_path)
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"source_snapshot_hash": HASH}), encoding="utf-8")
    report = audit_forward_root(
        raw_path,
        normalized_path,
        completed_cases_path=cases_path,
        prediction_ledger_paths=(predictions_path,),
        outcome_link_ledger_paths=(links_path,),
        terminal_observed_at=START + timedelta(days=2),
        source_manifest_path=manifest_path,
    )
    assert report.prediction_source_identity_valid is True

    manifest_path.write_text(json.dumps({"source_snapshot_hash": "c" * 64}), encoding="utf-8")
    mismatched = audit_forward_root(
        raw_path,
        normalized_path,
        completed_cases_path=cases_path,
        prediction_ledger_paths=(predictions_path,),
        outcome_link_ledger_paths=(links_path,),
        terminal_observed_at=START + timedelta(days=2),
        source_manifest_path=manifest_path,
    )
    assert mismatched.prediction_source_identity_valid is False


def test_prediction_source_identity_limitation_is_not_silently_passed(
    tmp_path: Path,
) -> None:
    raw_path, normalized_path, cases_path, predictions_path, links_path = (
        _write_multi_symbol_case_fixture(tmp_path)
    )
    report = audit_forward_root(
        raw_path,
        normalized_path,
        completed_cases_path=cases_path,
        prediction_ledger_paths=(predictions_path,),
        outcome_link_ledger_paths=(links_path,),
        terminal_observed_at=START + timedelta(days=2),
    )
    assert report.prediction_source_identity_valid is False
    assert report.integrity_ready is False


def test_audit_output_cannot_overlap_an_evidence_root(tmp_path: Path) -> None:
    evidence_root = tmp_path / "sealed-root"
    evidence_root.mkdir()
    raw_path = evidence_root / "raw-responses.jsonl"

    with pytest.raises(SystemExit, match="separate"):
        _ensure_output_is_separate(evidence_root / "audit" / "report.json", [raw_path])

    with pytest.raises(SystemExit, match="separate"):
        _ensure_output_is_separate(tmp_path, [raw_path])


def test_unsealed_diagnostic_cannot_be_admission_evidence(tmp_path: Path) -> None:
    raw_path, normalized_path, cases_path, predictions_path, links_path = (
        _write_multi_symbol_case_fixture(tmp_path)
    )
    report = audit_forward_root(
        raw_path,
        normalized_path,
        completed_cases_path=cases_path,
        prediction_ledger_paths=(predictions_path,),
        outcome_link_ledger_paths=(links_path,),
        terminal_observed_at=START + timedelta(days=2),
        terminal_evidence_eligible=False,
    )
    assert report.terminal_evidence_eligible is False
    assert report.admission_evidence_ready is False
    assert report.admission_minimum_met is False


def test_terminal_audit_requires_explicit_sealed_status(tmp_path: Path) -> None:
    _report, raw_path, normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
    )
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps({"state": "running"}), encoding="utf-8")

    with pytest.raises(IntegrityAuditError, match="sealed source status"):
        audit_forward_root(
            raw_path,
            normalized_path,
            source_status_path=status_path,
            terminal_evidence_eligible=True,
            auditor_repository_commit="a" * 40,
            terminal_observed_at=START + timedelta(minutes=10),
        )

    status_path.write_text(
        json.dumps({"state": "target_reached", "minimum_reached": False}), encoding="utf-8"
    )
    with pytest.raises(IntegrityAuditError, match="frozen minimum"):
        audit_forward_root(
            raw_path,
            normalized_path,
            source_status_path=status_path,
            terminal_evidence_eligible=True,
            auditor_repository_commit="a" * 40,
            terminal_observed_at=START + timedelta(minutes=10),
        )

    status_path.write_text(
        json.dumps({"state": "target_reached", "minimum_reached": True}), encoding="utf-8"
    )
    with pytest.raises(IntegrityAuditError, match="repository commit"):
        audit_forward_root(
            raw_path,
            normalized_path,
            source_status_path=status_path,
            terminal_evidence_eligible=True,
            terminal_observed_at=START + timedelta(minutes=10),
        )

    diagnostic = audit_forward_root(
        raw_path,
        normalized_path,
        source_status_path=status_path,
        terminal_evidence_eligible=False,
        terminal_observed_at=START + timedelta(minutes=10),
    )
    assert diagnostic.terminal_evidence_eligible is False


def test_terminal_audit_requires_reviewed_source_manifest_contract(tmp_path: Path) -> None:
    _report, raw_path, normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
    )
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps({"state": "target_reached", "minimum_reached": True}), encoding="utf-8"
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "provider_identity": "unreviewed_source",
                "endpoint": ENDPOINT,
                "evidence_class": "forward_pit_admission",
                "interval": "5m",
                "symbols": ["BTCUSDT", "ETHUSDT"],
                "market_data_only": True,
                "credentials_loaded": False,
                "order_writes_attempted": False,
                "source_snapshot_hash": HASH,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(IntegrityAuditError, match="provider identity"):
        audit_forward_root(
            raw_path,
            normalized_path,
            source_manifest_path=manifest_path,
            source_status_path=status_path,
            terminal_evidence_eligible=True,
            auditor_repository_commit="a" * 40,
            terminal_observed_at=START + timedelta(minutes=10),
        )


def test_terminal_audit_rejects_normalized_source_substitution(tmp_path: Path) -> None:
    _report, raw_path, normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
    )
    bar = parse_binance_klines(
        json.dumps([_row()]).encode(),
        symbol="BTCUSDT",
        collected_at=START + timedelta(minutes=6),
        source_snapshot_hash=HASH,
    )[0]
    substituted_provenance = bar.provenance.model_copy(update={"normalized_record_hash": "0" * 64})
    substituted = bar.model_copy(
        update={
            "source_id": "alternate_public_source",
            "provider_identity": "alternate_public_source",
            "endpoint": "https://alternate.example/klines",
            "provenance": substituted_provenance,
        }
    )
    normalized_hash = _hash_payload(_normalized_identity_payload(substituted))
    substituted = substituted.model_copy(
        update={
            "provenance": substituted.provenance.model_copy(
                update={"normalized_record_hash": normalized_hash}
            )
        }
    )
    normalized_path.write_text(substituted.model_dump_json() + "\n", encoding="utf-8")
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps({"state": "target_reached", "minimum_reached": True}), encoding="utf-8"
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "provider_identity": "binance_spot_public_market_data",
                "endpoint": ENDPOINT,
                "evidence_class": "forward_pit_admission",
                "interval": "5m",
                "symbols": ["BTCUSDT", "ETHUSDT"],
                "market_data_only": True,
                "credentials_loaded": False,
                "order_writes_attempted": False,
                "source_snapshot_hash": HASH,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(IntegrityAuditError, match="normalized bar source identity"):
        audit_forward_root(
            raw_path,
            normalized_path,
            source_manifest_path=manifest_path,
            source_status_path=status_path,
            terminal_evidence_eligible=True,
            auditor_repository_commit="a" * 40,
            terminal_observed_at=START + timedelta(minutes=10),
        )


def test_terminal_audit_rejects_raw_request_url_source_substitution(tmp_path: Path) -> None:
    _report, raw_path, normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
        request_url="https://api.binance.com/api/v3/klines?interval=5m&limit=2&symbol=BTCUSDT",
    )
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps({"state": "target_reached", "minimum_reached": True}), encoding="utf-8"
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "provider_identity": "binance_spot_public_market_data",
                "endpoint": ENDPOINT,
                "evidence_class": "forward_pit_admission",
                "interval": "5m",
                "symbols": ["BTCUSDT", "ETHUSDT"],
                "market_data_only": True,
                "credentials_loaded": False,
                "order_writes_attempted": False,
                "source_snapshot_hash": HASH,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(IntegrityAuditError, match="raw request URL"):
        audit_forward_root(
            raw_path,
            normalized_path,
            source_manifest_path=manifest_path,
            source_status_path=status_path,
            terminal_evidence_eligible=True,
            auditor_repository_commit="a" * 40,
            terminal_observed_at=START + timedelta(minutes=10),
        )


def test_allow_unsealed_cli_marks_report_diagnostic_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "run"
    report, raw_path, normalized_path = _single_bar_audit(
        run_root,
        [_row(), _row()],
        canonical_row=_row(),
    )
    del report
    (run_root / "status.json").write_text('{"state":"running"}\n', encoding="utf-8")
    output = tmp_path / "diagnostic" / "audit.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_phase4_v3core_integrity.py",
            "--run-directory",
            str(run_root),
            "--allow-unsealed",
            "--terminal-observed-at",
            (START + timedelta(minutes=10)).isoformat(),
            "--repository-commit",
            "a" * 40,
            "--output",
            str(output),
        ],
    )

    assert audit_integrity_cli() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["terminal_evidence_eligible"] is False
    assert payload["admission_evidence_ready"] is False
    assert payload["admission_minimum_met"] is False
    assert payload["auditor_repository_commit"] == "a" * 40
    assert raw_path.read_bytes()
    assert normalized_path.read_bytes()


def test_report_rejects_inconsistent_unsealed_admission_flags(tmp_path: Path) -> None:
    report, _raw_path, _normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
    )
    payload = report.model_dump(mode="json")
    payload["terminal_evidence_eligible"] = False
    payload["admission_evidence_ready"] = True
    with pytest.raises(ValueError, match="cannot be admission-ready"):
        IntegrityAuditReport.model_validate(payload)


def test_report_fingerprint_rejects_content_mutation(tmp_path: Path) -> None:
    report, _raw_path, _normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
    )
    payload = report.model_dump(mode="json")
    payload["normalized_bar_count"] = payload["normalized_bar_count"] + 1
    with pytest.raises(ValueError, match="audit fingerprint"):
        IntegrityAuditReport.model_validate(payload)


def test_missing_terminal_eligibility_defaults_to_diagnostic(tmp_path: Path) -> None:
    report, _raw_path, _normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
    )
    payload = report.model_dump(mode="json")
    payload.pop("terminal_evidence_eligible")
    restored = IntegrityAuditReport.model_validate(payload)
    assert restored.terminal_evidence_eligible is False
    assert restored.admission_evidence_ready is False
    assert restored.admission_minimum_met is False


def test_legacy_admission_alias_also_requires_case_content_validation(tmp_path: Path) -> None:
    report, _raw_path, _normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
    )
    payload = report.model_dump(mode="json")
    payload["terminal_evidence_eligible"] = True
    payload["completed_case_content_valid"] = False
    payload["admission_evidence_ready"] = False
    payload["admission_minimum_met"] = True
    with pytest.raises(ValueError, match="validated case content"):
        IntegrityAuditReport.model_validate(payload)


def test_case_content_must_match_audited_normalized_bars(tmp_path: Path) -> None:
    raw_path, normalized_path, cases_path, predictions_path, links_path = (
        _write_multi_symbol_case_fixture(tmp_path)
    )
    lines = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines()]
    lines[0]["case"]["context_bars"][0]["close"] = "99.5"
    lines[0]["case_hash"] = _case_hash(lines[0]["case"])
    cases_path.write_text(
        "".join(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n" for line in lines),
        encoding="utf-8",
    )
    report = audit_forward_root(
        raw_path,
        normalized_path,
        completed_cases_path=cases_path,
        prediction_ledger_paths=(predictions_path,),
        outcome_link_ledger_paths=(links_path,),
        terminal_observed_at=START + timedelta(days=2),
    )
    assert report.completed_case_content_valid is False
    assert any(
        "differs from audited normalized evidence" in item
        for item in report.completed_case_content_limitations
    )
    assert report.integrity_ready is False


def test_missing_prediction_source_snapshot_is_reported_with_manifest(
    tmp_path: Path,
) -> None:
    raw_path, normalized_path, cases_path, predictions_path, links_path = (
        _write_multi_symbol_case_fixture(tmp_path, include_prediction_source_snapshot=False)
    )
    manifest_path = tmp_path / "source-manifest.json"
    manifest_path.write_text(json.dumps({"source_snapshot_hash": HASH}), encoding="utf-8")
    report = audit_forward_root(
        raw_path,
        normalized_path,
        completed_cases_path=cases_path,
        prediction_ledger_paths=(predictions_path,),
        outcome_link_ledger_paths=(links_path,),
        terminal_observed_at=START + timedelta(days=2),
        source_manifest_path=manifest_path,
    )
    assert report.prediction_source_identity_valid is False
    assert any(
        "lacks source_snapshot_hash" in item for item in report.prediction_identity_limitations
    )
    assert report.integrity_ready is False


def test_prediction_model_identity_is_bound_to_manifest(tmp_path: Path) -> None:
    raw_path, normalized_path, cases_path, predictions_path, links_path = (
        _write_multi_symbol_case_fixture(tmp_path)
    )
    source_manifest_path = tmp_path / "source-manifest.json"
    source_manifest_path.write_text(json.dumps({"source_snapshot_hash": HASH}), encoding="utf-8")
    prediction_manifest_path = tmp_path / "prediction-manifest.json"
    prediction_manifest_path.write_text(
        json.dumps(
            {
                "models": ["lightgbm"],
                "model_identity_hashes": {"lightgbm": HASH},
            }
        ),
        encoding="utf-8",
    )
    report = audit_forward_root(
        raw_path,
        normalized_path,
        completed_cases_path=cases_path,
        prediction_ledger_paths=(predictions_path,),
        prediction_manifest_paths=(prediction_manifest_path,),
        outcome_link_ledger_paths=(links_path,),
        terminal_observed_at=START + timedelta(days=2),
        source_manifest_path=source_manifest_path,
    )
    assert report.prediction_model_identity_valid is True
    assert report.prediction_identity_limitations == ()


def test_unlinked_prediction_blocks_admission_readiness(tmp_path: Path) -> None:
    raw_path, normalized_path, cases_path, predictions_path, _links_path = (
        _write_multi_symbol_case_fixture(tmp_path)
    )
    source_manifest_path = tmp_path / "source-manifest.json"
    source_manifest_path.write_text(json.dumps({"source_snapshot_hash": HASH}), encoding="utf-8")
    prediction_manifest_path = tmp_path / "prediction-manifest.json"
    prediction_manifest_path.write_text(
        json.dumps({"models": ["lightgbm"], "model_identity_hashes": {"lightgbm": HASH}}),
        encoding="utf-8",
    )
    report = audit_forward_root(
        raw_path,
        normalized_path,
        completed_cases_path=cases_path,
        prediction_ledger_paths=(predictions_path,),
        prediction_manifest_paths=(prediction_manifest_path,),
        source_manifest_path=source_manifest_path,
        terminal_observed_at=START + timedelta(days=2),
    )
    assert report.prediction_outcome_link_complete is False
    assert report.integrity_ready is False
    assert report.admission_evidence_ready is False


def test_duplicate_prediction_id_across_ledgers_fails_closed(tmp_path: Path) -> None:
    raw_path, normalized_path, cases_path, predictions_path, links_path = (
        _write_multi_symbol_case_fixture(tmp_path)
    )
    duplicate_predictions_path = tmp_path / "duplicate-predictions.jsonl"
    duplicate_predictions_path.write_bytes(predictions_path.read_bytes())

    with pytest.raises(IntegrityAuditError, match="duplicate prediction identity"):
        audit_forward_root(
            raw_path,
            normalized_path,
            completed_cases_path=cases_path,
            prediction_ledger_paths=(predictions_path, duplicate_predictions_path),
            outcome_link_ledger_paths=(links_path,),
            terminal_observed_at=START + timedelta(days=2),
        )


def test_input_spools_are_byte_identical_after_audit_and_overlay_is_separate(
    tmp_path: Path,
) -> None:
    report, raw_path, normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(),
    )
    raw_before = raw_path.read_bytes()
    normalized_before = normalized_path.read_bytes()
    report_path = tmp_path / "audit.json"
    report_json = report.model_dump(mode="json")
    report_path.write_text(json.dumps(report_json, sort_keys=True), encoding="utf-8")
    overlay = build_exclusion_overlay(report, report_sha256="c" * 64)
    overlay_path = tmp_path / "overlay.json"
    overlay_path.write_text(json.dumps(overlay.model_dump(mode="json")), encoding="utf-8")
    assert raw_path.read_bytes() == raw_before
    assert normalized_path.read_bytes() == normalized_before
    assert overlay_path != raw_path
    assert overlay.contaminated_case_ids == ()


def test_normalized_identity_matches_collector_golden_vector() -> None:
    row = _row()
    bar = parse_binance_klines(
        json.dumps([row]).encode(),
        symbol="BTCUSDT",
        collected_at=START + timedelta(minutes=6),
        source_snapshot_hash=HASH,
    )[0]
    expected = "d65c693e87027c814a35aba5d9dc12f9497ed3109cf3a19c73ec3290b10d6881"
    assert _hash_payload(_normalized_identity_payload(bar)) == expected
    assert bar.provenance.normalized_record_hash == expected


def test_normalized_raw_row_identity_mismatch_is_unresolved(tmp_path: Path) -> None:
    report, _raw_path, _normalized_path = _single_bar_audit(
        tmp_path,
        [_row(), _row()],
        canonical_row=_row(close="101"),
    )
    record = report.bar_records[0]
    assert record.normalized_raw_row_identity_valid is False
    assert record.classification == "UNRESOLVED"
    assert "raw-row identity" in record.classification_reason
    assert report.integrity_ready is False
    assert report.admission_evidence_ready is False


def test_duplicate_normalized_interval_is_invalid_even_when_content_matches(
    tmp_path: Path,
) -> None:
    row = _row()
    raw_path = tmp_path / "raw.jsonl"
    normalized_path = tmp_path / "normalized.jsonl"
    raw = ForwardRawSpool(raw_path)
    raw.append(
        _response(row, START + timedelta(minutes=6, seconds=1)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    raw.append(
        _response(row, START + timedelta(minutes=6, seconds=2)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    bar = parse_binance_klines(
        json.dumps([row]).encode(),
        symbol="BTCUSDT",
        collected_at=START + timedelta(minutes=6),
        source_snapshot_hash=HASH,
    )[0]
    normalized_path.write_text(
        bar.model_dump_json() + "\n" + bar.model_dump_json() + "\n",
        encoding="utf-8",
    )
    report = audit_forward_root(
        raw_path,
        normalized_path,
        terminal_observed_at=START + timedelta(minutes=10),
    )
    record = report.bar_records[0]
    assert record.normalized_duplicate is True
    assert record.normalized_conflict is False
    assert record.normalized_provenance_conflict is False
    assert record.classification == "UNRESOLVED"
    assert report.normalized_duplicate_count == 1
    assert report.normalized_input_valid is False


def test_normalized_duplicate_with_provenance_difference_is_distinguished(
    tmp_path: Path,
) -> None:
    row = _row()
    raw_path = tmp_path / "raw.jsonl"
    normalized_path = tmp_path / "normalized.jsonl"
    raw = ForwardRawSpool(raw_path)
    raw.append(
        _response(row, START + timedelta(minutes=6, seconds=1)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    raw.append(
        _response(row, START + timedelta(minutes=6, seconds=2)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    first = parse_binance_klines(
        json.dumps([row]).encode(),
        symbol="BTCUSDT",
        collected_at=START + timedelta(minutes=6),
        source_snapshot_hash=HASH,
    )[0]
    second = parse_binance_klines(
        json.dumps([row]).encode(),
        symbol="BTCUSDT",
        collected_at=START + timedelta(minutes=6, seconds=3),
        source_snapshot_hash=HASH,
    )[0]
    normalized_path.write_text(
        first.model_dump_json() + "\n" + second.model_dump_json() + "\n",
        encoding="utf-8",
    )
    report = audit_forward_root(
        raw_path,
        normalized_path,
        terminal_observed_at=START + timedelta(minutes=10),
    )
    record = report.bar_records[0]
    assert record.normalized_duplicate is True
    assert record.normalized_conflict is False
    assert record.normalized_provenance_conflict is True
    assert record.classification == "UNRESOLVED"


def test_same_response_duplicate_cannot_count_as_terminal_repeat(tmp_path: Path) -> None:
    row = _row()
    raw_path = tmp_path / "raw.jsonl"
    normalized_path = tmp_path / "normalized.jsonl"
    raw = ForwardRawSpool(raw_path)
    raw.append(
        HttpResponse(
            status_code=200,
            body=json.dumps([row, row]).encode(),
            fetched_at=START + timedelta(minutes=6),
            url=ENDPOINT,
        ),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    raw.append(
        _response(row, START + timedelta(minutes=6, seconds=1)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    bar = parse_binance_klines(
        json.dumps([row]).encode(),
        symbol="BTCUSDT",
        collected_at=START + timedelta(minutes=6),
        source_snapshot_hash=HASH,
    )[0]
    assert ForwardNormalizedBarSpool(normalized_path).append(bar)
    report = audit_forward_root(
        raw_path,
        normalized_path,
        terminal_observed_at=START + timedelta(minutes=10),
    )
    record = report.bar_records[0]
    assert record.duplicate_raw_rows_within_response is True
    assert record.terminal_distinct_response_count == 2
    assert record.classification == "UNRESOLVED"


def test_backwards_receipt_order_is_fail_closed(tmp_path: Path) -> None:
    row = _row()
    raw_path = tmp_path / "raw.jsonl"
    normalized_path = tmp_path / "normalized.jsonl"
    raw = ForwardRawSpool(raw_path)
    raw.append(
        _response(row, START + timedelta(minutes=6, seconds=2)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    raw.append(
        _response(row, START + timedelta(minutes=6, seconds=1)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    bar = parse_binance_klines(
        json.dumps([row]).encode(),
        symbol="BTCUSDT",
        collected_at=START + timedelta(minutes=6),
        source_snapshot_hash=HASH,
    )[0]
    assert ForwardNormalizedBarSpool(normalized_path).append(bar)
    report = audit_forward_root(
        raw_path,
        normalized_path,
        terminal_observed_at=START + timedelta(minutes=10),
    )
    assert report.raw_receipt_order_valid is False
    assert report.bar_records[0].classification == "UNRESOLVED"
    assert report.admission_minimum_met is False
