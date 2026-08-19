from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from advisorai.phase4.v3core_integrity import IntegrityAuditError
from scripts.qualify_phase4_v3core_forward import (
    TerminalWorkflowRefused,
    _assert_output_is_separate,
    _load_sealed_root,
    _paired_paths,
    _workflow_decision,
    run,
)


def _report(*, ready: bool) -> SimpleNamespace:
    return SimpleNamespace(admission_evidence_ready=ready)


def test_workflow_refuses_running_root_without_creating_output(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "status.json").write_text('{"state":"running"}\n')
    output = tmp_path / "workflow"

    with pytest.raises(TerminalWorkflowRefused, match="running"):
        run(
            run_directory=run_root,
            resource_root=tmp_path / "resource",
            preregistration=tmp_path / "prereg.json",
            phase3_gate_sha256="a" * 64,
            terminal_observed_at=datetime(2026, 8, 22, tzinfo=UTC),
            output_root=output,
        )

    assert not output.exists()


def test_workflow_refuses_unsupported_or_incomplete_terminal_state(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    status = run_root / "status.json"
    status.write_text('{"state":"target_reached","minimum_reached":false}\n')

    with pytest.raises(TerminalWorkflowRefused, match="frozen minimum"):
        _load_sealed_root(run_root)

    status.write_text('{"state":"operator_stopped"}\n')
    with pytest.raises(TerminalWorkflowRefused, match="terminal state"):
        _load_sealed_root(run_root)


def test_prediction_ledgers_and_manifests_must_be_paired() -> None:
    with pytest.raises(TerminalWorkflowRefused, match="paired"):
        _paired_paths((Path("predictions.jsonl"),), ())


def test_workflow_output_cannot_overlap_evidence(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()

    with pytest.raises(TerminalWorkflowRefused, match="separate"):
        _assert_output_is_separate(run_root / "audit", (run_root,))


def test_workflow_decision_fails_closed_before_materialization() -> None:
    status = {"state": "target_reached", "minimum_reached": True}
    resource = {"issues": []}

    decision, next_action, allowed = _workflow_decision(
        status=status,
        integrity_report=_report(ready=False),
        resource_report=resource,
    )

    assert decision == "INTEGRITY_NOT_READY"
    assert "do not score utility" in next_action
    assert allowed is False


def test_workflow_requires_clean_resource_audit() -> None:
    status = {"state": "target_reached", "minimum_reached": True}

    decision, _, allowed = _workflow_decision(
        status=status,
        integrity_report=_report(ready=True),
        resource_report={"issues": ["resource_errors_present"]},
    )

    assert decision == "RESOURCE_NOT_READY"
    assert allowed is False


def test_deadline_root_never_becomes_materialization_ready() -> None:
    decision, _, allowed = _workflow_decision(
        status={"state": "deadline_reached", "minimum_reached": False},
        integrity_report=_report(ready=True),
        resource_report={"issues": []},
    )

    assert decision == "SAMPLE_MINIMUM_NOT_REACHED"
    assert allowed is False


def test_workflow_allows_materialization_only_after_all_boundaries_pass() -> None:
    decision, next_action, allowed = _workflow_decision(
        status={"state": "target_reached", "minimum_reached": True},
        integrity_report=_report(ready=True),
        resource_report={"issues": []},
    )

    assert decision == "READY_FOR_MATERIALIZATION"
    assert next_action == "materialize the frozen Phase-4 input"
    assert allowed is True


def test_integrity_audit_error_writes_immutable_workflow_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "status.json").write_text(
        '{"state":"target_reached","minimum_reached":true}\n', encoding="utf-8"
    )
    resource_root = tmp_path / "resource"
    resource_root.mkdir()
    preregistration = tmp_path / "preregistration.json"
    preregistration.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "workflow"

    def refuse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise IntegrityAuditError("synthetic integrity failure")

    monkeypatch.setattr(
        "scripts.qualify_phase4_v3core_forward.audit_forward_root",
        refuse,
    )

    with pytest.raises(TerminalWorkflowRefused, match="sealed-root workflow refused"):
        run(
            run_directory=run_root,
            resource_root=resource_root,
            preregistration=preregistration,
            phase3_gate_sha256="a" * 64,
            terminal_observed_at=datetime(2026, 8, 22, tzinfo=UTC),
            output_root=output,
        )

    refusal = output / "workflow-refusal.json"
    assert refusal.is_file()
    payload = refusal.read_text(encoding="utf-8")
    assert '"error_class": "IntegrityAuditError"' in payload
    assert '"source_inputs_mutated": false' in payload
