import asyncio

import pytest

from webmcp_resilience.engine.runner import ScenarioRunner
from webmcp_resilience.models.scenario import Fault, Scenario


class Page:
    main_frame = object()

    def on(self, *_args: object) -> None:
        pass


class UnregisterAdapter:
    def __init__(self, outcome: dict[str, object]) -> None:
        self.outcome = outcome
        self.unregistered: list[tuple[str, dict[str, object]]] = []

    async def install(self) -> None:
        pass

    async def wait_for_tools(self, _required: set[str]) -> list[dict[str, object]]:
        return [{"name": "slow", "annotations": {"readOnlyHint": True}}]

    async def get_tools(self) -> list[dict[str, object]]:
        return [{"name": "slow", "annotations": {"readOnlyHint": True}}]

    async def drain_lifecycle_events(self) -> list[dict[str, object]]:
        return []

    async def start_tool(self, *_args: object) -> None:
        pass

    async def await_started_tool(self, _invocation_id: str) -> dict[str, str]:
        await asyncio.sleep(0.01)
        return {"code": "OK"}

    async def unregister_tool(
        self, name: str, descriptor: dict[str, object]
    ) -> dict[str, object]:
        self.unregistered.append((name, descriptor))
        return self.outcome


@pytest.mark.asyncio
async def test_unregister_during_invoke_completes_an_inflight_call() -> None:
    scenario = Scenario.model_validate({
        "name": "unregister",
        "actors": {"agent": [{"invoke": "slow"}]},
        "faults": [{"type": "unregister_during_invoke", "tool": "slow"}],
    })
    runner = ScenarioRunner(Page(), scenario)  # type: ignore[arg-type]
    adapter = UnregisterAdapter({"applied": True, "chrome_major": 153})
    runner.adapter = adapter  # type: ignore[assignment]

    trace = await runner.run()

    assert adapter.unregistered == [("slow", {"name": "slow", "annotations": {"readOnlyHint": True}})]
    assert any(
        event.type == "fault.injected"
        and event.name == "unregister_during_invoke"
        and event.data["chrome_major"] == 153
        for event in trace.events
    )
    assert any(event.type == "tool.result" and event.data["result"] == {"code": "OK"} for event in trace.events)


@pytest.mark.asyncio
async def test_unregister_during_invoke_skips_without_failing_when_unsupported() -> None:
    scenario = Scenario.model_validate({
        "name": "unregister-skipped",
        "actors": {"agent": [{"invoke": "slow"}]},
        "faults": [{"type": "unregister_during_invoke", "tool": "slow"}],
    })
    runner = ScenarioRunner(Page(), scenario)  # type: ignore[arg-type]
    runner.adapter = UnregisterAdapter({  # type: ignore[assignment]
        "applied": False,
        "reason": "unregister_during_invoke requires Chrome 153+; detected 152",
    })

    trace = await runner.run()

    skipped = next(
        event
        for event in trace.events
        if event.type == "fault.skipped" and event.name == "unregister_during_invoke"
    )
    assert "Chrome 153+" in skipped.data["reason"]
    assert any(event.type == "tool.result" for event in trace.events)


def test_unregister_during_invoke_only_supports_before_invoke() -> None:
    assert Fault(type="unregister_during_invoke").at == "before_invoke"
    with pytest.raises(ValueError, match="unsupported"):
        Fault(type="unregister_during_invoke", at="after_invoke")
