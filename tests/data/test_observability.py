import sqlite3

from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.observability import (
    Incident,
    IncidentLedger,
    IncidentSeverity,
    StructuredTrace,
    TraceStore,
)


def test_structured_trace_is_persisted_locally_with_config_version(tmp_path):
    database = tmp_path / "state" / "traces.sqlite"
    trace = StructuredTrace(
        component="resource-governor",
        event="lease_rejected",
        config_hash="a" * 64,
        fields={"reason": "headroom"},
    )
    TraceStore(database).write(trace)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT component, event, config_hash, fields_json FROM structured_traces"
        ).fetchone()
    assert row == ("resource-governor", "lease_rejected", "a" * 64, '{"reason":"headroom"}')
    recent = TraceStore(database).recent(component="resource-governor")
    assert recent[0].fields["reason"] == "headroom"


def test_structured_trace_id_is_immutable(tmp_path):
    database = tmp_path / "state" / "immutable-traces.sqlite"
    trace = StructuredTrace(component="risk", event="checked", fields={"ok": True})
    store = TraceStore(database)
    store.write(trace)

    assert store.write(trace) == trace
    try:
        store.write(trace.model_copy(update={"fields": {"ok": False}}))
    except ValueError as exc:
        assert str(exc) == "trace ID is immutable"
    else:
        raise AssertionError("trace rewrites must be rejected")


def test_incident_ledger_rebuilds_open_and_closed_postmortems(tmp_path, timestamp):
    ledgers = SqliteLedgers(tmp_path / "incidents.sqlite")
    incident = Incident(
        severity=IncidentSeverity.HIGH,
        owner="operator",
        summary="venue outage",
        runbook="cancel and reconcile",
        containment="kill switch",
        opened_at=timestamp,
    )
    store = IncidentLedger(ledgers)
    store.record(incident)
    closed = incident.model_copy(
        update={
            "root_cause": "provider outage",
            "corrective_test": "outage fixture",
            "rollback_link": "runbook://paper",
            "reconciliation": "verified",
            "closed_at": timestamp,
        }
    )
    store.record(closed)
    assert store.all() == (closed,)
    assert (
        sum(
            event.event_type == "incident_recorded"
            for event in ledgers.events(LedgerNamespace.INCIDENT)
        )
        == 2
    )


def test_incident_ledger_close_requires_postmortem_links(tmp_path, timestamp):
    ledgers = SqliteLedgers(tmp_path / "incident-close.sqlite")
    incident = Incident(
        severity=IncidentSeverity.MEDIUM,
        owner="operator",
        summary="stale data",
        runbook="refresh source",
        containment="hold decisions",
        opened_at=timestamp,
    )
    store = IncidentLedger(ledgers)
    store.record(incident)
    closed = store.close(
        incident.incident_id,
        root_cause="source lag",
        corrective_test="stale-source fixture",
        rollback_link="runbook://refresh",
        timeline_entry="source recovered",
        closed_at=timestamp,
    )
    assert closed.closed_at == timestamp
    assert closed.timeline == ("source recovered",)
    assert closed.reconciliation == "verified"
