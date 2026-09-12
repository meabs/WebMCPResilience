import asyncio
import io
import json
from pathlib import Path

import pytest
import yaml

from webmcp_resilience.agent_control import AgentPolicy, LocalMCPControlAdapter, serve_stdio, tool_definitions
from webmcp_resilience.commands import CommandAPI, CommandError
from webmcp_resilience.config import Config
from webmcp_resilience.models.bundle import Artifact, RunBundle, SUPPORTED_COMPATIBILITY_GROUPS
from webmcp_resilience.models.scenario import Scenario
from webmcp_resilience.models.trace import TraceEvent, TraceRun


def control(tmp_path: Path, *, mutation: bool = False) -> LocalMCPControlAdapter:
    api = CommandAPI(Config(base_url="http://localhost:3000"), output_dir=tmp_path / ".webmcp" / "runs")
    return LocalMCPControlAdapter(api, AgentPolicy(tmp_path, "http://localhost:3000", mutation_permission=mutation))


def test_agent_rejects_traversal_and_sensitive_artifacts(tmp_path: Path) -> None:
    adapter = control(tmp_path)
    with pytest.raises(CommandError, match="traversal"):
        asyncio.run(adapter.call("validate_scenario", {"scenario_id": "../outside.yaml"}))
    run = RunBundle(command="run", run_id="safe-run", result={"passed": True})
    run.artifacts.append(Artifact(kind="screenshot", path=str(tmp_path / "secret.png"), sensitivity="potentially_sensitive"))
    adapter.api.save(run)
    with pytest.raises(CommandError, match="sensitive"):
        asyncio.run(adapter.call("get_failure_or_repro", {"run_id": "safe-run", "artifact": "screenshot"}))


def test_agent_trace_is_paginated_and_redacted(tmp_path: Path) -> None:
    adapter = control(tmp_path)
    trace = TraceRun(scenario="safe")
    trace.events.append(TraceEvent(timestamp_ms=0, actor="agent", type="tool.result", data={"url": "https://x.test/a?access_token=top-secret"}))
    adapter.api.save(RunBundle(command="run", run_id="trace-run", trace=trace, result={"passed": True}))
    result = asyncio.run(adapter.call("get_trace_slice", {"run_id": "trace-run", "limit": 1}))
    assert result["next_offset"] is None
    assert "top-secret" not in str(result)
    assert "REDACTED" in str(result)


def test_agent_cannot_escalate_mutations_in_request(tmp_path: Path) -> None:
    adapter = control(tmp_path, mutation=False)
    with pytest.raises(CommandError, match="escalation"):
        asyncio.run(adapter.call("run_scenario", {"scenario": {"name": "x", "actors": {"a": [{"invoke": "write"}]}}, "allow_mutations": True}))


def test_agent_rejects_scenario_navigation_outside_fixed_origin(tmp_path: Path) -> None:
    adapter = control(tmp_path)
    with pytest.raises(CommandError, match="outside"):
        asyncio.run(adapter.call("validate_scenario", {"scenario": {"name": "x", "url": "https://elsewhere.test/", "actors": {"a": [{"invoke": "read"}]}}}))


@pytest.mark.parametrize("url", [
    "//elsewhere.test/path",
    "\\\\elsewhere.test\\path",
    "/\\/elsewhere.test/path",
    "/%2f%2felsewhere.test/path",
    "/%2e%2e/elsewhere.test/path",
    "https:elsewhere.test/path",
    " https://elsewhere.test/path",
])
def test_agent_rejects_browser_normalized_navigation_bypass(tmp_path: Path, url: str) -> None:
    adapter = control(tmp_path)
    with pytest.raises(CommandError, match="outside|untrusted"):
        asyncio.run(adapter.call("validate_scenario", {
            "scenario": {"name": "x", "actors": {"agent": [{"action": "navigate", "value": url}]}}
        }))


def test_read_only_agent_ui_action_returns_policy_denied_without_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = control(tmp_path)

    def browser_must_not_start(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("browser should not be constructed for a denied UI action")

    monkeypatch.setattr("webmcp_resilience.commands.BrowserClient", browser_must_not_start)
    # The public envelope is the local-agent error boundary; validation should
    # reject the action before the patched execution seam is reached.
    result = asyncio.run(adapter.call_enveloped("run_scenario", {
        "scenario": {"name": "x", "actors": {"agent": [{"action": "click", "selector": "button"}]}}
    }))
    assert result["ok"] is False
    assert result["error"]["code"] == "policy_denied"


def test_run_ids_reject_path_syntax(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="run id"):
        RunBundle(command="run", run_id="../escape")


def test_policy_is_immutable_and_supports_multiple_origins(tmp_path: Path) -> None:
    policy = AgentPolicy(tmp_path, allowed_target_origins=["http://localhost:3000", "https://example.test/"])
    assert policy.allowed_target_origins == ("http://localhost:3000", "https://example.test")
    with pytest.raises(AttributeError):
        policy.mutation_permission = True  # type: ignore[misc]


def test_agent_run_returns_the_same_domain_bundle_as_command_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = control(tmp_path)
    scenario = {"name": "same", "actors": {"agent": [{"invoke": "read"}]}}

    async def fake_run(self: CommandAPI, scenario_path: Path | None = None, **kwargs: object) -> RunBundle:
        return RunBundle.for_scenario("run", Scenario.model_validate(kwargs["scenario"]), run_id=kwargs["run_id"])

    monkeypatch.setattr(CommandAPI, "run", fake_run)
    direct = asyncio.run(adapter.api.run(scenario=scenario, run_id="direct", allow_mutations=False))
    result = asyncio.run(adapter.call("run_scenario", {"scenario": scenario, "run_id": "agent"}))
    assert result["bundle"]["scenario"] == direct.scenario
    assert result["bundle"]["result"] == direct.result
    assert result["bundle"]["engine_version"] == direct.engine_version


def test_replay_accepts_only_run_ids_or_repository_local_bundle_ids(tmp_path: Path) -> None:
    adapter = control(tmp_path)
    bundle = RunBundle(command="run", run_id="saved-run", result={"passed": True})
    adapter.api.save(bundle)
    assert adapter._source_run_id("saved-run") == "saved-run"
    assert adapter._source_run_id(".webmcp/runs/saved-run/bundle.json") == "saved-run"
    with pytest.raises(CommandError, match="repository-relative"):
        adapter._source_run_id("/tmp/other.json")
    with pytest.raises(CommandError, match="traversal"):
        adapter._source_run_id(".webmcp/runs/../secret.json")


def test_mcp_stdio_exposes_typed_tools_and_versioned_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = control(tmp_path)
    stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setattr("sys.stdout", stdout)
    asyncio.run(serve_stdio(adapter))
    message = json.loads(stdout.getvalue())
    names = {item["name"] for item in message["result"]["tools"]}
    assert names == {"get_capabilities", "preflight", "list_scenarios", "validate_scenario", "run_scenario", "replay_run", "list_runs", "get_run_summary", "get_trace_slice", "get_failure_or_repro", "get_artifact_metadata", "diff_runs", "create_scenario_template"}
    assert all("inputSchema" in item for item in tool_definitions())


def test_capabilities_describe_execution_and_safety_contract(tmp_path: Path) -> None:
    result = asyncio.run(control(tmp_path).call("get_capabilities"))
    assert result["version"] == "1.0"
    assert result["operation"] == "get_capabilities"
    assert "agent" in result["actors"]["supported"]
    assert "invoke" in result["actions"]
    assert "duplicate_invocation" in result["faults"]
    assert ">=" in result["invariant_operators"]
    assert result["browser"]["mode"] == "headless"
    assert result["redaction"]["host_paths_exposed"] is False
    assert result["mutation_policy"]["agent_override"] is False


def test_list_runs_paginates_and_filters_without_host_paths(tmp_path: Path) -> None:
    adapter = control(tmp_path)
    adapter.api.save(RunBundle(command="run", run_id="passed", result={"passed": True}, scenario={"name": "alpha"}))
    adapter.api.save(RunBundle(command="run", run_id="failed", result={"passed": False}, scenario={"name": "beta"}))
    first = asyncio.run(adapter.call("list_runs", {"limit": 1, "status": "failed"}))
    assert first["version"] == "1.0"
    assert first["total"] == 1
    assert first["runs"][0]["run_id"] == "failed"
    assert str(tmp_path) not in str(first)


def test_failure_and_artifact_metadata_are_redacted_and_safe(tmp_path: Path) -> None:
    adapter = control(tmp_path)
    run_root = tmp_path / ".webmcp" / "runs" / "failure-run"
    run_root.mkdir(parents=True)
    failure = run_root / "failure.yaml"
    failure.write_text("failed: true\ntoken: should-not-leak\n")
    bundle = RunBundle(command="run", run_id="failure-run", result={"passed": False}, artifacts=[
        Artifact(kind="failure", path=str(failure), redacted=True, sensitivity="redacted"),
        Artifact(kind="screenshot", path=str(run_root / "secret.png"), redacted=False, sensitivity="potentially_sensitive"),
    ])
    adapter.api.save(bundle)
    evidence = asyncio.run(adapter.call("get_failure_or_repro", {"run_id": "failure-run"}))
    metadata = asyncio.run(adapter.call("get_artifact_metadata", {"run_id": "failure-run"}))
    assert evidence["version"] == metadata["version"] == "1.0"
    assert "should-not-leak" not in str(evidence)
    assert evidence["artifact"] == "failure"
    screenshot = next(item for item in metadata["artifacts"] if item["kind"] == "screenshot")
    assert screenshot["retrievable"] is False
    assert str(tmp_path) not in str(metadata)


def test_trace_filters_are_applied_before_pagination(tmp_path: Path) -> None:
    adapter = control(tmp_path)
    trace = TraceRun(scenario="filtered")
    trace.events.extend([
        TraceEvent(timestamp_ms=0, actor="agent", type="tool.invoke", name="read"),
        TraceEvent(timestamp_ms=1, actor="human", type="tool.invoke", name="read"),
        TraceEvent(timestamp_ms=2, actor="agent", type="tool.result", name="read"),
    ])
    adapter.api.save(RunBundle(command="run", run_id="filtered", trace=trace, result={"passed": True}))
    result = asyncio.run(adapter.call("get_trace_slice", {"run_id": "filtered", "actor": "agent", "event_type": "tool.invoke", "limit": 1}))
    assert result["total"] == 1
    assert len(result["events"]) == 1
    assert result["events"][0]["actor"] == "agent"
    assert result["next_cursor"] is None


def test_template_uses_only_discovered_tools_and_supported_dsl(tmp_path: Path) -> None:
    adapter = control(tmp_path)
    adapter.api.save(RunBundle(command="preflight", run_id="discovery", tool_inventory=[
        {"name": "safe_read", "inputSchema": {"type": "object"}},
        {"name": "other_tool", "inputSchema": {"type": "object"}},
    ]))
    result = asyncio.run(adapter.call("create_scenario_template", {"discovery_run_id": "discovery", "tool_name": "safe_read", "focus": "retry"}))
    template = yaml.safe_load(result["scenario_yaml"])
    assert result["version"] == "1.0"
    assert template["compatibility"]["requires"] == SUPPORTED_COMPATIBILITY_GROUPS
    assert template["actors"]["agent"][0]["invoke"] == "safe_read"
    assert template["actors"]["agent"][1]["retry"] == "safe_read"
    with pytest.raises(CommandError, match="discovery bundle"):
        asyncio.run(adapter.call("create_scenario_template", {"discovery_run_id": "discovery", "tool_name": "invented"}))


def test_agent_inspect_validate_run_failure_and_replay_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = control(tmp_path)
    scenario = {"name": "workflow", "actors": {"agent": [{"invoke": "safe_read"}]}}

    async def fake_preflight(self: CommandAPI, path: str = "/", **kwargs: object) -> RunBundle:
        return RunBundle(command="preflight", run_id=kwargs.get("run_id") or "inspect", tool_inventory=[{"name": "safe_read"}], result={"passed": True})

    async def fake_run(self: CommandAPI, scenario_path: Path | None = None, **kwargs: object) -> RunBundle:
        run_id = kwargs.get("run_id") or "failed-run"
        run_root = self.output_dir / str(run_id)
        run_root.mkdir(parents=True, exist_ok=True)
        failure = run_root / "failure.yaml"
        failure.write_text("token: workflow-secret\n")
        bundle = RunBundle.for_scenario("run", Scenario.model_validate(kwargs["scenario"]), run_id=str(run_id))
        bundle.result = {"passed": False}
        bundle.artifacts = [Artifact(kind="failure", path=str(failure), redacted=True, sensitivity="redacted")]
        return bundle

    async def fake_replay(self: CommandAPI, path: Path, **kwargs: object) -> RunBundle:
        return RunBundle(command="replay", run_id=str(kwargs.get("run_id") or "replayed"), result={"passed": True})

    monkeypatch.setattr(CommandAPI, "preflight", fake_preflight)
    monkeypatch.setattr(CommandAPI, "run", fake_run)
    monkeypatch.setattr(CommandAPI, "replay", fake_replay)

    inspected = asyncio.run(adapter.call("preflight", {"run_id": "inspect"}))
    assert inspected["run_id"] == "inspect"
    validated = asyncio.run(adapter.call("validate_scenario", {"scenario": scenario, "run_id": "validated"}))
    assert validated["result"]["passed"] is True
    executed = asyncio.run(adapter.call("run_scenario", {"scenario": scenario, "run_id": "failed-run"}))
    assert executed["bundle"]["approvals"][-1]["source"] == "agent_policy"
    failure = asyncio.run(adapter.call("get_failure_or_repro", {"run_id": "failed-run"}))
    assert failure["version"] == "1.0"
    assert "workflow-secret" not in str(failure)
    replayed = asyncio.run(adapter.call("replay_run", {"source_run_id": "failed-run", "run_id": "replayed"}))
    assert replayed["result"]["passed"] is True
