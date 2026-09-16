import pytest

from webmcp_resilience.engine.runner import ScenarioRunner
from webmcp_resilience.models.scenario import Scenario


class Page:
    main_frame = object()
    def on(self, *_args): pass


class Adapter:
    calls = 0
    arguments = []
    def __init__(self, *_args): pass
    async def install(self): pass
    async def wait_for_tools(self, _required): return [{"name": "read", "annotations": {"readOnlyHint": True}}]
    async def get_tools(self): return [{"name": "read", "annotations": {"readOnlyHint": True}}]
    async def drain_lifecycle_events(self): return []
    async def invoke_tool(self, _name, _args, _id):
        type(self).calls += 1
        type(self).arguments.append(_args)
        return {"code": "ok"}
    async def get_state(self): return {}


class CancellationAdapter(Adapter):
    cancelled = []

    async def invoke_tool(self, name, args, invocation_id):
        await __import__("asyncio").sleep(10)

    async def cancel(self, invocation_id):
        type(self).cancelled.append(invocation_id)
        return True


@pytest.mark.asyncio
async def test_after_invoke_duplicate_runs_once_per_declared_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """One original invocation must terminate after exactly one duplicate."""
    monkeypatch.setattr("webmcp_resilience.engine.runner.WebMCPAdapter", Adapter)
    Adapter.calls = 0
    Adapter.arguments = []
    scenario = Scenario.model_validate({"name": "one-extra", "actors": {"a": [{"invoke": "read"}]},
                                        "faults": [{"type": "duplicate_invocation", "tool": "read", "at": "after_invoke"}]})
    trace = await ScenarioRunner(Page(), scenario).run()
    assert Adapter.calls == 2
    assert len([event for event in trace.events if event.type == "tool.invoke"]) == 2


@pytest.mark.asyncio
async def test_before_invoke_duplicate_reuses_resolved_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("webmcp_resilience.engine.runner.WebMCPAdapter", Adapter)
    Adapter.calls = 0
    Adapter.arguments = []
    scenario = Scenario.model_validate({
        "name": "resolved-duplicate",
        "actors": {"a": [{"invoke": "read", "args": {"value": "${state.value}"}}]},
        "state": {"tool": "read"},
        "faults": [{"type": "duplicate_invocation", "tool": "read", "at": "before_invoke"}],
    })
    # The fake state provider returns this resolved value.
    monkeypatch.setattr("webmcp_resilience.engine.runner.observe", lambda *_args: None)

    async def observed(*_args):
        return {"value": "resolved"}

    monkeypatch.setattr("webmcp_resilience.engine.runner.observe", observed)
    trace = await ScenarioRunner(Page(), scenario).run()
    assert Adapter.arguments == [{"value": "resolved"}, {"value": "resolved"}]
    invokes = [event for event in trace.events if event.type == "tool.invoke"]
    assert [event.data["args"] for event in invokes] == [{"value": "resolved"}, {"value": "resolved"}]


@pytest.mark.asyncio
async def test_cancel_targets_the_declared_tool_when_two_invocations_are_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    monkeypatch.setattr("webmcp_resilience.engine.runner.WebMCPAdapter", CancellationAdapter)
    CancellationAdapter.cancelled = []
    scenario = Scenario.model_validate({"name": "targeted-cancel", "actors": {"agent": [{"cancel": "slow-b"}]}})
    runner = ScenarioRunner(Page(), scenario, allow_mutations=True)
    first = asyncio.create_task(asyncio.sleep(10))
    second = asyncio.create_task(asyncio.sleep(10))
    runner.invocations = {"first": first, "second": second}
    runner.invocation_names = {"first": "slow-a", "second": "slow-b"}
    runner.adapter.cancel = CancellationAdapter.cancel.__get__(runner.adapter, type(runner.adapter))
    await runner._execute("agent", scenario.actors["agent"][0])
    assert CancellationAdapter.cancelled == ["second"]
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_adversarial_cancellation_reliably_targets_each_matching_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stress the public cancel action across fresh adversarial-style schedules."""
    import asyncio

    monkeypatch.setattr("webmcp_resilience.engine.runner.WebMCPAdapter", CancellationAdapter)
    CancellationAdapter.cancelled = []
    scenario = Scenario.model_validate({"name": "cancel-stress", "actors": {"agent": [{"cancel": "slow"}]}})
    for index in range(25):
        runner = ScenarioRunner(Page(), scenario, allow_mutations=True)
        task = asyncio.create_task(asyncio.sleep(10))
        invocation_id = f"slow-{index}"
        runner.invocations = {invocation_id: task}
        runner.invocation_names = {invocation_id: "slow"}
        runner.adapter.cancel = CancellationAdapter.cancel.__get__(runner.adapter, type(runner.adapter))
        await runner._execute("agent", scenario.actors["agent"][0])
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
    assert CancellationAdapter.cancelled == [f"slow-{index}" for index in range(25)]
