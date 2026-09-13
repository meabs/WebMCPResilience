import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from webmcp_resilience.cli import _emit, app
from webmcp_resilience.commands import CommandAPI
from webmcp_resilience.config import Config
from webmcp_resilience.models.bundle import Compatibility, RunBundle
from webmcp_resilience.models.trace import TraceEvent, TraceRun


def test_demo_race_is_a_registered_cli_contract() -> None:
    result = CliRunner().invoke(app, ["demo-race", "--json", "--help"])
    assert result.exit_code == 0
    assert "Resilience Forge" in result.output


@pytest.mark.parametrize(("flag", "expected"), [(None, False), ("--replay-allow-mutations", True)])
def test_history_receives_cli_configured_replay_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str | None, expected: bool
) -> None:
    captured: dict[str, object] = {}

    def fake_history(path: Path, command_module: object) -> None:
        captured["path"] = path
        captured["module"] = command_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("webmcp_resilience.cli.run_history", fake_history)
    args = ["console", "--history"] + ([flag] if flag else [])
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert captured["module"].replay_allow_mutations is expected  # type: ignore[attr-defined]


def test_failed_bundle_json_contains_one_structured_handoff_document(tmp_path: Path, capsys) -> None:
    trace = TraceRun(scenario="race")
    trace.events.append(TraceEvent(
        timestamp_ms=10,
        actor="system",
        type="invariant.fail",
        data={"expression": "claims.active <= claims.capacity"},
        state_snapshot={"claims": {"active": 2, "capacity": 1}},
    ))
    bundle = RunBundle(
        command="run",
        run_id="contract-failure",
        compatibility=Compatibility(capability_fingerprint="fingerprint"),
        trace=trace,
        result={"passed": False},
        replay_command=["webmcp", "replay", ".webmcp/runs/contract-failure/bundle.json"],
    )
    api = CommandAPI(Config(), output_dir=tmp_path / "runs")
    _emit(bundle, json_output=True, api=api)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    handoff = payload["result"]["failure_handoff"]
    assert handoff["failed_invariant"] == "claims.active <= claims.capacity"
    assert handoff["observed_state"]["claims"]["active"] == 2
    assert handoff["capability_fingerprint"] == "fingerprint"
    assert handoff["replay_command"][0:2] == ["webmcp", "replay"]
