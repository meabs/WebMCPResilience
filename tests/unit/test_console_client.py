import asyncio
from pathlib import Path

import yaml

from webmcp_resilience.agent_control import AgentPolicy, LocalMCPControlAdapter
from webmcp_resilience.commands import CommandAPI
from webmcp_resilience.config import Config
from webmcp_resilience.console_client import (
    ScenarioEditorModel,
    bundle_comparison,
    bundle_trace,
    event_state_diff,
    load_bundle,
    safe_artifact_text,
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
