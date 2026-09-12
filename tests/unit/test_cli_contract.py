import json
from pathlib import Path

from typer.testing import CliRunner

from webmcp_resilience.cli import _emit, app
from webmcp_resilience.commands import CommandAPI
from webmcp_resilience.config import Config
from webmcp_resilience.models.bundle import Compatibility, RunBundle
from webmcp_resilience.models.trace import TraceEvent, TraceRun


def test_demo_race_is_a_registered_cli_contract() -> None:
    result = CliRunner().invoke(app, ["demo-race", "--help"])
    assert result.exit_code == 0
    assert "Resilience Forge" in result.output
    assert "--json" in result.output


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
