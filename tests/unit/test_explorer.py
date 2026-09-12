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
