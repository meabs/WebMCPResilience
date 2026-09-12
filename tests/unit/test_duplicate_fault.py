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
