"""CLI frontend for the typed CommandAPI; no execution logic lives here."""
import asyncio
import json
import threading
from pathlib import Path

import typer
from rich.console import Console

from .commands import CommandAPI, failure_handoff
from .config import Config, load_config
from .demo import create_server, forge_scenario, serve
from .models.bundle import ENGINE_VERSION, RunBundle
from .trace.otel import export_otel
from .trace.viewer import render_html
from .console import show_trace
from .console_commands import ConsoleCommandModule
from .tui import run_composer, run_console, run_history
from .agent_control import AgentPolicy, LocalMCPControlAdapter, serve_stdio
from .tool_contracts import concise_drift_lines

app = typer.Typer(help="Portable resilience evidence for WebMCP applications.", pretty_exceptions_enable=False)
console = Console()


def _api(output_dir: Path) -> CommandAPI:
    # Validation, diff and report remain useful before `webmcp init`.
    return CommandAPI(load_config() if Path(".webmcp/config.yaml").exists() else Config(), output_dir=output_dir)


def _emit(bundle: RunBundle | dict, *, json_output: bool, output: Path | None = None, api: CommandAPI | None = None) -> None:
    if isinstance(bundle, RunBundle):
        destination = api.save(bundle, output) if api else bundle.write(output or Path("bundle.json"))
        payload = bundle.persisted_dict() | {"output": str(destination)}
        if not bundle.result.get("passed", True):
            handoff = bundle.result.get("failure_handoff") or failure_handoff(bundle, Path(destination))
            payload["result"] = dict(payload.get("result", {}), failure_handoff=handoff)
    else:
        payload = {"contract_version": "1.0", "engine_version": ENGINE_VERSION} | bundle
    if json_output:
        print(json.dumps(payload, default=str, sort_keys=True))
    elif isinstance(bundle, RunBundle):
        summary = f"{'PASS' if bundle.result.get('passed') else 'FAIL'} {bundle.command} · run {bundle.run_id}"
        if bundle.command == "preflight":
            counts = bundle.result.get("finding_counts", {})
            summary += f"\nWebMCP compatibility: {bundle.result.get('status', 'unknown')} · findings: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
            if lines := concise_drift_lines(bundle.tool_contract_drift):
                summary += "\n" + "\n".join(lines)
        if not bundle.result.get("passed", True):
            handoff = bundle.result.get("failure_handoff") or failure_handoff(bundle, Path(payload["output"]))
            console.print(
                f"{summary}\n"
                f"Failed invariant: {handoff.get('failed_invariant') or 'unknown'}\n"
                f"Observed state: {json.dumps(handoff.get('observed_state', {}), sort_keys=True)}\n"
                f"Capability fingerprint: {handoff.get('capability_fingerprint') or 'unknown'}\n"
                f"Tool inventory fingerprint: {handoff.get('tool_inventory_fingerprint') or 'unknown'}\n"
                f"Tool contract drift: {(handoff.get('tool_contract_drift') or {}).get('status', 'not_compared')} "
                f"({(handoff.get('tool_contract_drift') or {}).get('policy_impact', 'none')})\n"
                f"Bundle: {handoff.get('bundle_path') or payload['output']}\n"
                f"Reduced repro: {handoff.get('reduced_repro_path') or 'none'}\n"
                f"Replay: {' '.join(handoff.get('replay_command') or bundle.replay_command)}"
            )
        else:
            console.print(f"{summary}\nBundle: {payload['output']}")
    else:
        console.print_json(json.dumps(payload, default=str))
        if lines := concise_drift_lines(payload.get("tool_contract_drift", {})):
            console.print("\n".join(lines))


def _command_error(error: Exception, json_output: bool) -> None:
    payload = {"schema_version": "1.0", "contract_version": "1.0", "engine_version": ENGINE_VERSION, "error": str(error), "error_code": getattr(error, "code", "internal_error"), "exit_code": 2}
    if hasattr(error, "details"):
        payload["details"] = getattr(error, "details")
    if json_output: print(json.dumps(payload, sort_keys=True))
    else: console.print(f"[red]Error:[/red] {payload['error']}")
    raise typer.Exit(2)


@app.command()
def init(directory: Path = Path(".webmcp"), json_output: bool = typer.Option(False, "--json")) -> None:
    """Initialize an isolated local project configuration."""
    try:
        (directory / "scenarios").mkdir(parents=True, exist_ok=True); (directory / "failures").mkdir(parents=True, exist_ok=True)
        config = directory / "config.yaml"
        if not config.exists(): config.write_text("base_url: http://localhost:3000\nbrowser: chromium\nbrowser_args: []\n")
        payload = {"schema_version": "1.0", "contract_version": "1.0", "engine_version": ENGINE_VERSION, "result": "initialized", "directory": str(directory)}
        print(json.dumps(payload, sort_keys=True)) if json_output else console.print(f"Initialized [bold]{directory}[/bold]")
    except Exception as error: _command_error(error, json_output)


@app.command()
def preflight(path: str = "/", ci: bool = typer.Option(False, "--ci"), run_id: str | None = typer.Option(None, "--run-id"), output: Path | None = typer.Option(None, "--output"), json_output: bool = typer.Option(False, "--json")) -> None:
    """Collect non-mutating WebMCP/browser readiness evidence."""
    try:
        api = _api(Path(".webmcp/runs")); bundle = asyncio.run(api.preflight(path, headless=ci, run_id=run_id)); _emit(bundle, json_output=json_output, output=output, api=api)
        if not bundle.result["passed"]: raise typer.Exit(1)
    except typer.Exit: raise
    except Exception as error: _command_error(error, json_output)


@app.command()
def validate(scenario: Path, run_id: str | None = typer.Option(None, "--run-id"), output: Path | None = typer.Option(None, "--output"), json_output: bool = typer.Option(False, "--json")) -> None:
    """Validate a YAML scenario and emit its portable declaration bundle."""
    try:
        api = _api(Path(".webmcp/runs")); _emit(api.validate(scenario, run_id=run_id), json_output=json_output, output=output, api=api)
    except Exception as error: _command_error(error, json_output)


@app.command()
def run(scenario: Path, ci: bool = typer.Option(False, "--ci"), adversarial: bool = typer.Option(False, "--adversarial"), seed: int = typer.Option(0, "--seed"), allow_mutations: bool = typer.Option(False, "--allow-mutations"), run_id: str | None = typer.Option(None, "--run-id"), output: Path | None = typer.Option(None, "--output"), json_output: bool = typer.Option(False, "--json")) -> None:
    """Execute one scenario. Every invocation emits a complete run bundle."""
    try:
        api = _api(Path(".webmcp/runs")); bundle = asyncio.run(api.run(scenario, run_id=run_id, headless=ci, allow_mutations=allow_mutations, adversarial=adversarial, seed=seed)); _emit(bundle, json_output=json_output, output=output, api=api)
        if not bundle.result["passed"]: raise typer.Exit(1)
    except typer.Exit: raise
    except Exception as error: _command_error(error, json_output)


# Backward-compatible spelling; both names cross the same CommandAPI seam.
app.command("test")(run)
app.command("inspect")(preflight)


@app.command()
def replay(bundle: Path, ci: bool = typer.Option(True, "--ci/--headed"), allow_mutations: bool = typer.Option(False, "--allow-mutations"), run_id: str | None = typer.Option(None, "--run-id"), output: Path | None = typer.Option(None, "--output"), json_output: bool = typer.Option(False, "--json")) -> None:
    """Replay only a compatible versioned bundle; unsafe artifacts are rejected."""
    try:
        api = _api(Path(".webmcp/runs")); result = asyncio.run(api.replay(bundle, run_id=run_id, headless=ci, allow_mutations=allow_mutations)); _emit(result, json_output=json_output, output=output, api=api)
        if not result.result["passed"]: raise typer.Exit(1)
    except typer.Exit: raise
    except Exception as error: _command_error(error, json_output)


@app.command()
def diff(left: Path, right: Path, json_output: bool = typer.Option(False, "--json")) -> None:
    """Compare two portable run bundles without launching a browser."""
    try: _emit(_api(Path(".webmcp/runs")).diff(left, right), json_output=json_output)
    except Exception as error: _command_error(error, json_output)


@app.command()
def report(bundle: Path, output: Path | None = typer.Option(None, "--output"), json_output: bool = typer.Option(False, "--json")) -> None:
    """Render an offline bundle report; JSON mode returns its stable summary."""
    try:
        _emit(_api(Path(".webmcp/runs")).report(bundle, output), json_output=json_output)
    except Exception as error: _command_error(error, json_output)


@app.command("handoff")
def handoff(bundle: Path, output: Path | None = typer.Option(None, "--output"), json_output: bool = typer.Option(False, "--json")) -> None:
    """Export redacted Markdown/JSON incident metadata without evidence contents."""
    try:
        outputs = _api(Path(".webmcp/runs")).export_handoff(bundle, output)
        summary = json.loads(outputs["json"].read_text())
        payload = {"schema_version": "1.0", "contract_version": "1.0", "outputs": {key: str(value) for key, value in outputs.items()},
                   "tool_inventory_fingerprint": summary.get("tool_inventory_fingerprint"),
                   "tool_contract_drift": summary.get("tool_contract_drift", {})}
        if json_output:
            print(json.dumps(payload, sort_keys=True))
        else:
            drift = payload["tool_contract_drift"]
            console.print(f"Tool contract: {drift.get('status', 'not_compared')} ({drift.get('policy_impact', 'none')})\nMarkdown: {outputs['markdown']}\nJSON: {outputs['json']}")
    except Exception as error: _command_error(error, json_output)


@app.command()
def demo(host: str = "127.0.0.1", port: int = 4173) -> None: serve(host, port)


@app.command("demo-race")
def demo_race(host: str = "127.0.0.1", port: int = 4173,
              seed: int = typer.Option(7, "--seed"),
              run_id: str = typer.Option("demo-race-failure", "--run-id"),
              json_output: bool = typer.Option(False, "--json/--no-json", help="Emit machine-readable output.")) -> None:
    """Orchestrate the included local Resilience Forge vulnerable race. Use --json for machine-readable output."""
    server = None
    try:
        server = create_server(host, port)
        thread = threading.Thread(target=server.serve_forever, name="webmcp-resilience-forge", daemon=True)
        thread.start()
        config = Config(base_url=f"http://{host}:{server.server_port}", state_script="window.__resilienceLab.getState()")
        api = CommandAPI(config, output_dir=Path(".webmcp/runs"))
        scenario_path = forge_scenario()

        async def workflow() -> dict[str, object]:
            preflight_bundle = await api.preflight("/", headless=True, run_id=f"{run_id}-preflight")
            preflight_path = api.save(preflight_bundle)
            validated = api.validate(scenario_path, run_id=f"{run_id}-validate")
            validate_path = api.save(validated)
            failed = await api.run(
                scenario_path, run_id=run_id, headless=True, allow_mutations=True,
                adversarial=True, seed=seed,
            )
            failure_path = api.save(failed)
            replayed = await api.replay(failure_path, run_id=f"{run_id}-replay", headless=True, allow_mutations=True)
            replay_path = api.save(replayed)
            return {
                "preflight": {"passed": preflight_bundle.result.get("passed"), "bundle_path": str(preflight_path)},
                "validation": {"passed": validated.result.get("passed"), "bundle_path": str(validate_path)},
                "run": {"passed": failed.result.get("passed"), "bundle_path": str(failure_path), "failure_handoff": failed.result.get("failure_handoff")},
                "replay": {"passed": replayed.result.get("passed"), "bundle_path": str(replay_path), "failure_handoff": replayed.result.get("failure_handoff")},
                "scenario": str(scenario_path),
                "seed": seed,
            }

        result = asyncio.run(workflow())
        if json_output:
            print(json.dumps({"schema_version": "1.0", "contract_version": "1.0", "engine_version": ENGINE_VERSION,
                              "command": "demo-race", "result": result}, default=str, sort_keys=True))
        else:
            console.print("Resilience Forge demo started: " + f"http://{host}:{server.server_port}")
            console.print("Completed preflight -> validation -> seeded adversarial run -> reduction -> replay")
            failed_handoff = result["run"].get("failure_handoff") if isinstance(result.get("run"), dict) else None
            if isinstance(failed_handoff, dict):
                console.print(
                    f"Failed invariant: {failed_handoff.get('failed_invariant') or 'unknown'}\n"
                    f"Observed state: {json.dumps(failed_handoff.get('observed_state', {}), sort_keys=True)}\n"
                    f"Capability fingerprint: {failed_handoff.get('capability_fingerprint') or 'unknown'}\n"
                    f"Tool inventory fingerprint: {failed_handoff.get('tool_inventory_fingerprint') or 'unknown'}\n"
                    f"Tool contract drift: {(failed_handoff.get('tool_contract_drift') or {}).get('status', 'not_compared')} "
                    f"({(failed_handoff.get('tool_contract_drift') or {}).get('policy_impact', 'none')})\n"
                    f"Bundle: {failed_handoff.get('bundle_path')}\n"
                    f"Reduced repro: {failed_handoff.get('reduced_repro_path') or 'none'}\n"
                    f"Replay: {' '.join(failed_handoff.get('replay_command') or [])}"
                )
    except Exception as error:
        _command_error(error, json_output)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


@app.command("agent-server")
def agent_server(project_root: Path = Path("."), target_origin: str | None = None,
                 allow_mutations: bool = typer.Option(False, "--allow-mutations"),
                 concurrency: int = typer.Option(1, "--concurrency")) -> None:
    """Start the local stdio control adapter for coding agents only."""
    try:
        root = project_root.resolve()
        config = load_config(root / ".webmcp") if (root / ".webmcp" / "config.yaml").exists() else Config()
        api = CommandAPI(config, output_dir=root / ".webmcp" / "runs")
        policy = AgentPolicy(root, target_origin or config.base_url, concurrency_limit=concurrency,
                             mutation_permission=allow_mutations,
                             approval_context="operator enabled --allow-mutations" if allow_mutations else "operator started read-only server")
        asyncio.run(serve_stdio(LocalMCPControlAdapter(api, policy)))
    except Exception as error:
        _command_error(error, False)


@app.command()
def trace(trace_file: Path, html_output: Path | None = typer.Option(None, "--html"), otel_output: Path | None = typer.Option(None, "--otel"), json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        outputs: list[str] = []
        if html_output or not otel_output:
            target = html_output or trace_file.with_suffix(".html"); render_html(trace_file, target); outputs.append(str(target))
        if otel_output: export_otel(trace_file, otel_output); outputs.append(str(otel_output))
        _emit({"schema_version": "1.0", "outputs": outputs}, json_output=json_output)
    except Exception as error: _command_error(error, json_output)


@app.command("console")
def console_ui(trace_file: Path | None = typer.Argument(None), print_view: bool = typer.Option(False, "--print"), replay_allow_mutations: bool = typer.Option(False, "--replay-allow-mutations"), compare: Path | None = typer.Option(None, "--compare"), discovery: Path | None = typer.Option(None, "--discovery", help="Portable preflight bundle whose schemas drive the editor"), history: bool = typer.Option(False, "--history", help="Browse saved portable run bundles")) -> None:
    if history:
        # Keep history replay on the same CLI-owned authority path as bundle
        # replay. The TUI receives the configured module; it cannot escalate.
        run_history(Path(".webmcp/runs"), ConsoleCommandModule(_api(Path(".webmcp/runs")), replay_allow_mutations=replay_allow_mutations))
    elif trace_file is None: run_composer(discovery)
    elif print_view: show_trace(trace_file, console, compare)
    else:
        # The CLI is the authority boundary.  The TUI receives an already
        # configured command module and cannot escalate its own permission.
        run_console(trace_file, ConsoleCommandModule(_api(Path(".webmcp/runs")), replay_allow_mutations=replay_allow_mutations), compare)


if __name__ == "__main__": app()
