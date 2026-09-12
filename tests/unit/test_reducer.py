from webmcp_resilience.engine.reducer import reduce_failure
from webmcp_resilience.models.scenario import Scenario


async def test_removes_irrelevant_declared_actions() -> None:
    scenario = Scenario.model_validate({"name": "reduce", "actors": {"agent": [
        {"invoke": "necessary"}, {"invoke": "irrelevant"}], "human": [{"action": "wait", "value": "1"}]}})

    async def reproduces(candidate: Scenario) -> bool:
        return any(action.invoke == "necessary" for action in candidate.actors["agent"])

    reduced = await reduce_failure(scenario, reproduces)
    assert [action.invoke for action in reduced.actors["agent"]] == ["necessary"]
    assert reduced.actors["human"] == []
