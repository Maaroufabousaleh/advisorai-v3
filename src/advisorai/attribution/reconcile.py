"""Exact-enough attribution reconciliation with incident escalation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from advisorai.contracts import Attribution
from advisorai.observability import Incident, IncidentLedger, IncidentSeverity


class AttributionIncident(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AttributionReconciler:
    tolerance: Decimal = Decimal("0.01")
    incident_ledger: IncidentLedger | None = None

    def __post_init__(self) -> None:
        if not self.tolerance.is_finite() or self.tolerance < 0:
            raise ValueError("attribution tolerance cannot be negative")

    def reconcile(
        self,
        *,
        reconciliation_id: UUID,
        total_pnl: Decimal,
        data_forecast: Decimal,
        allocation_selection: Decimal,
        risk_overlay: Decimal,
        execution_financing: Decimal,
        regime_capacity: Decimal,
        currency: str,
    ) -> Attribution:
        if not currency.strip():
            raise ValueError("attribution currency is required")
        currency = currency.strip().upper()
        components = (
            total_pnl,
            data_forecast,
            allocation_selection,
            risk_overlay,
            execution_financing,
            regime_capacity,
        )
        if any(not value.is_finite() for value in components):
            raise ValueError("attribution values must be finite")
        residual = total_pnl - (
            data_forecast
            + allocation_selection
            + risk_overlay
            + execution_financing
            + regime_capacity
        )
        if abs(residual) > self.tolerance:
            if self.incident_ledger is not None:
                self.incident_ledger.record(
                    Incident(
                        incident_id=uuid5(
                            NAMESPACE_URL, f"advisorai-v3/attribution/{reconciliation_id}"
                        ),
                        severity=IncidentSeverity.HIGH,
                        owner="attribution-reconciler",
                        summary=f"unexplained attribution residual {residual} {currency}",
                        runbook="freeze promotion, inspect ledger and reconcile P&L",
                        evidence_ids=(reconciliation_id,),
                        containment="hold new promotion until attribution is reconciled",
                    )
                )
            raise AttributionIncident(f"unexplained attribution residual {residual} {currency}")
        return Attribution(
            reconciliation_id=reconciliation_id,
            data_forecast=data_forecast,
            allocation_selection=allocation_selection,
            risk_overlay=risk_overlay,
            execution_financing=execution_financing,
            regime_capacity=regime_capacity,
            unexplained_residual=residual,
            currency=currency,
            total_pnl=total_pnl,
        )
