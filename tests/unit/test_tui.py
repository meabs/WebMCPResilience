import json
from pathlib import Path

import pytest
import yaml
from textual.widgets import Button

from webmcp_resilience.tui import ExperimentConsole, HistoryConsole, TraceConsole
from webmcp_resilience.console_commands import ReplayOutcome, replay_outcome
from webmcp_resilience.models.bundle import Artifact, RunBundle
from webmcp_resilience.models.trace import TraceRun


@pytest.mark.asyncio
async def test_console_selects_event_payload(tmp_path: Path) -> None:
    trace = tmp_path / "trace.json"
    trace.write_text(json.dumps({"scenario": "console-test", "events": [{"timestamp_ms": 1, "actor": "agent", "type": "tool.result", "name": "read", "data": {}, "state_snapshot": {}}]}))
    app = TraceConsole(trace)
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()
        assert "tool.result" in str(app.query_one("#detail").render())
        assert "ACTOR EVENTS" in str(app.query_one("#summary").render())


@pytest.mark.asyncio
async def test_console_reloads_fresh_trace_and_compares(tmp_path: Path) -> None:
    old_trace = tmp_path / "old.json"
    fresh_trace = tmp_path / "fresh.json"
    old_trace.write_text(json.dumps({"scenario": "console-test", "events": [{"timestamp_ms": 1, "actor": "agent", "type": "tool.invoke", "name": "read", "data": {}}]}))
    fresh_trace.write_text(json.dumps({"scenario": "console-test", "events": [{"timestamp_ms": 2, "actor": "agent", "type": "tool.invoke", "name": "read", "data": {}}, {"timestamp_ms": 3, "actor": "agent", "type": "tool.result", "name": "read", "data": {}}]}))
    app = TraceConsole(old_trace, compare_path=old_trace)
    async with app.run_test() as pilot:
        await app._replace_trace(fresh_trace, "ok")
        app.action_compare()
        await pilot.pause()
        assert [event["type"] for event in app.events] == ["tool.invoke", "tool.result"]
        assert "+ 1 × agent · tool.result · read" in str(app.query_one("#detail").render())


@pytest.mark.asyncio
async def test_console_reads_current_bundle_trace_artifact(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps({"scenario": "bundle-console", "events": [{"timestamp_ms": 1, "actor": "agent", "type": "tool.result", "data": {}}]}))
    bundle_path = RunBundle(command="run", trace=TraceRun(scenario="bundle-console"), artifacts=[Artifact(kind="trace", path=str(trace_path), redacted=True, sensitivity="redacted")]).write(tmp_path / "bundle.json")
    app = TraceConsole(bundle_path)
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()
        assert app.bundle_path == bundle_path.resolve()
        assert "tool.result" in str(app.query_one("#detail").render())


@pytest.mark.asyncio
async def test_console_filter_actions_reduce_timeline(tmp_path: Path) -> None:
    trace = tmp_path / "trace.json"
    trace.write_text(json.dumps({"scenario": "filters", "events": [
        {"timestamp_ms": 1, "actor": "agent", "type": "fault.injected", "name": "latency", "data": {}},
        {"timestamp_ms": 2, "actor": "agent", "type": "tool.result", "name": "read", "data": {}},
    ]}))
    app = TraceConsole(trace)
    async with app.run_test() as pilot:
        app.action_toggle_faults()
        await pilot.pause()
        assert len(app.events) == 1


@pytest.mark.asyncio
async def test_composer_writes_cli_compatible_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = ExperimentConsole()
    async with app.run_test() as pilot:
        app.query_one("#name").value = "read-status"
        app.query_one("#tool").value = "get_webmcp_status"
        app.on_button_pressed(Button.Pressed(app.query_one("#save")))
        await pilot.pause()
    scenario = yaml.safe_load((tmp_path / ".webmcp/scenarios/read-status.yaml").read_text())
    assert scenario["actors"]["agent"][0]["invoke"] == "get_webmcp_status"


def test_replay_outcome_reproduces_expected_failure_even_when_process_failed() -> None:
    source = RunBundle(
        command="run",
        run_id="source-failure",
        scenario={"name": "intentional-failure"},
        trace=TraceRun(scenario="intentional-failure", events=[
            {"timestamp_ms": 1, "actor": "system", "type": "invariant.fail",
             "data": {"expression": "count <= 0"}, "state_snapshot": {"count": 1}},
        ]),
        result={"passed": False},
    )
    fresh = RunBundle(
        command="replay",
        run_id="fresh-failure",
        scenario={"name": "intentional-failure"},
        result={"passed": False, "failure_handoff": {"failed_invariant": "count <= 0", "observed_state": {"count": 1}}},
    )

    outcome = replay_outcome(source, fresh, fresh_path=Path("/tmp/fresh-bundle.json"), process_returncode=1)

    assert isinstance(outcome, ReplayOutcome)
    assert outcome.status == "reproduced_expected_failure"
    assert outcome.invariant == "count <= 0"
    assert outcome.observed_state == {"count": 1}


def test_documented_tui_keyboard_controls_have_real_actions() -> None:
    trace_bindings = {binding[0]: binding[1] for binding in TraceConsole.BINDINGS}
    history_bindings = {binding[0]: binding[1] for binding in HistoryConsole.BINDINGS}
    assert {"q", "home", "end", "r", "c", "m", "h"} <= set(trace_bindings)
    assert {"q", "b", "t", "r", "c", "m", "h"} <= set(history_bindings)
    assert all(hasattr(TraceConsole, f"action_{action}") for action in trace_bindings.values() if action != "quit")
    assert all(hasattr(HistoryConsole, f"action_{action}") for action in history_bindings.values() if action != "quit")
