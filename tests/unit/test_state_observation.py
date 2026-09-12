import pytest

from webmcp_resilience.commands import CommandAPI, CommandError, build_state_observation
from webmcp_resilience.config import Config
from webmcp_resilience.models.scenario import Scenario


def scenario_with_state(tool: str = "get_observable_state") -> Scenario:
    return Scenario.model_validate({
        "name": "state-source",
        "actors": {"agent": [{"invoke": "read_status"}]},
        "state": {"tool": tool},
    })


def test_state_observation_none_is_explicit() -> None:
    observation = build_state_observation(Config())
    assert observation.mode == "none"
    assert observation.configured_source is None
    assert observation.validation["status"] == "not_configured"


def test_state_script_is_the_invariant_observation_source() -> None:
    observation = build_state_observation(Config(state_script="window.__resilienceLab.getState()"))
    assert observation.mode == "state_script"
    assert observation.configured_source == "window.__resilienceLab.getState()"
    assert observation.state_script["source"] == observation.configured_source
    assert observation.scenario_state_tool["configured"] is False


def test_scenario_state_tool_requires_discovered_read_only_tool() -> None:
    scenario = scenario_with_state()
    valid = build_state_observation(
        Config(), scenario,
        tool_inventory=[{"name": "get_observable_state", "annotations": {"readOnlyHint": True}}],
    )
    assert valid.mode == "scenario_state_tool"
    assert valid.validation == {"status": "valid", "valid": True, "message": "Discovered state tool is explicitly read-only."}

    invalid = build_state_observation(
        Config(), scenario,
        tool_inventory=[{"name": "get_observable_state", "annotations": {"consequentialHint": True}}],
    )
    assert invalid.validation["valid"] is False
    assert "readOnlyHint: true" in invalid.validation["message"]


def test_invariant_without_state_script_fails_with_remediation() -> None:
    scenario = Scenario.model_validate({
        "name": "missing-state",
        "actors": {"agent": [{"invoke": "read_status"}]},
        "invariants": ["count >= 0"],
    })
    with pytest.raises(CommandError, match="configure state_script"):
        CommandAPI(Config()).validate(scenario=scenario, run_id="missing-state")
