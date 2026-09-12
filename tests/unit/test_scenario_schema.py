import pytest

from webmcp_resilience.models.scenario import Fault, Scenario
from webmcp_resilience.engine.runner import ScenarioRunner


class _Page:
    def on(self, *_: object) -> None:
        pass


def test_accepts_documented_scenario_and_fault_shorthand() -> None:
    scenario = Scenario.model_validate({
        "scenario": "declared-run",
        "actors": {"agent": [{"invoke": "published_tool"}]},
        "faults": ["duplicate_invocation"],
    })
    assert scenario.name == "declared-run"
    assert scenario.faults[0].type == "duplicate_invocation"


def test_supported_default_and_non_default_fault_timing() -> None:
    assert Fault(type="latency").at == "before_invoke"
    assert Fault(type="latency", at="after_invoke").at == "after_invoke"


def test_unsupported_timed_fault_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        Fault(type="timeout", at="after_invoke")


@pytest.mark.asyncio
async def test_supported_non_default_timing_is_applied_and_recorded() -> None:
    scenario = Scenario.model_validate({"name": "timing", "actors": {"agent": [{"invoke": "read"}]}, "faults": [{"type": "latency", "tool": "read", "at": "after_invoke", "duration_ms": 1}]})
    runner = ScenarioRunner(_Page(), scenario)  # type: ignore[arg-type]
    await runner._apply_after_invoke_faults("agent", "read", {})
    assert runner.recorder.run.events[-1].name == "latency"
    assert runner.recorder.run.events[-1].data["at"] == "after_invoke"
