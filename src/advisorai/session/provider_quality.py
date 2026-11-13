"""Snapshot-bound outcome quality from supervised provider transport measurements."""

from __future__ import annotations

import hashlib
import json
import math
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from threading import Lock
from uuid import NAMESPACE_URL, UUID, uuid5

from advisorai.contracts import Fill, Snapshot
from advisorai.execution import RiskMarketState
from advisorai.integrations.http import HttpRequestMetrics, SafeHttpClient
from advisorai.learning import PaperDecisionRecord

from .outcomes import PaperOutcomeQuality


@dataclass
class ProviderOutcomeQualityProvider:
    """Measure provider request latency/failures and bind them to one PIT snapshot.

    The callback consumes cumulative counters from a credential-safe HTTP client,
    differences them from the previous outcome window, and stores only aggregate
    counts and a content hash in outcome provenance. Request paths, headers,
    bodies, and credentials are never retained.
    """

    provider_identity: str
    client: SafeHttpClient
    _last_metrics: HttpRequestMetrics = field(init=False, repr=False)
    _quality_by_snapshot: OrderedDict[UUID, PaperOutcomeQuality] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.provider_identity = self.provider_identity.strip().lower()
        if self.provider_identity not in {
            "binance_spot_testnet",
            "coinbase_exchange_sandbox",
        }:
            raise ValueError("provider quality requires an admitted sandbox identity")
        if not isinstance(self.client, SafeHttpClient):
            raise TypeError("provider quality requires the supervised SafeHttpClient")
        self._last_metrics = self.client.request_metrics

    def __call__(
        self,
        *,
        decision: PaperDecisionRecord,
        as_of: datetime,
        snapshot: Snapshot,
        market: RiskMarketState,
        fills: tuple[Fill, ...],
    ) -> PaperOutcomeQuality:
        del decision, market, fills
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("provider quality cutoff must include a timezone")
        cutoff = as_of.astimezone(UTC)
        if snapshot.as_of.astimezone(UTC) != cutoff:
            raise ValueError("provider quality snapshot differs from the outcome cutoff")
        with self._lock:
            cached = self._quality_by_snapshot.get(snapshot.artifact_id)
            if cached is not None:
                if cached.as_of != cutoff:
                    raise ValueError("provider quality snapshot identity was reused at another cutoff")
                return cached
            current = self.client.request_metrics
            attempts = current.attempt_count - self._last_metrics.attempt_count
            failures = current.failed_count - self._last_metrics.failed_count
            elapsed_ms = current.latency_ms_total - self._last_metrics.latency_ms_total
            if attempts <= 0:
                raise ValueError("provider quality requires current measured request samples")
            if failures < 0 or failures > attempts or elapsed_ms < 0:
                raise ValueError("provider HTTP counters moved backwards or became inconsistent")
            self._last_metrics = current

            state = snapshot.data_quality_state.strip().lower()
            data_reliability = Decimal("1") if state in {"validated", "gold"} else Decimal("0")
            failure_rate = Decimal(failures) / Decimal(attempts)
            latency_ms = math.ceil(elapsed_ms / attempts)
            source = f"provider-http-metrics/{self.provider_identity}/v1"
            evidence_payload = {
                "provider": self.provider_identity,
                "source": source,
                "snapshot_id": str(snapshot.artifact_id),
                "as_of": cutoff.isoformat(),
                "data_quality_state": state,
                "attempt_count": attempts,
                "failed_count": failures,
                "latency_ms_total": elapsed_ms,
            }
            measurement_hash = hashlib.sha256(
                json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            quality = PaperOutcomeQuality(
                data_reliability=data_reliability,
                latency_ms=latency_ms,
                failure_rate=failure_rate,
                evidence_id=uuid5(
                    NAMESPACE_URL,
                    f"advisorai-v3/provider-quality/{self.provider_identity}/{measurement_hash}",
                ),
                source=source,
                snapshot_id=snapshot.artifact_id,
                as_of=cutoff,
                sample_count=attempts,
                failed_count=failures,
                measurement_hash=measurement_hash,
            )
            self._quality_by_snapshot[snapshot.artifact_id] = quality
            while len(self._quality_by_snapshot) > 64:
                self._quality_by_snapshot.popitem(last=False)
            return quality


__all__ = ["ProviderOutcomeQualityProvider"]
