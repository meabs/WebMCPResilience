import asyncio
import json
from pathlib import Path

import yaml
import pytest

from webmcp_resilience.agent_control import AgentPolicy, LocalMCPControlAdapter
from webmcp_resilience.commands import CommandAPI
from webmcp_resilience.config import Config
from webmcp_resilience.console_commands import ConsoleCommandModule, replay_outcome
from webmcp_resilience.console_client import (
    RunHistory,
    ScenarioEditorModel,
    bundle_comparison,
    bundle_trace,
    event_state_diff,
    filter_events,
    load_bundle,
    safe_artifact_text,
    state_diff,
)
from webmcp_resilience.models.bundle import Artifact, Compatibility, RunBundle
from webmcp_resilience.models.trace import TraceEvent, TraceRun


def test_editor_declaration_is_unchanged_for_command_api_and_mcp(tmp_path: Path) -> None:
    model = ScenarioEditorModel([{
        "name": "read_status",
        "inputSchema": {"type": "object", "properties": {"verbose": {"type": "boolean"}}},
    }], {"actors": ["agent"], "actions": ["invoke"]})
    scenario_path = tmp_path / ".webmcp" / "scenarios" / "status.yaml"
    scenario_path.parent.mkdir(parents=True)
    scenario = model.build_scenario(name="status", actor="agent", at="0ms", tool="read_status", args={})
    scenario_path.write_text(model.yaml_text(scenario))

    api = CommandAPI(Config(base_url="http://localhost:3000"), output_dir=tmp_path / ".webmcp" / "runs")
    direct = api.validate(scenario_path)
    adapter = LocalMCPControlAdapter(api, AgentPolicy(tmp_path, "http://localhost:3000"))
    relative = ".webmcp/scenarios/status.yaml"
    through_mcp = asyncio.run(adapter.call("validate_scenario", {"scenario_id": relative}))

    assert yaml.safe_load(scenario_path.read_text()) == scenario
    assert through_mcp["bundle"]["scenario"] == direct.scenario
    assert model.cli_operations(scenario_path, tmp_path)["run"].startswith("webmcp run .webmcp/scenarios/status.yaml")
    assert model.mcp_operations(scenario_path, tmp_path)["run"]["tool"] == "run_scenario"


def test_trace_state_diff_and_bundle_browser_comparison_are_stable() -> None:
    trace = TraceRun(scenario="compare", events=[
        TraceEvent(timestamp_ms=0, actor="agent", type="state.observed", state_snapshot={"count": 1}),
        TraceEvent(timestamp_ms=1, actor="agent", type="tool.result", state_snapshot={"count": 2, "ready": True}),
    ])
    left = RunBundle(command="run", run_id="left", trace=trace, compatibility=Compatibility(browser="chromium", headless=True))
    right = RunBundle(command="run", run_id="right", trace=trace, compatibility=Compatibility(browser="firefox", headless=False))
    assert event_state_diff(trace.model_dump(mode="json")["events"], 1)["changed"]["count"] == {"before": 1, "after": 2}
    assert bundle_comparison(left, right)["browser_changed"] is True


def test_console_never_reads_sensitive_artifacts(tmp_path: Path) -> None:
    sensitive = tmp_path / "secret.png"
    sensitive.write_bytes(b"private screenshot")
    redacted = tmp_path / "failure.yaml"
    redacted.write_text("passed: false\n")
    bundle = RunBundle(command="run", run_id="safe", artifacts=[
        Artifact(kind="screenshot", path=str(sensitive), sensitivity="potentially_sensitive"),
        Artifact(kind="failure", path=str(redacted), redacted=True, sensitivity="redacted"),
    ])
    bundle_path = bundle.write(tmp_path / "bundle.json")
    assert safe_artifact_text(bundle_path, bundle, "screenshot") is None
    assert safe_artifact_text(bundle_path, bundle, "failure") == "passed: false\n"


def test_bundle_trace_redacts_imported_unsanitized_trace() -> None:
    bundle = RunBundle(command="run", trace=TraceRun(scenario="unsafe", events=[
        TraceEvent(timestamp_ms=1, actor="agent", type="tool.result", data={
            "result": {"api-key": "console-secret"},
            "url": "https://example.test/?signature=console-secret",
        })
    ]))
    rendered = str(bundle_trace(bundle))
    assert "console-secret" not in rendered


def test_bundle_loading_redacts_untrusted_imported_json(tmp_path: Path) -> None:
    path = tmp_path / "imported.json"
    path.write_text('{"command":"run","result":{"access_key":"load-secret"}}')
    loaded = load_bundle(path)
    assert loaded is not None
    assert loaded.result["access_key"] == "[REDACTED]"


def test_nested_state_diff_and_timeline_filters() -> None:
    assert state_diff({"cart": {"count": 1}}, {"cart": {"count": 2}})["changed"] == {
        "cart": {"added": {}, "removed": {}, "changed": {"count": {"before": 1, "after": 2}}}
    }
    trace = {"events": [
        {"actor": "agent", "type": "fault.injected", "name": "latency", "data": {}},
        {"actor": "human", "type": "invariant.fail", "name": "", "data": {}},
    ]}
    assert len(filter_events(trace, actor="agent", faults=True)) == 1
    assert len(filter_events(trace, failures=True)) == 1


def test_editor_builds_multiple_actors_actions_and_executable_faults() -> None:
    scenario = ScenarioEditorModel().build_scenario(
        name="multi", actors={
            "agent": [{"at": "0ms", "invoke": "read", "args": {}}, {"at": "10ms", "retry": "read", "args": {}}],
            "human": [{"at": "5ms", "action": "click", "selector": "#go"}],
        }, faults=[{"type": "latency", "tool": "read", "duration_ms": 10, "at": "before_invoke"}],
        invariants=["count <= 2"], result_invariants=["count != 99"],
    )
    assert list(scenario["actors"]) == ["agent", "human"]
    assert scenario["actors"]["human"][0]["action"] == "click"
    assert scenario["faults"][0]["type"] == "latency"
    assert scenario["invariants"] == ["count <= 2"]
    assert scenario["result_invariants"] == ["count != 99"]
    with pytest.raises(ValueError, match="either actors JSON or ordered actions JSON"):
        ScenarioEditorModel().build_scenario(
            name="conflict", actors={"agent": [{"invoke": "read"}]}, actions=[{"invoke": "other"}]
        )
    with pytest.raises(ValueError, match="metadata-only"):
        ScenarioEditorModel().build_scenario(name="bad", tool="read", fault_label="not-real")


def test_run_history_is_read_only_and_reports_safe_metadata(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    bundle = RunBundle(command="run", run_id="history", scenario={"name": "safe"}, result={"passed": False},
                       execution={"seed": 7, "schedule": []}, artifacts=[Artifact(kind="screenshot", path="secret.png")])
    path = bundle.write(root / "history" / "bundle.json")
    entries = RunHistory(root).entries()
    assert entries[0].run_id == "history"
    assert entries[0].seed == 7
    assert entries[0].artifact_kinds == ("screenshot",)
    history = RunHistory(root)
    assert history.pin_baseline(path) == path.resolve()


def test_console_replay_uses_cli_json_contract_and_not_command_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    trace = TraceRun(scenario="source", events=[TraceEvent(
        timestamp_ms=1, actor="system", type="invariant.fail",
        data={"expression": "count <= 0"}, state_snapshot={"count": 1},
    )])
    source = RunBundle(command="run", run_id="source", trace=trace, result={"passed": False})
    fresh = RunBundle(command="replay", run_id="fresh", trace=trace, result={"passed": False})
    source_path = source.write(tmp_path / "source.json")
    fresh_path = fresh.write(tmp_path / "fresh.json")
    calls: list[list[str]] = []

    class NoReplayAPI:
        async def replay(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("console must not call CommandAPI.replay directly")

        def save(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("console must consume the CLI-emitted bundle")

    def fake_run(command: list[str], **kwargs: object) -> object:
        calls.append(command)
        return type("Completed", (), {"returncode": 1, "stdout": json.dumps({"output": str(fresh_path)}), "stderr": ""})()

    monkeypatch.setattr("webmcp_resilience.console_commands.subprocess.run", fake_run)
    outcome = asyncio.run(ConsoleCommandModule(NoReplayAPI(), replay_allow_mutations=True).replay(source_path))

    assert outcome.status == "reproduced_expected_failure"
    assert calls and calls[0][calls[0].index("replay")] == "replay"
    assert "--json" in calls[0]
    assert "--allow-mutations" in calls[0]


def test_replay_outcome_requires_the_same_failure_identity() -> None:
    def bundle(run_id: str, expression: str, value: int, passed: bool = False) -> RunBundle:
        trace = TraceRun(scenario="identity", events=[TraceEvent(
            timestamp_ms=1, actor="system", type="invariant.fail" if not passed else "invariant.pass",
            data={"expression": expression}, state_snapshot={"count": value},
        )])
        return RunBundle(command="run", run_id=run_id, trace=trace, result={"passed": passed})

    source = bundle("source", "count <= 0", 1)
    same = bundle("same", "count <= 0", 1)
    different_value = bundle("different-value", "count <= 0", 2)
    different = bundle("different", "count >= 2", 1)
    assert replay_outcome(source, same, process_returncode=1).status == "reproduced_expected_failure"
    assert replay_outcome(source, different_value, process_returncode=1).status == "unexpected_failure"
    assert replay_outcome(source, different, process_returncode=1).status == "unexpected_failure"
    assert replay_outcome(source, bundle("fixed", "count <= 0", 0, passed=True)).status == "passed_expected_failure"
    assert replay_outcome(source, None, error="bad CLI JSON").status == "replay_execution_failure"
