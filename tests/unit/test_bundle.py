from pathlib import Path
import json

import pytest

from webmcp_resilience.commands import CommandAPI, CommandError, diff_bundles
from webmcp_resilience.config import Config
from webmcp_resilience.models.bundle import Artifact, Compatibility, CompatibilityRequirements, RunBundle
from webmcp_resilience.models.trace import TraceEvent, TraceRun


def test_validate_emits_portable_versioned_bundle(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.yaml"
    scenario.write_text("name: portable\nactors:\n  agent:\n    - invoke: read_status\n")
    bundle = CommandAPI(Config()).validate(scenario, run_id="run-123")
    assert bundle.schema_version == "2.0"
    assert bundle.run_id == "run-123"
    assert bundle.actions[0]["invoke"] == "read_status"
    assert bundle.approvals == []


def test_replay_rejects_unversioned_or_incompatible_artifact(tmp_path: Path) -> None:
    path = tmp_path / "old.json"
    path.write_text('{"scenario": {"name": "x"}}')
    with pytest.raises(CommandError, match="unsafe replay rejected"):
        CommandAPI(Config()).replay_bundle(path)


def test_bundle_diff_compares_trace_independently_of_frontend() -> None:
    left = RunBundle(command="run", result={"passed": True})
    right = RunBundle(command="run", result={"passed": False})
    assert diff_bundles(left, right)["result_changed"] is True


def test_bundle_diff_separates_compatibility_and_behavioural_drift() -> None:
    left = RunBundle(command="run", run_id="left", result={"passed": True}, execution={"schedule": [], "approval_policy": []})
    right = RunBundle(command="run", run_id="right", result={"passed": False}, execution={"schedule": [{"actor": "a"}], "approval_policy": []})
    diff = diff_bundles(left, right)
    assert diff["behavioural_drift"]["changed"] is True
    assert diff["compatibility_drift"]["changed"] is False


def test_persistence_recursively_redacts_evidence(tmp_path: Path) -> None:
    trace = TraceRun(scenario="secrets")
    trace.events.append(TraceEvent(timestamp_ms=1, actor="agent", type="tool.result", data={"result": {"access_token": "result-secret"}}, state_snapshot={"password": "snapshot-secret"}))
    bundle = RunBundle(command="run", scenario={"actors": {"agent": [{"args": {"secret": "argument-secret"}}]}}, actions=[{"args": {"authorization": "action-secret"}}], trace=trace, result={"error": {"token": "error-secret"}}, browser_environment={"network": {"cookie": "cookie-secret"}})
    target = bundle.write(tmp_path / "bundle.json")
    text = target.read_text()
    assert all(value not in text for value in ("argument-secret", "action-secret", "result-secret", "snapshot-secret", "error-secret", "cookie-secret"))
    assert text.count("[REDACTED]") >= 5


def test_persisted_url_evidence_redacts_query_credentials_and_userinfo(tmp_path: Path) -> None:
    secret = "not-for-evidence"
    bundle = RunBundle(command="run", browser_environment={"url": f"https://user:{secret}@example.test/path?access_token={secret}&page=2"})
    text = bundle.write(tmp_path / "bundle.json").read_text()
    assert secret not in text
    assert "user:" not in text
    assert "access_token=%5BREDACTED%5D" in text


def test_persistence_redacts_signature_and_credential_variants(tmp_path: Path) -> None:
    secret = "variant-secret"
    bundle = RunBundle(
        command="run",
        result={
            "api-key": secret,
            "apikey": secret,
            "access_key": secret,
            "credential": secret,
            "X-Amz-Signature": secret,
            "sig": secret,
        },
        browser_environment={
            "url": "https://example.test/download?sig=" + secret + "&signature=" + secret + "&x-amz-signature=" + secret
        },
    )
    text = bundle.write(tmp_path / "variants.json").read_text()
    assert secret not in text
    assert text.count("[REDACTED]") >= 6
    assert text.count("%5BREDACTED%5D") >= 3


def replay_contract(*, groups: dict[str, str] | None = None) -> RunBundle:
    scenario = {"name": "race", "actors": {"a": [{"invoke": "read"}], "b": [{"invoke": "read"}]}}
    requirements = CompatibilityRequirements.model_validate(groups or CompatibilityRequirements().model_dump())
    return RunBundle(command="run", scenario=scenario, requirements=requirements, compatibility=Compatibility(groups=requirements), result={"passed": False},
                     faults=[], approvals=[],
                     execution={"adversarial": True, "seed": 9, "schedule": [{"offset_ms": 0, "actor": "b", "action_index": 0}, {"offset_ms": 0, "actor": "a", "action_index": 0}], "schedule_index": 1, "requirements": requirements.model_dump(), "fault_configuration": [], "approval_policy": []})


def test_command_api_replay_preserves_recorded_adversarial_schedule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A saved adversarial failure reuses its chosen schedule through CommandAPI."""
    source = replay_contract()
    path = source.write(tmp_path / "bundle.json")
    captured: dict[str, object] = {}

    async def fake_run(self: CommandAPI, scenario_path: Path | None, **kwargs: object) -> RunBundle:
        captured.update(kwargs)
        return RunBundle(command="run", scenario=source.scenario, result={"passed": True})

    monkeypatch.setattr(CommandAPI, "run", fake_run)
    result = __import__("asyncio").run(CommandAPI(Config()).replay(path))
    assert result.command == "replay"
    assert captured["adversarial"] is True
    assert captured["recorded_schedule"] == source.execution["schedule"]


def test_replay_prefers_versioned_selected_logical_schedule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = replay_contract()
    source.execution["scheduler"] = {
        "version": "1.0",
        "requested_action_offsets": [{"actor": "a", "action_index": 0, "offset_ms": 0}],
        "selected_logical_schedule": source.execution["schedule"],
        "adversarial_seed": 9,
        "recorded_trace_timestamps": [],
    }
    path = source.write(tmp_path / "scheduler.json")
    captured: dict[str, object] = {}

    async def fake_run(self: CommandAPI, scenario_path: Path | None, **kwargs: object) -> RunBundle:
        captured.update(kwargs)
        return RunBundle(command="run", scenario=source.scenario, result={"passed": True})

    monkeypatch.setattr(CommandAPI, "run", fake_run)
    __import__("asyncio").run(CommandAPI(Config()).replay(path))
    assert captured["recorded_schedule"] == source.execution["scheduler"]["selected_logical_schedule"]


def test_report_escapes_tool_result_markup(tmp_path: Path) -> None:
    trace = TraceRun(scenario="markup")
    trace.events.append(TraceEvent(timestamp_ms=1, actor="agent", type="tool.result", data={"result": "<script>alert(1)</script>"}))
    bundle_path = RunBundle(command="run", trace=trace).write(tmp_path / "bundle.json")
    report = tmp_path / "report.html"
    result = CommandAPI(Config()).report(bundle_path, report)
    assert result["passed"] is None
    assert "<script>" not in report.read_text()
    assert "&lt;script&gt;" in report.read_text()


def test_command_api_validates_discovered_arguments_and_exports_safe_handoff(tmp_path: Path) -> None:
    api = CommandAPI(Config(), output_dir=tmp_path / "runs")
    scenario = {"name": "schema", "actors": {"agent": [{"invoke": "read", "args": {}}]}}
    discovery = [{"name": "read", "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}]
    with pytest.raises(CommandError, match="missing required"):
        api.validate(scenario=scenario, tool_inventory=discovery)

    trace = TraceRun(scenario="incident")
    trace.events.append(TraceEvent(timestamp_ms=1, actor="system", type="invariant.fail", data={"expression": "count <= 0"}, state_snapshot={"count": 1}))
    screenshot = tmp_path / "secret.png"
    screenshot.write_bytes(b"private screenshot")
    bundle = RunBundle(command="run", run_id="incident", trace=trace, result={"passed": False}, artifacts=[
        Artifact(kind="screenshot", path=str(screenshot)),
    ])
    bundle_path = bundle.write(tmp_path / "incident.json")
    outputs = api.export_handoff(bundle_path)
    assert "count <= 0" in outputs["markdown"].read_text()
    assert "private screenshot" not in outputs["markdown"].read_text()
    assert "screenshot" in outputs["json"].read_text()


@pytest.mark.parametrize("group", ["fault_model", "invariant_model", "trace_model"])
def test_replay_rejects_changed_semantic_group_versions(tmp_path: Path, group: str) -> None:
    groups = CompatibilityRequirements().model_dump()
    groups[group] = "999"
    bundle = replay_contract(groups=groups)
    with pytest.raises(CommandError, match="incompatible compatibility groups"):
        CommandAPI(Config()).replay_bundle(bundle.write(tmp_path / f"{group}.json"))


def test_replay_rejects_legacy_flat_requirements_without_translation(tmp_path: Path) -> None:
    bundle = replay_contract()
    bundle.execution["requirements"] = ["scenario-1", "trace-1"]
    with pytest.raises(CommandError, match="inconsistent compatibility requirements"):
        CommandAPI(Config()).replay_bundle(bundle.write(tmp_path / "legacy.json"))
