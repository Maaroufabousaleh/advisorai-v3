#!/usr/bin/env python3
"""Run a bounded, read-only Phase-3 V3-Core source qualification.

The command is deliberately opt-in because it performs public HTTPS reads. It
uses the existing V3-Core collector factory, persists each response through
the raw-first spool, replays successful parses from those bytes, and records
quality findings without persisting response bodies in the summary report.

The current native venue is pinned to the reviewed Coinbase Exchange Sandbox.
Its product catalogue is expected to be authoritative: a missing ETH-USD
product is recorded as an external failure and never substituted with another
venue or a production Coinbase endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from advisorai.collectors import DataQualityMonitor, HttpResponse, RawHttpSpool
from advisorai.config import SecretSettings
from advisorai.contracts import AssetClass, InstrumentIdentity, PointInTimeObservation
from advisorai.integrations import (
    COINBASE_EXCHANGE_PRODUCTION_HOST,
    COINBASE_EXCHANGE_SANDBOX_BASE_URL,
    COINBASE_EXCHANGE_SANDBOX_HOST,
    SourceEndpoint,
    build_v3_core_collectors,
)

SCHEMA = "advisorai.phase3.v3-core-source-qualification.v1"
DEFAULT_DERIBIT_URL = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
DEFAULT_RSS_URL = "https://www.sec.gov/news/pressreleases.rss"
DEFAULT_GDELT_URL = (
    "https://api.gdeltproject.org/api/v2/doc/doc?"
    "query=bitcoin&mode=artlist&format=json&maxrecords=5"
)


@dataclass(frozen=True, slots=True)
class Operation:
    name: str
    source: str
    url: str
    instrument: InstrumentIdentity
    max_age_seconds: int
    expected_interval_seconds: int | None = None


def _sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _write_immutable_json(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable evidence differs: {path}")
        return
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _instrument(
    canonical_id: str,
    *,
    venue: str | None = None,
    venue_symbol: str | None = None,
    base_asset: str | None = None,
    quote_asset: str | None = None,
) -> InstrumentIdentity:
    return InstrumentIdentity(
        canonical_id=canonical_id,
        asset_class=AssetClass.CRYPTO,
        venue=venue,
        venue_symbol=venue_symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
    )


def _endpoint(url: str) -> SourceEndpoint:
    parsed = urlsplit(url)
    if parsed.path not in {"", "/"}:
        raise ValueError("source base endpoints must not contain a path")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("source base endpoints cannot contain query, fragment, or credentials")
    if parsed.hostname is None:
        raise ValueError("source endpoint must contain a hostname")
    return SourceEndpoint(url=url.rstrip("/"), allowed_host=parsed.hostname)


def _public_endpoint(url: str) -> SourceEndpoint:
    """Build a reviewed public endpoint while rejecting secret-like queries."""

    parsed = urlsplit(url)
    if parsed.username or parsed.password:
        raise ValueError("source endpoints cannot contain credentials")
    sensitive_query_names = {
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "password",
        "passphrase",
        "secret",
        "token",
    }
    if any(
        any(token in key.lower() for token in sensitive_query_names)
        for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        raise ValueError("public source endpoints cannot contain secret-like query parameters")
    if parsed.hostname is None:
        raise ValueError("source endpoint must contain a hostname")
    return SourceEndpoint(url=url, allowed_host=parsed.hostname)


def _safe_error(exc: Exception) -> dict[str, object]:
    status_code = getattr(exc, "status_code", None)
    return {
        "error_class": type(exc).__name__,
        "status_code": status_code if isinstance(status_code, int) else None,
    }


def _failure_classification(
    error: dict[str, object] | None,
    records: tuple[Any, ...],
    *,
    observation_count: int,
    quality_passed: bool = True,
    quality_finding_codes: tuple[str, ...] = (),
    replay_match: bool | None = True,
    duplicate_append_rejected: bool | None = True,
) -> dict[str, object]:
    """Classify a failed source pass without guessing at provider internals.

    An HTTP response is external evidence.  A parser/quality failure after a
    successful response is kept separate so provider availability is not
    confused with an AdvisorAI implementation or data-integrity problem.  All
    failed operations remain fail-closed because no observation is admitted.
    """

    if (
        error is None
        and observation_count > 0
        and quality_passed
        and replay_match is True
        and duplicate_append_rejected is True
    ):
        return {
            "category": "none",
            "implementation_failure": False,
            "data_integrity_failure": False,
            "external_provider_availability": False,
            "safe_fail_closed": False,
        }

    status_code = error.get("status_code") if error is not None else None
    if not isinstance(status_code, int):
        latest = records[-1] if records else None
        status_code = getattr(latest, "status_code", None)

    if isinstance(status_code, int):
        if status_code == 404:
            category = "external_provider_product_unavailable"
            provider_availability = False
        elif status_code in {408, 425, 429, 500, 502, 503, 504}:
            category = "external_provider_unavailable_or_rate_limited"
            provider_availability = True
        else:
            category = "external_provider_http_failure"
            provider_availability = True
        integrity_failure = False
    elif any(
        code in {"stale", "future_event", "clock_confidence"} for code in quality_finding_codes
    ):
        category = "external_provider_stale_or_clock_uncertain"
        provider_availability = True
        integrity_failure = False
    elif replay_match is False or duplicate_append_rejected is False:
        category = "data_integrity_or_replay_failure"
        provider_availability = False
        integrity_failure = True
    elif records:
        category = "data_integrity_or_schema_failure"
        provider_availability = False
        integrity_failure = True
    else:
        category = "no_observation_without_response"
        provider_availability = False
        integrity_failure = True

    classification = {
        "category": category,
        "implementation_failure": False,
        "data_integrity_failure": integrity_failure,
        "external_provider_availability": provider_availability,
        "safe_fail_closed": category != "none",
    }
    if isinstance(status_code, int):
        classification["status_code"] = status_code
    return classification


def _matching_records(spool: RawHttpSpool, url: str):
    return tuple(record for record in spool.read() if record.url == url)


def _observation_hashes(observations: tuple[PointInTimeObservation, ...]) -> tuple[str, ...]:
    return tuple(observation.canonical_hash() for observation in observations)


def _operation(
    operation: Operation,
    collector: Any,
    *,
    monitor: DataQualityMonitor,
    as_of: datetime | None = None,
) -> tuple[dict[str, object], tuple[PointInTimeObservation, ...]]:
    spool = getattr(collector, "raw_spool", None)
    if not isinstance(spool, RawHttpSpool):
        raise TypeError("V3-Core collectors must expose a RawHttpSpool for qualification")

    started = time.monotonic()
    observations: tuple[PointInTimeObservation, ...] = ()
    error: dict[str, object] | None = None
    try:
        observations = collector.fetch(operation.url, operation.instrument)
    except Exception as exc:  # the report intentionally records only the class/status
        error = _safe_error(exc)
    latency_ms = round((time.monotonic() - started) * 1000, 3)
    records = _matching_records(spool, operation.url)
    latest = records[-1] if records else None
    replay_match: bool | None = None
    duplicate_append_rejected: bool | None = None
    replay_hashes: tuple[str, ...] = ()
    if latest is not None:
        duplicate_append_rejected = not spool.append(
            HttpResponse(
                status_code=latest.status_code,
                body=latest.payload,
                fetched_at=latest.fetched_at,
                url=latest.url,
            )
        )
        if error is None:
            try:
                replayed = collector.parse(
                    latest.payload,
                    instrument=operation.instrument,
                    available_at=latest.fetched_at,
                )
                replay_hashes = _observation_hashes(replayed)
                replay_match = replay_hashes == _observation_hashes(observations)
            except Exception as exc:
                error = _safe_error(exc)

    quality_cutoff = as_of or datetime.now(UTC)
    quality = monitor.evaluate(
        dataset=operation.name,
        observations=observations,
        as_of=quality_cutoff,
        max_age_seconds=operation.max_age_seconds,
        expected_interval_seconds=operation.expected_interval_seconds,
    )
    timed_observations = tuple(
        (observation, observation.event_time)
        for observation in observations
        if observation.event_time is not None
    )
    clock_drift_seconds = None
    if operation.source in {"native_venue", "deribit"} and timed_observations:
        clock_drift_seconds = round(
            max(
                abs((event_time - observation.first_available_at).total_seconds())
                for observation, event_time in timed_observations
                if event_time is not None
            ),
            3,
        )
    passed = (
        error is None
        and bool(observations)
        and quality.passed
        and replay_match is True
        and duplicate_append_rejected is True
    )
    status = (
        "measured_pass" if passed else ("measured_failure" if error else "measured_quality_failure")
    )
    result: dict[str, object] = {
        "name": operation.name,
        "source": operation.source,
        "endpoint": operation.url,
        "instrument": operation.instrument.canonical_id,
        "status": status,
        "passed": passed,
        "latency_ms": latency_ms,
        "quality_cutoff": quality.as_of.isoformat(),
        "observation_count": len(observations),
        "observation_hashes": _observation_hashes(observations),
        "replay_observation_hashes": replay_hashes,
        "replay_match": replay_match,
        "duplicate_raw_append_rejected": duplicate_append_rejected,
        "clock_drift_seconds_max_abs": clock_drift_seconds,
        "sequence_check": "not_observable_from_rest_bootstrap",
        "schema_check": "parser_accepted" if error is None else "parser_or_transport_rejected",
        "failure_classification": _failure_classification(
            error,
            records,
            observation_count=len(observations),
            quality_passed=quality.passed,
            quality_finding_codes=tuple(finding.code for finding in quality.findings),
            replay_match=replay_match,
            duplicate_append_rejected=duplicate_append_rejected,
        ),
        "quality": quality.model_dump(mode="json"),
        "raw_responses": [
            {
                "status_code": record.status_code,
                "url": record.url,
                "fetched_at": record.fetched_at.isoformat(),
                "raw_sha256": record.raw_sha256,
                "payload_bytes": len(record.payload),
            }
            for record in records
        ],
    }
    if error is not None:
        result["error"] = error
    return result, observations


def run_evidence(
    output_root: Path,
    *,
    native_url: str = COINBASE_EXCHANGE_SANDBOX_BASE_URL,
    deribit_url: str = DEFAULT_DERIBIT_URL,
    rss_url: str = DEFAULT_RSS_URL,
    gdelt_url: str = DEFAULT_GDELT_URL,
) -> tuple[Path, dict[str, object], str]:
    """Run one real source pass and return its report path, payload, and digest."""

    native_endpoint = _endpoint(native_url)
    if native_endpoint.allowed_host == COINBASE_EXCHANGE_PRODUCTION_HOST:
        raise ValueError("production Coinbase endpoints are prohibited")
    if native_endpoint.allowed_host != COINBASE_EXCHANGE_SANDBOX_HOST:
        raise ValueError(
            "the current V3-Core native endpoint must be the reviewed Coinbase Sandbox"
        )
    deribit_endpoint = _public_endpoint(deribit_url)
    rss_endpoint = _public_endpoint(rss_url)
    gdelt_endpoint = _public_endpoint(gdelt_url)

    output_root = output_root.expanduser().resolve()
    run_id_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_id_base
    suffix = 1
    while (output_root / run_id).exists():
        suffix += 1
        run_id = f"{run_id_base}-{suffix}"
    run_directory = output_root / run_id
    settings = SecretSettings.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "coinbase_exchange_sandbox",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
        }
    )
    collectors = build_v3_core_collectors(
        settings=settings,
        native=native_endpoint,
        deribit=deribit_endpoint,
        rss=rss_endpoint,
        gdelt=gdelt_endpoint,
        raw_spool_dir=run_directory / "raw-http",
    )
    btc = _instrument(
        "crypto:BTC-USD:coinbase_exchange_sandbox:spot",
        venue="coinbase_exchange_sandbox",
        venue_symbol="BTC-USD",
        base_asset="BTC",
        quote_asset="USD",
    )
    context = _instrument("context:crypto-market")
    operations = (
        Operation(
            name="native_btc_usd_ticker",
            source="native_venue",
            url=f"{native_url.rstrip('/')}/products/BTC-USD/ticker",
            instrument=btc,
            max_age_seconds=900,
        ),
        Operation(
            name="native_eth_usd_ticker",
            source="native_venue",
            url=f"{native_url.rstrip('/')}/products/ETH-USD/ticker",
            instrument=_instrument(
                "crypto:ETH-USD:coinbase_exchange_sandbox:spot",
                venue="coinbase_exchange_sandbox",
                venue_symbol="ETH-USD",
                base_asset="ETH",
                quote_asset="USD",
            ),
            max_age_seconds=900,
        ),
        Operation(
            name="deribit_btc_index",
            source="deribit",
            url=deribit_url,
            instrument=btc,
            max_age_seconds=900,
        ),
        Operation(
            name="official_rss_press_releases",
            source="official_rss",
            url=rss_url,
            instrument=context,
            max_age_seconds=7 * 24 * 60 * 60,
        ),
        Operation(
            name="gdelt_bitcoin_articles",
            source="gdelt",
            url=gdelt_url,
            instrument=context,
            max_age_seconds=24 * 60 * 60,
        ),
    )
    monitor = DataQualityMonitor()
    operation_results: list[dict[str, object]] = []
    all_observations: list[PointInTimeObservation] = []
    for operation in operations:
        collector = getattr(
            collectors,
            {
                "native_venue": "native",
                "deribit": "deribit",
                "official_rss": "rss",
                "gdelt": "gdelt",
            }[operation.source],
        )
        result, observations = _operation(
            operation,
            collector,
            monitor=monitor,
        )
        operation_results.append(result)
        all_observations.extend(observations)

    measured_at = datetime.now(UTC)
    combined = monitor.evaluate(
        dataset="v3_core_combined",
        observations=tuple(all_observations),
        as_of=measured_at,
        max_age_seconds=7 * 24 * 60 * 60,
    )
    combined_finding_codes = tuple(sorted({finding.code for finding in combined.findings}))
    combined_error_codes = tuple(
        sorted({finding.code for finding in combined.findings if finding.severity == "error"})
    )
    clients = {
        name: getattr(getattr(collectors, name), "transport", None)
        for name in ("native", "deribit", "rss", "gdelt")
    }
    network_calls = sum(
        int(getattr(getattr(transport, "client", None), "request_count", 0))
        for transport in clients.values()
    )
    source_passed = all(bool(result["passed"]) for result in operation_results)
    report: dict[str, object] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "measured_at": measured_at.isoformat(),
        "runner_code_sha256": _sha256(Path(__file__).read_bytes()),
        "collector_code_sha256": _sha256(
            (
                Path(__file__).resolve().parents[1] / "src/advisorai/collectors/sources.py"
            ).read_bytes()
        ),
        "network_calls": network_calls,
        "venue_identity": "coinbase_exchange_sandbox",
        "venue_environment": "paper_testnet",
        "native_reviewed_host": COINBASE_EXCHANGE_SANDBOX_HOST,
        "native_endpoint": native_url,
        "sources": {
            "native_venue": native_endpoint.allowed_host,
            "deribit": deribit_endpoint.allowed_host,
            "official_rss": rss_endpoint.allowed_host,
            "gdelt": gdelt_endpoint.allowed_host,
        },
        "parser_versions": {
            name: getattr(collectors, name).descriptor.parser_version
            for name in ("native", "deribit", "rss", "gdelt")
        },
        "operations": operation_results,
        "combined_quality": combined.model_dump(mode="json"),
        "cross_source_disagreement": {
            "state": (
                "warning_present"
                if "cross_source_disagreement" in combined_finding_codes
                else "no_same_event_disagreement_observed"
            ),
            "finding_codes": combined_finding_codes,
            "error_codes": combined_error_codes,
            "origins": combined.origins,
            "source_families": combined.source_families,
        },
        "passed": source_passed,
        "gate_state": (
            "EXTERNALLY_MEASURED / QUALIFIED_FOR_SOURCE_SMOKE"
            if source_passed
            else "EXTERNALLY_MEASURED / PENDING_EXTERNAL_EVIDENCE"
        ),
        "admission_opened": False,
        "notes": (
            "REST bootstrap cannot attest WebSocket sequence gaps or a continuous freshness soak; "
            "those remain separate evidence requirements.",
            "The native ETH-USD operation is provider truth and is never substituted when absent.",
        ),
    }
    report_path = run_directory / "phase3-v3-core-source-qualification.json"
    _write_immutable_json(report_path, report)
    report_sha256 = _sha256(report_path.read_bytes())
    manifest = {
        "schema": f"{SCHEMA}.manifest",
        "run_id": run_id,
        "report": report_path.name,
        "evidence_sha256": report_sha256,
        "raw_spool_directory": "raw-http",
    }
    _write_immutable_json(run_directory / "evidence-manifest.json", manifest)
    latest_path = output_root / "latest.json"
    temporary = output_root / ".latest.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"schema": f"{SCHEMA}.latest", "run_id": run_id, "evidence_sha256": report_sha256},
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, latest_path)
    return report_path, report, report_sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="allow public HTTPS reads")
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase3/source-qualification"),
    )
    parser.add_argument("--native-url", default=COINBASE_EXCHANGE_SANDBOX_BASE_URL)
    parser.add_argument("--deribit-url", default=DEFAULT_DERIBIT_URL)
    parser.add_argument("--rss-url", default=DEFAULT_RSS_URL)
    parser.add_argument("--gdelt-url", default=DEFAULT_GDELT_URL)
    args = parser.parse_args()
    if not args.real:
        parser.error("network reads require explicit --real")
    path, report, report_sha256 = run_evidence(
        args.evidence_dir,
        native_url=args.native_url,
        deribit_url=args.deribit_url,
        rss_url=args.rss_url,
        gdelt_url=args.gdelt_url,
    )
    print(
        json.dumps(
            {
                "report": str(path),
                "evidence_sha256": report_sha256,
                "network_calls": report["network_calls"],
                "passed": report["passed"],
                "gate_state": report["gate_state"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
