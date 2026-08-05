import pytest

from advisorai.orchestration import HamiltonRuntime, PrefectRuntime, PydanticRuntime


@pytest.mark.parametrize("runtime", (PydanticRuntime(), PrefectRuntime(), HamiltonRuntime()))
def test_optional_canonical_runtimes_do_not_silently_fallback(runtime):
    if runtime.status.production_admitted:
        return
    with pytest.raises(RuntimeError, match="quarantined"):
        if isinstance(runtime, PrefectRuntime):
            runtime.run_flow(lambda: None)
        elif isinstance(runtime, HamiltonRuntime):
            runtime.compute_features(lambda values: values, {})
        else:
            runtime.run_typed_agent(lambda values: values, {})
