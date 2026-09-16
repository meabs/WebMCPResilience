import pytest
import asyncio

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


def test_accepts_final_invariants_and_per_invocation_timeout() -> None:
    scenario = Scenario.model_validate({
        "name": "final-state", "actors": {"agent": [{"invoke": "save", "timeout_ms": 50}]},
        "final_invariants": ["booking.confirmed == true"],
    })
    assert scenario.actors["agent"][0].timeout_ms == 50
    assert scenario.final_invariants == ["booking.confirmed == true"]


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


@pytest.mark.asyncio
async def test_final_invariants_are_checked_only_at_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    class Adapter:
        async def get_state(self):
            return {"booking": {"complete": True}}

    scenario = Scenario.model_validate({
        "name": "final-state", "actors": {"agent": [{"action": "wait", "value": "0"}]},
        "final_invariants": ["booking.complete == true"],
    })
    runner = ScenarioRunner(_Page(), scenario)  # type: ignore[arg-type]
    runner.adapter = Adapter()  # type: ignore[assignment]
    await runner._check_invariants("agent")
    assert not runner.recorder.run.events
    await runner._check_invariants("system", final=True)
    assert runner.recorder.run.events[-1].data["phase"] == "final"


@pytest.mark.asyncio
async def test_normal_invocation_timeout_aborts_the_browser_call(monkeypatch: pytest.MonkeyPatch) -> None:
    class Adapter:
        cancelled: list[str] = []

        async def invoke_tool(self, *_args):
            await asyncio.sleep(1)

        async def cancel(self, invocation_id: str):
            self.cancelled.append(invocation_id)

    scenario = Scenario.model_validate({"name": "timeout", "actors": {"agent": [{"invoke": "read", "timeout_ms": 1}]}})
    runner = ScenarioRunner(_Page(), scenario, invoke_timeout_ms=None)  # type: ignore[arg-type]
    runner.adapter = Adapter()  # type: ignore[assignment]
    runner.invocation_descriptors["one"] = {}
    runner.invocation_timeouts["one"] = 1
    with pytest.raises(TimeoutError):
        await runner._invoke("agent", "one", "read", {})
    assert runner.adapter.cancelled == ["one"]
    event = runner.recorder.run.events[-1]
    assert event.data["error_code"] == "tool_invoke_timeout"


@pytest.mark.asyncio
async def test_ambiguous_selector_explains_how_to_fix_it() -> None:
    class Locator:
        async def count(self): return 4

    class Page(_Page):
        def locator(self, _selector): return Locator()

    scenario = Scenario.model_validate({"name": "ambiguous", "actors": {"human": [{"action": "click", "selector": "button.btn-size"}]}})
    runner = ScenarioRunner(Page(), scenario)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="matched 4 elements.*nth-of-type"):
        await runner._ui("human", scenario.actors["human"][0])
