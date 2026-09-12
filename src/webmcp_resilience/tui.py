import json
import asyncio
from pathlib import Path
from collections import Counter
from typing import Any

import yaml

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual import work
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from .models.bundle import RunBundle, redact_recursive
from .commands import CommandAPI, CommandError
from .config import Config, load_config
from .console_commands import ConsoleCommandModule, ReplayOutcome
from .console_client import (
    FAILURE_EVENTS,
    ScenarioEditorModel,
    actor_lanes,
    bundle_comparison,
    bundle_trace,
    event_counts,
    event_state_diff,
    filter_events,
    json_text,
    load_bundle,
    RunHistory,
    safe_artifact_text,
    timeline_rows,
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
        ("h", "handoff", "Export handoff"),
        ("a", "filter_actor", "Actor filter"),
        ("f", "toggle_faults", "Faults"),
        ("x", "toggle_failures", "Failures"),
        ("s", "toggle_state_changes", "State changes"),
    ]

    def __init__(self, trace_path: Path, command_module: ConsoleCommandModule | None = None,
                 compare_path: Path | None = None) -> None:
        super().__init__()
        self.trace_path = trace_path.resolve()
        self.bundle_path: Path | None = None
        self.bundle: RunBundle | None = None
        self.command_module = command_module or ConsoleCommandModule(
            CommandAPI(load_config() if Path(".webmcp/config.yaml").exists() else Config(), output_dir=Path(".webmcp/runs")), replay_allow_mutations=False
        )
        self.compare_path = compare_path.resolve() if compare_path else None
        self.compare_bundle: RunBundle | None = load_bundle(self.compare_path) if self.compare_path else None
        self.compare_trace = self._trace_for_bundle(self.compare_path, self.compare_bundle) if self.compare_bundle else (redact_recursive(json.loads(compare_path.read_text())) if compare_path else None)
        self.filters: dict[str, Any] = {"faults": False, "failures": False, "state_changes": False}
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
        actor_text = ", ".join(f"{actor}({count})" for actor, count in actors.items()) or "no actor events"
        comparison = "    [c] no comparison"
        if self.compare_trace:
            added, removed = self._comparison_counts()
            comparison = f"    COMPARE  +{added}/-{removed} events  [c] details"
        result = "PASS" if self.bundle is None or self.bundle.result.get("passed", True) else "FAIL"
        approvals = self.bundle.approvals if self.bundle else []
        authority = approvals[-1].authority if approvals else "read_only"
        redaction = (self.bundle.redaction.get("enabled", True) if self.bundle else True)
        filter_text = ", ".join(key for key, value in self.filters.items() if value) or "none"
        return (f"ACTOR EVENTS  {actor_text}    STATE DIFFS  {state_versions}    TOOL CALLS  {calls}    "
                f"FAULTS  {faults}    FAILURES  {failures}    RESULT  {result}\n"
                f"POLICY  {authority}    APPROVALS  {len(approvals)}    REDACTION  {'on' if redaction else 'off'}    "
                f"SENSITIVE ARTIFACTS  metadata-only    FILTERS  {filter_text}    {comparison}    [r] replay  [m] minimized repro")

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
        text = f"{marker}+{event['timestamp_ms']:>5}ms  [{event['actor']:<10}]  {event['type']:<23} {event.get('name') or ''} {code}"
        return ListItem(Label(text, classes=style), id=f"event-{index}")

    def _toggle_filter(self, name: str) -> None:
        self.filters[name] = not self.filters[name]
        self.events = filter_events(self.trace, **self.filters)
        self.run_worker(self._refresh_event_list(), exclusive=True)
        self.query_one("#summary", Static).update(self._summary())

    async def _refresh_event_list(self) -> None:
        events = self.query_one("#events", ListView)
        await events.clear()
        for index, event in enumerate(self.events):
            await events.append(self._item(index, event))

    def action_filter_actor(self) -> None:
        actors = sorted({event.get("actor", "system") for event in self.events})
        if actors:
            self.filters["actor"] = actors[0] if self.filters.get("actor") != actors[0] else None
            self.events = filter_events(self.trace, **self.filters)
            self.run_worker(self._refresh_event_list(), exclusive=True)
            self.query_one("#summary", Static).update(self._summary())

    def action_toggle_faults(self) -> None:
        self._toggle_filter("faults")

    def action_toggle_failures(self) -> None:
        self._toggle_filter("failures")

    def action_toggle_state_changes(self) -> None:
        self._toggle_filter("state_changes")

    def on_list_view_selected(self, message: ListView.Selected) -> None:
        index = int(message.item.id.split("-")[-1])
        event = self.events[index]
        name = event.get("name")
        links = {
            "declared_actions": [action for action in (self.bundle.actions if self.bundle else []) if action.get("invoke") == name or action.get("retry") == name or action.get("cancel") == name],
            "faults": [fault for fault in (self.bundle.faults if self.bundle else []) if fault.get("tool") in {None, name}],
            "safe_artifact_metadata": [{"kind": artifact.kind, "sensitivity": artifact.sensitivity, "redacted": artifact.redacted, "description": artifact.description} for artifact in (self.bundle.artifacts if self.bundle else [])],
        }
        detail = json.dumps({"event": event, "state_diff": event_state_diff(self.events, index), "links": links}, indent=2, default=str)
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

    def action_handoff(self) -> None:
        if self.bundle_path is None:
            self._set_detail("Handoff export requires a portable run bundle.")
            return
        outputs = self.command_module.export_handoff(self.bundle_path)
        self._set_detail(f"Safe handoff exported\nMarkdown: {outputs['markdown']}\nJSON: {outputs['json']}")

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

    async def _replace_bundle(self, bundle_path: Path, outcome: ReplayOutcome) -> None:
        self._load_input(bundle_path)
        events = self.query_one("#events", ListView)
        await events.clear()
        for index, event in enumerate(self.events):
            await events.append(self._item(index, event))
        self.query_one("#summary", Static).update(self._summary())
        details = [outcome.message, f"Loaded fresh replay bundle: {bundle_path}"]
        if outcome.invariant:
            details.append(f"Invariant: {outcome.invariant}")
            details.append(f"Observed state: {json_text(outcome.observed_state or {})}")
        if outcome.replay_command:
            details.append(f"Replay: {' '.join(outcome.replay_command)}")
        self._set_detail("\n".join(details))

    @work(thread=True, exclusive=True)
    def run_replay(self) -> None:
        """Replay through the injected CommandAPI console module."""
        assert self.bundle_path is not None
        self.call_from_thread(self._set_detail, f"Replaying {self.bundle_path.name} through CommandAPI…")
        try:
            outcome = asyncio.run(self.command_module.replay(self.bundle_path, headless=True))
        except Exception as error:
            safe_error = str(redact_recursive(str(error)))
            self.call_from_thread(self._set_detail, f"Replay execution/contract failure\n\n{safe_error}")
            return
        if outcome.bundle is not None and outcome.fresh_path is not None:
            self.call_from_thread(self._replace_bundle, outcome.fresh_path, outcome)
        else:
            details = [outcome.message]
            if outcome.error:
                details.append(str(redact_recursive(outcome.error)))
            self.call_from_thread(self._set_detail, "\n\n".join(details))


def run_console(trace_path: Path, command_module: ConsoleCommandModule | None = None,
                compare_path: Path | None = None) -> None:
    TraceConsole(trace_path, command_module, compare_path).run()


class HistoryConsole(App[None]):
    """Read-only run history and baseline workspace."""
    CSS = TraceConsole.CSS + """
    #history { width: 58%; border: solid #385063; background: #162131; }
    #history-detail { width: 42%; margin-left: 1; border: solid #385063; background: #1e2b3a; padding: 1 2; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"), ("b", "baseline", "Pin baseline"),
        ("t", "trace", "Open trace"), ("r", "replay", "Replay"),
        ("c", "compare", "Compare"), ("m", "repro", "Min repro"), ("h", "handoff", "Export handoff"),
    ]

    def __init__(self, runs_root: Path, command_module: ConsoleCommandModule | None = None) -> None:
        super().__init__()
        self.history = RunHistory(runs_root)
        self.command_module = command_module or ConsoleCommandModule(
            CommandAPI(load_config() if (runs_root.parent / "config.yaml").exists() else Config(), output_dir=runs_root), replay_allow_mutations=False
        )
        self.entries = self.history.entries()
        self.selected: int | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            yield ListView(*[self._item(i, entry) for i, entry in enumerate(self.entries)], id="history")
            yield Static("Select a saved portable bundle. Imported evidence is redacted at the boundary.", id="history-detail")
        yield Footer()

    @staticmethod
    def _item(index: int, entry: Any) -> ListItem:
        marker = "✓" if entry.status == "passed" else "✕" if entry.status == "failed" else "?"
        artifacts = ",".join(entry.artifact_kinds) or "none"
        text = f"{marker} {entry.run_id:<24} {entry.scenario or '-':<22} {entry.status:<7} seed={entry.seed} artifacts={artifacts}"
        return ListItem(Label(text), id=f"history-{index}")

    def on_list_view_selected(self, message: ListView.Selected) -> None:
        self.selected = int(message.item.id.split("-")[-1])
        entry = self.entries[self.selected]
        self._set_detail(json_text({
            "run_id": entry.run_id, "scenario": entry.scenario, "status": entry.status,
            "created_at": entry.created_at, "seed": entry.seed,
            "browser_fingerprint": entry.browser_fingerprint,
            "mutation_authority": entry.mutation_authority,
            "artifact_availability": entry.artifact_availability, "bundle": str(entry.path),
        }))

    def _entry(self) -> Any | None:
        return self.entries[self.selected] if self.selected is not None and self.selected < len(self.entries) else None

    def _set_detail(self, text: str) -> None:
        self.query_one("#history-detail", Static).update(text)

    def action_baseline(self) -> None:
        entry = self._entry()
        if entry is None:
            self._set_detail("Select a bundle first.")
            return
        self.history.pin_baseline(entry.path)
        self._set_detail(f"Pinned baseline: {entry.path}")

    def action_trace(self) -> None:
        entry = self._entry()
        bundle = load_bundle(entry.path) if entry else None
        self._set_detail(json_text(timeline_rows(bundle_trace(bundle))) if bundle else "Select a bundle first.")

    def action_compare(self) -> None:
        entry = self._entry()
        if entry is None or self.history.baseline_path is None:
            self._set_detail("Pin a baseline with [b], then select a bundle to compare.")
            return
        current = load_bundle(entry.path)
        baseline = load_bundle(self.history.baseline_path)
        if current is None or baseline is None:
            self._set_detail("Selected bundle is not a valid portable bundle.")
            return
        self._set_detail(json_text(bundle_comparison(current, baseline)))

    def action_repro(self) -> None:
        entry = self._entry()
        bundle = load_bundle(entry.path) if entry else None
        if entry is None or bundle is None:
            self._set_detail("Select a bundle first.")
            return
        self._set_detail(json_text({
            "bundle": str(entry.path),
            "safe_repro": safe_artifact_text(entry.path, bundle, "scenario"),
            "artifact_metadata": [{"kind": item.kind, "sensitivity": item.sensitivity, "redacted": item.redacted, "description": item.description} for item in bundle.artifacts],
        }))

    def action_handoff(self) -> None:
        entry = self._entry()
        if entry is None:
            self._set_detail("Select a bundle first.")
            return
        outputs = self.command_module.export_handoff(entry.path)
        self._set_detail(f"Safe handoff exported\nMarkdown: {outputs['markdown']}\nJSON: {outputs['json']}")

    @work(thread=True, exclusive=True)
    def run_replay(self) -> None:
        entry = self._entry()
        if entry is None:
            self.call_from_thread(self._set_detail, "Select a bundle first.")
            return
        try:
            outcome = asyncio.run(self.command_module.replay(entry.path))
            self.call_from_thread(self._set_detail, f"{outcome.message}\nFresh bundle: {outcome.fresh_path}")
        except Exception as error:
            self.call_from_thread(self._set_detail, f"Replay execution/contract failure\n{redact_recursive(str(error))}")

    def action_replay(self) -> None:
        self.run_replay()


def run_history(
    runs_root: Path = Path(".webmcp/runs"),
    command_module: ConsoleCommandModule | None = None,
) -> None:
    """Launch history with the CLI-owned command module when provided."""
    HistoryConsole(runs_root, command_module=command_module).run()


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

    def __init__(self, discovery_path: Path | None = None, project_root: Path | None = None,
                 command_module: ConsoleCommandModule | None = None) -> None:
        super().__init__()
        self.discovery_path = discovery_path.resolve() if discovery_path else None
        self.project_root = (project_root or Path.cwd()).resolve()
        self.command_module = command_module or ConsoleCommandModule(
            CommandAPI(load_config(self.project_root / ".webmcp") if (self.project_root / ".webmcp" / "config.yaml").exists() else Config(), output_dir=self.project_root / ".webmcp" / "runs"), replay_allow_mutations=False
        )
        self.editor = ScenarioEditorModel.from_bundle(self.discovery_path)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="composer"):
            with VerticalScroll(id="form"):
                yield Label("Compose a portable WebMCP experiment")
                yield Input(placeholder="Scenario name", id="name")
                yield Input(placeholder='Actors JSON: {"agent":[...],"human":[...]}', id="actors-json")
                yield Input(placeholder="Optional ordered action list JSON", id="actions-json")
                yield Input(value="agent", placeholder="Actor", id="actor")
                yield Input(value="0ms", placeholder="Timing", id="at")
                yield Input(placeholder="Discovered tool name", id="tool")
                yield Input(value="{}", placeholder="JSON arguments", id="args")
                yield Input(placeholder="Optional state tool, e.g. get_bench_snapshot", id="state-tool")
                yield Input(placeholder='State invariants JSON, e.g. ["count <= 1"]', id="invariants")
                yield Input(placeholder='Result invariants JSON, e.g. ["OK == OK"]', id="result-invariants")
                yield Input(placeholder='Executable faults JSON, e.g. [{"type":"latency","duration_ms":100,"at":"before_invoke"}]', id="faults")
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
                f"read-only state tools: {', '.join(self.editor.read_only_state_tools) or 'none discovered'}\n"
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
            if not name: raise ValueError("Scenario name is required.")
            if name in {".", ".."} or Path(name).name != name:
                raise ValueError("Scenario name must be a single path-safe name.")
            args = json.loads(self.query_one("#args", Input).value or "{}")
            actors_text = self.query_one("#actors-json", Input).value.strip()
            actions_text = self.query_one("#actions-json", Input).value.strip()
            actors = json.loads(actors_text) if actors_text else None
            actions = json.loads(actions_text) if actions_text else None
            if not tool and actors is None and actions is None:
                raise ValueError("Provide a tool or actors/actions JSON.")
            invariants = json.loads(self.query_one("#invariants", Input).value or "[]")
            result_invariants = json.loads(self.query_one("#result-invariants", Input).value or "[]")
            faults = json.loads(self.query_one("#faults", Input).value or "[]")
            if not isinstance(invariants, list) or not isinstance(result_invariants, list) or not isinstance(faults, list):
                raise ValueError("invariants, result invariants, and faults must be JSON arrays")
            if actors is not None and not isinstance(actors, dict):
                raise ValueError("actors must be a JSON object keyed by actor")
            if actions is not None and not isinstance(actions, list):
                raise ValueError("actions must be a JSON array")
            scenario = self.editor.build_scenario(
                name=name,
                actor=actor,
                at=self.query_one("#at", Input).value,
                tool=tool,
                args=args,
                actors=actors,
                actions=actions,
                state_tool=self.query_one("#state-tool", Input).value.strip(),
                invariants=invariants,
                result_invariants=result_invariants,
                faults=faults,
            )
            self.command_module.validate_scenario(scenario, discovered_tools=self.editor.tool_inventory or None)
            path = self.project_root / ".webmcp" / "scenarios" / f"{name}.yaml"
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
        except (ValueError, json.JSONDecodeError, CommandError) as error:
            self.query_one("#status", Static).update(f"Cannot save: {error}")


def run_composer(discovery_path: Path | None = None) -> None:
    ExperimentConsole(discovery_path=discovery_path).run()
