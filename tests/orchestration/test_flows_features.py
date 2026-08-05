import pytest

from advisorai.features import FeatureGraph, FeatureNode
from advisorai.flows import DurableFlow


def test_feature_graph_computes_dependencies_and_rejects_cycles():
    graph = FeatureGraph(
        (
            FeatureNode("x2", ("x",), lambda values: values["x"] * 2, "v1"),
            FeatureNode("x4", ("x2",), lambda values: values["x2"] * 2, "v1"),
        )
    )
    assert graph.compute(("x4",), {"x": 1}) == {"x4": 4}
    with pytest.raises(ValueError, match="cycle"):
        FeatureGraph(
            (
                FeatureNode("a", ("b",), lambda values: 1, "v1"),
                FeatureNode("b", ("a",), lambda values: 1, "v1"),
            )
        ).compute(("a",), {})
    with pytest.raises(ValueError, match="unique"):
        graph.compute((" x4 ", "x4"), {"x": 1})


def test_durable_flow_retries_without_owning_market_events(tmp_path):
    attempts = {"count": 0}

    def task():
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise RuntimeError("transient")
        return "ok"

    from advisorai.ledger import LedgerNamespace, SqliteLedgers

    ledgers = SqliteLedgers(tmp_path / "flow.sqlite")
    run, value = DurableFlow("backfill", max_retries=2, ledgers=ledgers).run(task)
    assert run.state.value == "succeeded"
    assert run.attempt == 2
    assert value == "ok"
    assert any(
        event.event_type == "flow_succeeded" for event in ledgers.events(LedgerNamespace.MISSION)
    )
