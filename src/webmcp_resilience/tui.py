import json
from pathlib import Path
import subprocess
import sys
from collections import Counter

import yaml

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual import work
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from .models.bundle import RunBundle, redact_recursive
from .console_client import (
    FAILURE_EVENTS,
    ScenarioEditorModel,
    actor_lanes,
    bundle_comparison,
    bundle_trace,
    event_counts,
    event_state_diff,
    json_text,
    load_bundle,
    safe_artifact_text,
    visible_events,
)


class TraceConsole(App[None]):
    """Interactive terminal flight deck for a recorded WebMCP run."""
    CSS = """
    Screen { background: #101722; color: #eaf2f3; }
    Header { background: #1e2b3a; color: #3bd4d1; }
    Footer { background: #1e2b3a; }
    #summary { height: 5; margin: 1 2 0 2; padding: 0 1; color: #ffcf8f; background: #162131; border: solid #385063; }
    #layout { height: 1fr; padding: 1 2; }
    #events { width: 58%; border: solid #385063; background: #162131; }
    #detail { width: 42%; margin-left: 1; border: solid #385063; background: #1e2b3a; padding: 1 2; }
    ListItem { padding: 0 1; }
    ListItem.-highlight { background: #3bd4d1; color: #101722; }
    .ok { color: #3bd4d1; } .warn { color: #ffcf8f; } .bad { color: #ff8b7b; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("home", "first", "First event"),
        ("end", "last", "Last event"),
        ("r", "replay", "Replay"),
        ("c", "compare", "Compare"),
        ("m", "repro", "Min repro"),
    ]

    def __init__(self, trace_path: Path, replay_allow_mutations: bool = False,
                 compare_path: Path | None = None) -> None:
        super().__init__()
        self.trace_path = trace_path.resolve()
        self.bundle_path: Path | None = None
        self.bundle: RunBundle | None = None
        self.replay_allow_mutations = replay_allow_mutations
        self.compare_path = compare_path.resolve() if compare_path else None
        self.compare_bundle: RunBundle | None = load_bundle(self.compare_path) if self.compare_path else None
        self.compare_trace = self._trace_for_bundle(self.compare_path, self.compare_bundle) if self.compare_bundle else (redact_recursive(json.loads(compare_path.read_text())) if compare_path else None)
        self._load_input(self.trace_path)

    @staticmethod
    def _trace_for_bundle(path: Path | None, bundle: RunBundle | None) -> dict | None:
        if bundle is None or path is None:
            return None
        if bundle.trace and bundle.trace.events:
            return bundle_trace(bundle)
        artifact = next((item for item in bundle.artifacts if item.kind == "trace" and item.redacted and item.sensitivity == "redacted"), None)
        if artifact is None:
            return bundle_trace(bundle)
        artifact_path = Path(artifact.path)
        if not artifact_path.is_absolute():
            artifact_path = path.parent / artifact_path
        try:
            artifact_path = artifact_path.resolve()
            artifact_path.relative_to(path.parent.resolve())
            return redact_recursive(json.loads(artifact_path.read_text()))
        except (OSError, ValueError, json.JSONDecodeError):
            return bundle_trace(bundle)

    def _load_input(self, path: Path) -> None:
        """Current consoles consume a run bundle; bare traces remain view-only diagnostics."""
        bundle = load_bundle(path)
        if bundle is None:
            self._load_trace(path)
            return
        self.bundle_path = path.resolve()
        self.bundle = bundle
        # Embedded trace data is the portable contract. The external trace is
        # only a backwards-compatible fallback, and must already be redacted.
        if bundle.trace and bundle.trace.events:
            self.trace_path = path.resolve()
            self.trace = bundle_trace(bundle)
            self.events = visible_events(self.trace)
            self.title = f"WebMCP Console · {self.trace['scenario']}"
            return
        trace_artifact = next((artifact for artifact in bundle.artifacts if artifact.kind == "trace"), None)
        if trace_artifact and trace_artifact.redacted and trace_artifact.sensitivity == "redacted":
            artifact_path = Path(trace_artifact.path)
            if not artifact_path.is_absolute():
                artifact_path = path.parent / artifact_path
            try:
                artifact_path = artifact_path.resolve()
                artifact_path.relative_to(path.parent.resolve())
            except ValueError as error:
                raise ValueError("bundle trace artifact is outside the bundle directory") from error
            self._load_trace(artifact_path)
            return
        raise ValueError(f"bundle {path} has no safe embedded trace")

    def _load_trace(self, trace_path: Path) -> None:
        self.trace_path = trace_path.resolve()
        self.trace = redact_recursive(json.loads(self.trace_path.read_text()))
        self.events = visible_events(self.trace)
        self.title = f"WebMCP Console · {self.trace['scenario']}"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(self._summary(), id="summary")
        with Horizontal(id="layout"):
            yield ListView(*[self._item(index, event) for index, event in enumerate(self.events)], id="events")
            yield Static("Select an event to inspect its payload.", id="detail")
        yield Footer()

    def _summary(self) -> str:
        actors = actor_lanes(self.trace)
        state_versions = sum(event["type"] == "state.observed" for event in self.events)
        faults = sum("fault" in (event.get("name") or "").lower() or "fault" in event["type"] for event in self.events)
        calls = sum(event["type"] == "tool.invoke" for event in self.events)
        failures = sum(event.get("type") in FAILURE_EVENTS for event in self.events)
        lane_text = ", ".join(f"{actor}({count})" for actor, count in actors.items()) or "no actor lanes"
        comparison = "    [c] no comparison"
        if self.compare_trace:
            added, removed = self._comparison_counts()
            comparison = f"    COMPARE  +{added}/-{removed} events  [c] details"
        result = "PASS" if self.bundle is None or self.bundle.result.get("passed", True) else "FAIL"
        approvals = self.bundle.approvals if self.bundle else []
        authority = approvals[-1].authority if approvals else "read_only"
        redaction = (self.bundle.redaction.get("enabled", True) if self.bundle else True)
        return (f"ACTOR LANES  {lane_text}    STATE DIFFS  {state_versions}    TOOL CALLS  {calls}    "
                f"FAULTS  {faults}    FAILURES  {failures}    RESULT  {result}\n"
                f"POLICY  {authority}    APPROVALS  {len(approvals)}    REDACTION  {'on' if redaction else 'off'}    "
                f"SENSITIVE ARTIFACTS  metadata-only    {comparison}    [r] replay  [m] minimized repro")

    @staticmethod
    def _event_counts(trace: dict) -> Counter[tuple[str, str, str, str]]:
        return Counter(
            (
                event.get("actor", "system"),
                event["type"],
                event.get("name") or "",
                (event.get("data", {}).get("result") or {}).get("code", ""),
            )
            for event in trace.get("events", [])
            if event["type"] not in {"tool.discovered", "tool.change"}
        )

    def _comparison_counts(self) -> tuple[int, int]:
        if not self.compare_trace:
            return (0, 0)
        current = event_counts(self.trace)
        baseline = event_counts(self.compare_trace)
        return (sum((current - baseline).values()), sum((baseline - current).values()))

    def _item(self, index: int, event: dict) -> ListItem:
        result = event.get("data", {}).get("result")
        code = result.get("code", "") if isinstance(result, dict) else ""
        fault = "fault" in (event.get("name") or "").lower() or "fault" in event.get("type", "")
        failed = code in {"STALE_STATE", "ERROR"} or event.get("type") in FAILURE_EVENTS
        style = "bad" if failed else "warn" if fault else "ok" if code == "OK" else "warn"
        marker = "⚠ FAULT " if fault else "✕ FAIL " if failed else "      "
        text = f"{marker}+{event['timestamp_ms']:>5}ms  {event['actor']:<10}  {event['type']:<23} {event.get('name') or ''} {code}"
        return ListItem(Label(text, classes=style), id=f"event-{index}")

    def on_list_view_selected(self, message: ListView.Selected) -> None:
        index = int(message.item.id.split("-")[-1])
        event = self.events[index]
        detail = json.dumps({"event": event, "state_diff": event_state_diff(self.events, index)}, indent=2, default=str)
        self.query_one("#detail", Static).update(detail)

    def action_first(self) -> None:
        self.query_one("#events", ListView).index = 0

    def action_last(self) -> None:
        self.query_one("#events", ListView).index = len(self.events) - 1

    def action_replay(self) -> None:
        if self.bundle_path is None:
            self._set_detail("This is a view-only diagnostic trace. Open a current bundle.json to replay it.")
            return
        self.run_replay()

    def action_compare(self) -> None:
        if not self.compare_trace:
            self._set_detail("No comparison trace loaded. Reopen with --compare BASELINE.json")
            return
        current = event_counts(self.trace)
        baseline = event_counts(self.compare_trace)
        added = current - baseline
        removed = baseline - current
        lines = [f"Bundle comparison: current {self.trace_path.name} vs baseline {self.compare_path.name}", ""]
        if self.bundle and self.compare_bundle:
            lines.append(json_text(bundle_comparison(self.bundle, self.compare_bundle)) + "")
        lines += [f"+ {count} × {actor} · {event_type} · {name} {code}" for (actor, event_type, name, code), count in added.items()]
        lines += [f"- {count} × {actor} · {event_type} · {name} {code}" for (actor, event_type, name, code), count in removed.items()]
        self._set_detail("\n".join(lines) if len(lines) > 2 else "Traces have the same actor/event counts.")

    def action_repro(self) -> None:
        if self.bundle is None or self.bundle_path is None:
            self._set_detail("Minimized reproduction view requires a portable run bundle.")
            return
        rows = []
        for artifact in self.bundle.artifacts:
            if artifact.kind not in {"failure", "scenario"}:
                continue
            status = "retrievable redacted text" if artifact.redacted and artifact.sensitivity == "redacted" else "metadata only"
            rows.append({"kind": artifact.kind, "sensitivity": artifact.sensitivity, "redacted": artifact.redacted, "access": status, "description": artifact.description})
        content = next((safe_artifact_text(self.bundle_path, self.bundle, kind) for kind in ("scenario", "failure") if safe_artifact_text(self.bundle_path, self.bundle, kind)), None)
        self._set_detail("MINIMIZED REPRO / FAILURE\n\n" + json_text({"artifacts": rows, "content": content or "[no safe textual repro available]"}))

    def _set_detail(self, value: str) -> None:
        self.query_one("#detail", Static).update(value)

    async def _replace_trace(self, trace_path: Path, replay_output: str) -> None:
        self._load_trace(trace_path)
        events = self.query_one("#events", ListView)
        await events.clear()
        for index, event in enumerate(self.events):
            await events.append(self._item(index, event))
        self.query_one("#summary", Static).update(self._summary())
        self._set_detail(f"Replay passed. Loaded fresh trace: {trace_path.name}\n\n{replay_output}")

    async def _replace_bundle(self, bundle_path: Path, replay_output: str) -> None:
        self._load_input(bundle_path)
        events = self.query_one("#events", ListView)
        await events.clear()
        for index, event in enumerate(self.events):
            await events.append(self._item(index, event))
        self.query_one("#summary", Static).update(self._summary())
        self._set_detail(f"Replay passed. Loaded portable bundle: {bundle_path.name}\n\n{replay_output}")

    @work(thread=True, exclusive=True)
    def run_replay(self) -> None:
        """Replay the current run bundle through the same portable CLI contract."""
        assert self.bundle_path is not None
        command = [sys.executable, "-m", "webmcp_resilience.cli", "replay", str(self.bundle_path), "--ci", "--json"]
        if self.replay_allow_mutations:
            command.append("--allow-mutations")
        self.call_from_thread(self._set_detail, f"Replaying {self.bundle_path.name}…\n$ {' '.join(command)}")
        completed = subprocess.run(
            command,
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
        output = (completed.stdout + completed.stderr).strip() or "Replay finished without console output."
        output = str(redact_recursive(output))
        try:
            payload = json.loads(completed.stdout)
            fresh_bundle = Path(payload["output"])
            record = RunBundle.model_validate_json(fresh_bundle.read_text())
        except (json.JSONDecodeError, KeyError, StopIteration, OSError, ValueError) as error:
            output = f"{output}\n\nCould not load replay bundle trace: {error}"
            record = None
        if completed.returncode == 0 and record is not None:
            self.bundle_path = fresh_bundle.resolve()
            self.call_from_thread(self._replace_bundle, fresh_bundle, output)
        else:
            self.call_from_thread(self._set_detail, f"Replay failed (exit {completed.returncode})\n\n{output}")


def run_console(trace_path: Path, replay_allow_mutations: bool = False,
                compare_path: Path | None = None) -> None:
    TraceConsole(trace_path, replay_allow_mutations, compare_path).run()


class ExperimentConsole(App[None]):
    """Terminal scenario editor; it emits declarations, never executes them."""
    CSS = """
    Screen { background: #101722; color: #eaf2f3; }
    Header, Footer { background: #1e2b3a; color: #3bd4d1; }
    #form { width: 72; height: auto; margin: 2 4; border: solid #385063; background: #1e2b3a; padding: 1 2; }
    #form { width: 58%; }
    #evidence { width: 42%; height: auto; margin: 2 2 2 0; border: solid #385063; background: #162131; padding: 1 2; }
    Input { margin: 1 0; } Button { margin: 1 0; background: #3bd4d1; color: #101722; }
    #status { color: #ffcf8f; margin-top: 1; }
    #generated-yaml, #operation { color: #c4e6e4; margin-top: 1; }
    """

    def __init__(self, discovery_path: Path | None = None, project_root: Path | None = None) -> None:
        super().__init__()
        self.discovery_path = discovery_path.resolve() if discovery_path else None
        self.project_root = (project_root or Path.cwd()).resolve()
        self.editor = ScenarioEditorModel.from_bundle(self.discovery_path)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="composer"):
            with VerticalScroll(id="form"):
                yield Label("Compose a portable WebMCP experiment")
                yield Input(placeholder="Scenario name", id="name")
                yield Input(value="agent", placeholder="Actor", id="actor")
                yield Input(value="0ms", placeholder="Timing", id="at")
                yield Input(placeholder="Discovered tool name", id="tool")
                yield Input(value="{}", placeholder="JSON arguments", id="args")
                yield Input(placeholder="Optional state tool, e.g. get_bench_snapshot", id="state-tool")
                yield Input(placeholder="Optional result invariant", id="result-invariant")
                yield Input(placeholder="Optional fault label (metadata only)", id="fault-label")
                yield Button("Save scenario", id="save", variant="success")
                yield Static("Policy: declaration only. Mutation authority remains with CLI or AgentPolicy.", id="status")
            with VerticalScroll(id="evidence"):
                yield Label("DISCOVERED CAPABILITIES")
                yield Static(self._capability_text(), id="capabilities")
                yield Label("TOOL SCHEMA")
                yield Static("Enter a discovered tool to inspect its schema.", id="tool-schema")
                yield Label("GENERATED YAML / OPERATIONS")
                yield Static("Save to preview the exact CLI and MCP-equivalent operations.", id="generated-yaml")
                yield Static("", id="operation")
        yield Footer()

    def _capability_text(self) -> str:
        tools = ", ".join(self.editor.tool_names) or "none discovered (run preflight first)"
        capabilities = self.editor.capabilities
        return (f"tools: {tools}\nactors: {', '.join(capabilities.get('actors', ['agent', 'human', 'system']))}\n"
                f"actions: {', '.join(capabilities.get('actions') or ['invoke', 'retry', 'cancel', 'navigate'])}\n"
                f"browser mode: {capabilities.get('browser_mode') or 'unknown'}\n"
                f"redaction: safe metadata only; sensitive artifacts blocked")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "tool":
            return
        self.query_one("#tool-schema", Static).update(self.editor.describe_tool(event.value.strip()))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save": return
        try:
            name = self.query_one("#name", Input).value.strip()
            actor = self.query_one("#actor", Input).value.strip() or "agent"
            tool = self.query_one("#tool", Input).value.strip()
            if not name or not tool: raise ValueError("Scenario name and tool name are required.")
            args = json.loads(self.query_one("#args", Input).value or "{}")
            scenario = self.editor.build_scenario(
                name=name,
                actor=actor,
                at=self.query_one("#at", Input).value,
                tool=tool,
                args=args,
                state_tool=self.query_one("#state-tool", Input).value.strip(),
                invariant=self.query_one("#result-invariant", Input).value.strip(),
                fault_label=self.query_one("#fault-label", Input).value.strip(),
            )
            path = Path(".webmcp/scenarios") / f"{name}.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.editor.yaml_text(scenario))
            operations = self.editor.cli_operations(path, self.project_root)
            mcp = self.editor.mcp_operations(path, self.project_root)
            self.query_one("#generated-yaml", Static).update(self.editor.yaml_text(scenario))
            self.query_one("#operation", Static).update(
                f"CLI validate: {operations['validate']}\nCLI run: {operations['run']}\n"
                f"MCP validate_scenario: {json_text(mcp['validate'])}\nMCP run_scenario: {json_text(mcp['run'])}"
            )
            self.query_one("#status", Static).update(f"Saved {path}. Declaration unchanged for CLI and MCP.")
        except (ValueError, json.JSONDecodeError) as error:
            self.query_one("#status", Static).update(f"Cannot save: {error}")


def run_composer(discovery_path: Path | None = None) -> None:
    ExperimentConsole(discovery_path=discovery_path).run()
