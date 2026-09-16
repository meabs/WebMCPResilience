from webmcp_resilience.engine.reducer import FailureSignature, ReductionBudget, reduce_failure, reduce_failure_report
from webmcp_resilience.models.scenario import Scenario


async def test_removes_irrelevant_declared_actions() -> None:
    scenario = Scenario.model_validate({"name": "reduce", "actors": {"agent": [
        {"invoke": "necessary"}, {"invoke": "irrelevant"}], "human": [{"action": "wait", "value": "1"}]}})

    async def reproduces(candidate: Scenario) -> bool:
        return any(action.invoke == "necessary" for action in candidate.actors["agent"])

    reduced = await reduce_failure(scenario, reproduces)
    assert [action.invoke for action in reduced.actors["agent"]] == ["necessary"]
    assert reduced.actors["human"] == []


async def test_reducer_rejects_a_different_failure_signature() -> None:
    scenario = Scenario.model_validate({"name": "signatures", "actors": {"agent": [
        {"invoke": "necessary"}, {"invoke": "changes-failure"},
    ]}})

    async def reproduces(candidate: Scenario) -> FailureSignature | None:
        if any(action.invoke == "changes-failure" for action in candidate.actors["agent"]):
            return FailureSignature("invariant", invariant="other.expression")
        if any(action.invoke == "necessary" for action in candidate.actors["agent"]):
            return FailureSignature("invariant", invariant="target.expression")
        return None

    report = await reduce_failure_report(
        scenario, reproduces,
        target_signature=FailureSignature("invariant", invariant="target.expression"),
        budget=ReductionBudget(max_attempts=10, time_budget_ms=1000),
    )
    assert [action.invoke for action in report.scenario.actors["agent"]] == ["necessary"]
    assert report.rejected_signatures == [FailureSignature("invariant", invariant="other.expression")]
