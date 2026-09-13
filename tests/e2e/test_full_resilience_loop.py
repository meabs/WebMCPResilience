"""Browser-backed proof of the portable CLI/console/MCP resilience loop."""
from __future__ import annotations

import asyncio
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml
from typer.testing import CliRunner

from webmcp_resilience.agent_control import AgentPolicy, LocalMCPControlAdapter
from webmcp_resilience.cli import app
from webmcp_resilience.commands import CommandAPI, ToolContractDriftError
from webmcp_resilience.config import Config
from webmcp_resilience.console_commands import ConsoleCommandModule
from webmcp_resilience.demo import create_server
from webmcp_resilience.models.bundle import redact_recursive
from webmcp_resilience.tui import TraceConsole


pytestmark = pytest.mark.e2e


class _QuietDirectoryHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def lab_server() -> str:
    server = create_server(port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def scenario_path(project: Path) -> Path:
    path = project / ".webmcp" / "scenarios" / "vulnerable-human-tool-race.yaml"
    path.parent.mkdir(parents=True)
    source = Path(__file__).parents[2] / "examples" / "resilience-forge" / ".webmcp" / "scenarios" / path.name
    path.write_text(source.read_text())
    return path


def decode(output: str) -> dict:
    for line in reversed(output.strip().splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"no JSON response in output: {output!r}")


def contract_page(*, quantity_required: bool) -> str:
    required = ", required: ['quantity']" if quantity_required else ""
    return f"""<!doctype html><html><body><script type="module">
const tools = [];
document.modelContext = {{
  async registerTool(tool) {{ tools.push(tool); }},
  async getTools() {{ return tools; }},
  async executeTool(tool, json) {{ return tool.execute(JSON.parse(json)); }},
}};
await document.modelContext.registerTool({{
  name: 'reserve_inventory',
  inputSchema: {{type: 'object', properties: {{quantity: {{type: 'integer'}}}}{required}}},
  outputSchema: {{type: 'object', properties: {{code: {{type: 'string'}}}}, required: ['code']}},
  annotations: {{readOnlyHint: true, semanticVersion: '1'}},
  execute: () => ({{code: 'OK'}}),
}});
</script></body></html>"""


def test_cli_full_loop_replays_a_redacted_adversarial_failure(
    tmp_path: Path, lab_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight, validate, race, reduce, replay, diff, and console all agree."""
    monkeypatch.chdir(tmp_path)
    webmcp = tmp_path / ".webmcp"
    webmcp.mkdir()
    (webmcp / "config.yaml").write_text(
        f"base_url: {lab_server}\nbrowser: chromium\nbrowser_args: []\n"
        "state_script: window.__resilienceLab.getState()\n"
    )
    scenario = scenario_path(tmp_path)
    runner = CliRunner()

    inspected = runner.invoke(app, ["preflight", "--path", "/", "--ci", "--run-id", "inspect", "--json"])
    assert inspected.exit_code == 0, inspected.output
    inspected_payload = decode(inspected.output)
    assert inspected_payload["result"]["passed"] is True
    assert inspected_payload["state_observation"]["mode"] == "state_script"
    assert inspected_payload["state_observation"]["validation"]["valid"] is True

    validated = runner.invoke(app, ["validate", str(scenario), "--run-id", "validated", "--json"])
    assert validated.exit_code == 0, validated.output
    assert decode(validated.output)["result"]["passed"] is True

    failed = runner.invoke(
        app,
        [
            "run", str(scenario), "--ci", "--adversarial", "--seed", "7",
            "--allow-mutations", "--run-id", "race-failure", "--json",
        ],
    )
    assert failed.exit_code == 1, failed.output
    failed_payload = decode(failed.output)
    assert failed_payload["result"]["passed"] is False
    assert failed_payload["result"]["failure_handoff"]["failed_invariant"] == "claims.active <= claims.capacity"
    assert failed_payload["result"]["failure_handoff"]["bundle_path"].endswith("race-failure/bundle.json")
    failure_bundle = webmcp / "runs" / "race-failure" / "bundle.json"
    failure_json = json.loads(failure_bundle.read_text())
    assert failure_json["state_observation"]["mode"] == "state_script"
    assert failure_json["state_observation"]["validation"]["valid"] is True
    assert failure_json["compatibility"]["browser_version"] not in {None, "unknown"}
    assert failure_json["execution"]["schedule"]
    scheduler = failure_json["execution"]["scheduler"]
    assert scheduler["version"] == "1.0"
    assert scheduler["adversarial_seed"] == 7
    assert scheduler["selected_logical_schedule"] == failure_json["execution"]["schedule"]
    assert scheduler["requested_action_offsets"]
    assert [item["action_index"] for item in scheduler["selected_logical_schedule"]] == [0, 0, 1, 1]
    assert scheduler["recorded_trace_timestamps"]
    assert any(item["kind"] == "failure" for item in failure_json["artifacts"])
    assert any(item["kind"] == "scenario" for item in failure_json["artifacts"])
    trace_types = [event["type"] for event in failure_json["trace"]["events"]]
    assert trace_types.count("tool.invoke") == 1
    assert trace_types.index("tool.invoke") < trace_types.index("scheduler.concurrent_dispatch") < trace_types.index("ui.click")
    failed_state = next(event["state_snapshot"] for event in failure_json["trace"]["events"] if event["type"] == "invariant.fail")
    assert failed_state["claims"]["active"] == 2
    repro_path = Path(next(item["path"] for item in failure_json["artifacts"] if item["kind"] == "scenario"))
    repro = yaml.safe_load(repro_path.read_text())
    assert [action.get("invoke") or action.get("action") for actions in repro["scenario"]["actors"].values() for action in actions] == ["claim_slot", "click", "wait"]

    # Persisted bundles are already redacted. Copying the JSON models the
    # handoff to a developer or coding agent in another directory.
    redacted_copy = tmp_path / "handoff-bundle.json"
    redacted_copy.write_text(json.dumps(redact_recursive(failure_json)))
    replayed = runner.invoke(
        app,
        [
            "replay", str(redacted_copy), "--ci", "--allow-mutations",
            "--run-id", "race-replay", "--json",
        ],
    )
    assert replayed.exit_code == 1, replayed.output
    replay_payload = decode(replayed.output)
    assert replay_payload["result"]["passed"] is False
    replay_bundle = webmcp / "runs" / "race-replay" / "bundle.json"
    replay_json = json.loads(replay_bundle.read_text())
    assert replay_json["execution"]["schedule"] == failure_json["execution"]["schedule"]
    assert replay_json["execution"]["scheduler"]["selected_logical_schedule"] == scheduler["selected_logical_schedule"]

    diff = runner.invoke(app, ["diff", str(failure_bundle), str(replay_bundle), "--json"])
    assert diff.exit_code == 0, diff.output
    assert decode(diff.output)["result_changed"] is False

    console = runner.invoke(app, ["console", str(failure_bundle), "--print"])
    assert console.exit_code == 0, console.output
    assert "invariant.fail" in console.output


def test_local_mcp_control_executes_the_same_loop_and_policy(
    tmp_path: Path, lab_server: str
) -> None:
    """Typed local MCP control uses the same browser runner and replay policy."""
    config = Config(base_url=lab_server, state_script="window.__resilienceLab.getState()")
    path = scenario_path(tmp_path)
    adapter = LocalMCPControlAdapter(
        CommandAPI(config, output_dir=tmp_path / ".webmcp" / "runs"),
        AgentPolicy(tmp_path, lab_server, mutation_permission=True, approval_context="CI fixture approval"),
    )

    inspected = asyncio.run(adapter.call("preflight", {"path": "/", "run_id": "agent-inspect"}))
    assert inspected["result"]["passed"] is True
    validated = asyncio.run(adapter.call("validate_scenario", {
        "scenario_id": path.relative_to(tmp_path).as_posix(), "run_id": "agent-validated",
    }))
    assert validated["result"]["passed"] is True
    executed = asyncio.run(adapter.call("run_scenario", {
        "scenario_id": path.relative_to(tmp_path).as_posix(),
        "adversarial": True,
        "seed": 7,
        "run_id": "agent-failure",
    }))
    assert executed["result"]["passed"] is False
    assert executed["bundle"]["approvals"][-1]["source"] == "agent_policy"
    assert executed["bundle"]["agent_policy"]["mutation_authority"] == "mutation_authorized"
    assert executed["bundle"]["state_observation"]["validation"]["valid"] is True

    evidence = asyncio.run(adapter.call("get_failure_or_repro", {
        "run_id": "agent-failure", "artifact": "failure",
    }))
    assert evidence["artifact"] == "failure"
    assert "failed_invariant" in evidence["contents"]

    replayed = asyncio.run(adapter.call("replay_run", {
        "source_run_id": "agent-failure", "run_id": "agent-replay",
    }))
    assert replayed["result"]["passed"] is False
    assert replayed["bundle"]["execution"]["schedule"] == executed["bundle"]["execution"]["schedule"]


def test_browser_backed_replay_rejects_changed_tool_contract(tmp_path: Path) -> None:
    site = tmp_path / "contract-site"
    site.mkdir()
    page = site / "index.html"
    page.write_text(contract_page(quantity_required=False))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(_QuietDirectoryHandler, directory=str(site))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        api = CommandAPI(Config(base_url=base_url), output_dir=tmp_path / ".webmcp" / "runs")
        scenario = {
            "name": "contract-replay",
            "actors": {"agent": [{"invoke": "reserve_inventory", "args": {"quantity": 1}}]},
            "tool_contracts": {"reserve_inventory": {
                "read_only": True,
                "semantic_version": "1",
                "expected_result_codes": ["OK"],
            }},
        }
        baseline = asyncio.run(api.run(scenario=scenario, run_id="contract-baseline", headless=True))
        assert baseline.result["passed"] is True
        assert baseline.tool_contract_expectations["semantic_assertions"]["status"] == "passed"
        baseline_path = api.save(baseline)
        page.write_text(contract_page(quantity_required=True))
        with pytest.raises(ToolContractDriftError) as caught:
            asyncio.run(api.replay(baseline_path, run_id="contract-replay", headless=True))
        assert caught.value.code == "tool_contract_drift"
        assert caught.value.details["changed_input_schemas"] == ["reserve_inventory"]
        assert caught.value.details["policy_impact"] == "breaking"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


async def _wait_for_console_detail(app: TraceConsole, pilot: object, needle: str) -> str:
    # Textual workers run in a background thread; bounded polling keeps this
    # browser-backed assertion deterministic without sleeping in production.
    for _ in range(100):
        await pilot.pause(0.1)  # type: ignore[attr-defined]
        detail = str(app.query_one("#detail").render())
        if needle in detail:
            return detail
    return str(app.query_one("#detail").render())


async def test_browser_backed_console_replays_intentionally_failing_bundle(
    tmp_path: Path, lab_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interactive console reports a reproduced invariant failure."""
    monkeypatch.chdir(tmp_path)
    scenario = scenario_path(tmp_path)
    webmcp = tmp_path / ".webmcp"
    webmcp.mkdir(exist_ok=True)
    (webmcp / "config.yaml").write_text(
        f"base_url: {lab_server}\nbrowser: chromium\nbrowser_args: []\n"
        "state_script: window.__resilienceLab.getState()\n"
    )
    config = Config(base_url=lab_server, state_script="window.__resilienceLab.getState()")
    api = CommandAPI(config, output_dir=tmp_path / ".webmcp" / "runs")
    failed = await api.run(scenario, headless=True, allow_mutations=True, adversarial=True, seed=7, run_id="console-failure")
    assert failed.result["passed"] is False
    failure_path = api.save(failed)
    command_module = ConsoleCommandModule(api, replay_allow_mutations=True)
    app = TraceConsole(failure_path, command_module=command_module)
    async with app.run_test() as pilot:
        app.run_replay()
        detail = await _wait_for_console_detail(app, pilot, "Failure reproduced")
    assert "claims.active <= claims.capacity" in detail
    assert "Observed state" in detail
