from __future__ import annotations

from pathlib import Path

import pytest
from webmcp_resilience.console_commands import ConsoleCommandModule
from webmcp_resilience.models.bundle import RunBundle
from webmcp_resilience.models.trace import TraceEvent, TraceRun
from webmcp_resilience.tui import TraceConsole


@pytest.mark.asyncio
async def test_console_replay_loads_failure_bundle_and_reports_reproduction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failure_trace = TraceRun(scenario="intentional-failure", events=[
        TraceEvent(timestamp_ms=1, actor="system", type="invariant.fail",
                   data={"expression": "count <= 0"}, state_snapshot={"count": 1}),
    ])
    source = RunBundle(command="run", run_id="source", scenario={"name": "intentional-failure"},
                       trace=failure_trace, result={"passed": False})
    fresh = RunBundle(command="replay", run_id="fresh", scenario=source.scenario,
                      trace=failure_trace, result={"passed": False})
    source_path = source.write(tmp_path / "source.json")
    fresh_path = tmp_path / "fresh.json"

    def fake_cli(command: list[str], **kwargs: object) -> object:
        assert "replay" in command
        assert "--json" in command
        return type("Completed", (), {"returncode": 1, "stdout": '{"output": "' + str(fresh_path) + '"}', "stderr": ""})()

    monkeypatch.setattr("webmcp_resilience.console_commands.subprocess.run", fake_cli)
    fresh.write(fresh_path)
    command_module = ConsoleCommandModule(object())  # CLI replay is the execution seam.

    app = TraceConsole(source_path, command_module=command_module)
    async with app.run_test() as pilot:
        app.run_replay()
        await pilot.pause(0.2)
        detail = str(app.query_one("#detail").render())

    assert "Failure reproduced" in detail
    assert "count <= 0" in detail
    assert "Observed state" in detail
    assert str(fresh_path) in detail
