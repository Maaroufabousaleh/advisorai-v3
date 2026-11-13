"""Decision Model port: typed proposals, ABSTAIN behavior, no authority."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from advisorai.decision import (
    DecisionModelArtifact,
    DecisionModelInput,
    DecisionProposal,
    DecisionStance,
    ReferenceDecisionAdapter,
)


def _artifact(snapshot_id=None):
    return DecisionModelArtifact(
        model_name="reference",
        adapter_name="reference-v1",
        code_hash="a" * 64,
        config_hash="b" * 64,
        snapshot_id=snapshot_id or uuid4(),
    )


def test_gate_failure_produces_abstain():
    adapter = ReferenceDecisionAdapter(_artifact())
    mission_id, snapshot_id = uuid4(), uuid4()
    now = datetime(2026, 8, 5, 15, tzinfo=UTC)
    proposal = adapter.propose(
        DecisionModelInput(
            mission_id=mission_id,
            snapshot_id=snapshot_id,
            evidence_ids=(),
            consensus="evidence_gate_not_passed",
            missing_evidence=("source_families:1<2",),
            gate_passed=False,
        ),
        now=now,
    )
    assert proposal.stance is DecisionStance.ABSTAIN
    assert proposal.abstained is True
    assert proposal.calibrated_confidence == Decimal("0")
    assert proposal.fallback_used is True


def test_gate_pass_produces_typed_flat_proposal():
    adapter = ReferenceDecisionAdapter(_artifact())
    mission_id, snapshot_id = uuid4(), uuid4()
    evidence = uuid4()
    proposal = adapter.propose(
        DecisionModelInput(
            mission_id=mission_id,
            snapshot_id=snapshot_id,
            evidence_ids=(evidence,),
            consensus="evidence_gate_passed",
            gate_passed=True,
        ),
        now=datetime(2026, 8, 5, 15, tzinfo=UTC),
    )
    assert proposal.abstained is False
    assert proposal.evidence_ids == (evidence,)
    assert proposal.expires_at > datetime(2026, 8, 5, 15, tzinfo=UTC)


def test_abstain_requires_zero_confidence():
    with pytest.raises(ValidationError):
        DecisionProposal(
            mission_id=uuid4(),
            snapshot_id=uuid4(),
            model_artifact_id=uuid4(),
            stance=DecisionStance.ABSTAIN,
            calibrated_confidence=Decimal("0.5"),
            score=Decimal("0"),
            horizon="1h",
            uncertainty=Decimal("1"),
            expires_at=datetime(2026, 8, 5, 16, tzinfo=UTC),
            abstained=True,
        )


def test_probabilities_must_sum_to_one():
    with pytest.raises(ValidationError, match="sum to one"):
        DecisionProposal(
            mission_id=uuid4(),
            snapshot_id=uuid4(),
            model_artifact_id=uuid4(),
            stance=DecisionStance.LONG,
            probabilities={"long": Decimal("0.9"), "short": Decimal("0.5")},
            calibrated_confidence=Decimal("0.5"),
            score=Decimal("1"),
            horizon="1h",
            uncertainty=Decimal("0.5"),
            evidence_ids=(uuid4(),),
            expires_at=datetime(2026, 8, 5, 16, tzinfo=UTC),
        )
