from webmcp_resilience.engine.explorer import schedules
from webmcp_resilience.models.scenario import Scenario


def test_explores_declared_simultaneous_actions_only() -> None:
    scenario = Scenario.model_validate({
        "name": "live-app-actions",
        "actors": {
            "agent": [{"at": "0ms", "invoke": "first_real_tool"}],
            "human": [{"at": "0ms", "action": "click", "selector": "#real-control"}],
        },
    })
    variants = schedules(scenario)
    assert len(variants) == 2
    assert {item[1] for item in variants[0]} == {"agent", "human"}


def test_large_simultaneous_group_is_lazily_bounded() -> None:
    scenario = Scenario.model_validate({
        "name": "large",
        "actors": {f"actor-{index}": [{"at": "0ms", "action": "wait", "value": "0"}] for index in range(20)},
    })
    variants = schedules(scenario, limit=12, explore_yields=False)
    assert len(variants) == 12
    assert variants.examined <= 12


def test_same_actor_order_is_preserved() -> None:
    scenario = Scenario.model_validate({
        "name": "same-actor",
        "actors": {"agent": [
            {"at": "0ms", "action": "wait", "value": "first"},
            {"at": "0ms", "action": "wait", "value": "second"},
        ], "human": [{"at": "0ms", "action": "wait", "value": "human"}]},
    })
    variants = schedules(scenario, limit=10, explore_yields=False)
    assert len(variants) == 3
    for variant in variants:
        assert [item[2].value for item in variant if item[1] == "agent"] == ["first", "second"]
