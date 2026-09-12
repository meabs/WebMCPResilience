"""Pure, bundle-aware helpers used by the terminal console.

This module deliberately contains presentation and request construction only.
Execution remains in :class:`CommandAPI` (or the CLI/MCP adapters that call it).
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .commands import diff_bundles
from .console_commands import ConsoleCommandModule as ConsoleCommandClient
from .models.bundle import RunBundle, SUPPORTED_COMPATIBILITY_GROUPS, redact_recursive


HIDDEN_TRACE_EVENTS = {"tool.discovered", "tool.change"}
FAILURE_EVENTS = {"tool.error", "invariant.fail", "result_invariant.fail", "tool_contract.assertion.fail"}


def load_bundle(path: Path) -> RunBundle | None:
    """Load a portable bundle, returning ``None`` for a legacy bare trace."""
    try:
        imported = RunBundle.model_validate(json.loads(path.read_text()))
        return RunBundle.model_validate(imported.persisted_dict())
    except Exception:
        return None


def bundle_trace(bundle: RunBundle) -> dict[str, Any]:
    if bundle.trace is None:
        return {"scenario": (bundle.scenario or {}).get("name", bundle.run_id), "events": []}
    return redact_recursive(bundle.trace.model_dump(mode="json"))


def visible_events(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [event for event in trace.get("events", []) if event.get("type") not in HIDDEN_TRACE_EVENTS]


def event_counts(trace: dict[str, Any]) -> Counter[tuple[str, str, str, str]]:
    return Counter(
        (
            event.get("actor", "system"),
            event.get("type", ""),
            event.get("name") or "",
            (event.get("data", {}).get("result") or {}).get("code", ""),
        )
        for event in visible_events(trace)
    )


def state_diff(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic nested state diff suitable for a detail pane."""
    added = {key: current[key] for key in sorted(set(current) - set(previous))}
    removed = {key: previous[key] for key in sorted(set(previous) - set(current))}
    changed: dict[str, Any] = {}
    for key in sorted(set(previous) & set(current)):
        before, after = previous[key], current[key]
        if isinstance(before, dict) and isinstance(after, dict):
            nested = state_diff(before, after)
            if any(nested.values()):
                changed[key] = nested
        elif before != after:
            changed[key] = {"before": before, "after": after}
    return {"added": added, "removed": removed, "changed": changed}


def event_state_diff(events: list[dict[str, Any]], index: int) -> dict[str, Any]:
    current = events[index].get("state_snapshot") or {}
    if not current:
        return {"added": {}, "removed": {}, "changed": {}}
    previous = next(
        (event.get("state_snapshot") or {} for event in reversed(events[:index]) if event.get("state_snapshot")),
        {},
    )
    return state_diff(previous, current)


def actor_lanes(trace: dict[str, Any]) -> dict[str, int]:
    counts = Counter(event.get("actor", "system") for event in visible_events(trace))
    return dict(sorted(counts.items()))


def filter_events(trace: dict[str, Any], *, actor: str | None = None,
                  event_type: str | None = None, tool: str | None = None,
                  faults: bool = False, failures: bool = False,
                  state_changes: bool = False) -> list[dict[str, Any]]:
    """Apply flight-deck filters without exposing hidden discovery events."""
    events = visible_events(trace)
    if actor:
        events = [event for event in events if event.get("actor") == actor]
    if event_type:
        events = [event for event in events if event.get("type") == event_type]
    if tool:
        events = [event for event in events if event.get("name") == tool or event.get("data", {}).get("tool") == tool]
    if faults:
        events = [event for event in events if "fault" in event.get("type", "") or "fault" in (event.get("name") or "").lower()]
    if failures:
        events = [event for event in events if event.get("type") in FAILURE_EVENTS or (event.get("data", {}).get("result") or {}).get("code") in {"ERROR", "STALE_STATE"}]
    if state_changes:
        events = [event for event in events if event.get("type") == "state.observed" or event.get("state_snapshot")]
    return events


def timeline_rows(trace: dict[str, Any], **filters: Any) -> list[dict[str, Any]]:
    """Project events into a stable actor swim-lane representation."""
    rows = []
    for index, event in enumerate(filter_events(trace, **filters)):
        rows.append({
            "index": index,
            "lane": event.get("actor", "system"),
            "timestamp_ms": event.get("timestamp_ms", 0),
            "event_type": event.get("type", ""),
            "tool": event.get("name"),
            "fault": "fault" in event.get("type", "") or "fault" in (event.get("name") or "").lower(),
            "failure": event.get("type") in FAILURE_EVENTS,
            "state_change": event.get("type") == "state.observed" or bool(event.get("state_snapshot")),
            "event": event,
        })
    return rows


@dataclass(frozen=True)
class RunHistoryEntry:
    path: Path
    run_id: str
    scenario: str | None
    status: str
    created_at: str
    seed: int | None
    browser_fingerprint: str | None
    tool_inventory_fingerprint: str | None
    tool_contract_drift_status: str
    mutation_authority: str
    artifact_kinds: tuple[str, ...]
    artifact_availability: dict[str, bool]


class RunHistory:
    """Read-only browser of repository-local portable bundles."""

    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root.resolve()
        self.baseline_path: Path | None = None

    def entries(self) -> list[RunHistoryEntry]:
        entries: list[RunHistoryEntry] = []
        if not self.runs_root.is_dir():
            return entries
        for path in sorted(self.runs_root.glob("*/bundle.json")):
            try:
                path.resolve().relative_to(self.runs_root)
                bundle = load_bundle(path)
            except (OSError, ValueError):
                continue
            if bundle is None:
                continue
            approvals = bundle.approvals
            authority = approvals[-1].authority if approvals else str(bundle.agent_policy.get("mutation_authority", "read_only"))
            entries.append(RunHistoryEntry(
                path=path.resolve(), run_id=bundle.run_id,
                scenario=(bundle.scenario or {}).get("name"),
                status="passed" if bundle.result.get("passed") is True else "failed" if bundle.result.get("passed") is False else "unknown",
                created_at=bundle.created_at.isoformat(),
                seed=bundle.execution.get("seed"),
                browser_fingerprint=bundle.compatibility.capability_fingerprint,
                tool_inventory_fingerprint=bundle.compatibility.tool_inventory_fingerprint,
                tool_contract_drift_status=str(bundle.tool_contract_drift.get("status", "not_compared")),
                mutation_authority=authority,
                artifact_kinds=tuple(sorted({item.kind for item in bundle.artifacts})),
                artifact_availability={item.kind: True for item in bundle.artifacts},
            ))
        return sorted(entries, key=lambda item: (item.created_at, item.run_id), reverse=True)

    def pin_baseline(self, path: Path) -> Path:
        candidate = path.resolve()
        candidate.relative_to(self.runs_root)
        if candidate.name != "bundle.json" or not candidate.is_file():
            raise ValueError("baseline must be a saved bundle.json")
        if load_bundle(candidate) is None:
            raise ValueError("baseline is not a valid portable bundle")
        self.baseline_path = candidate
        return candidate


def bundle_comparison(left: RunBundle, right: RunBundle) -> dict[str, Any]:
    """Use the same structural diff contract as the CLI ``diff`` command."""
    return diff_bundles(left, right)


def safe_artifact_text(bundle_path: Path, bundle: RunBundle, kind: str) -> str | None:
    """Read only redacted textual failure/repro artifacts.

    Screenshots, network files, and artifacts marked potentially sensitive are
    intentionally never opened by the console.
    """
    if kind not in {"failure", "scenario"}:
        return None
    artifact = next(
        (
            item for item in bundle.artifacts
            if item.kind == kind and item.redacted and item.sensitivity == "redacted"
        ),
        None,
    )
    if artifact is None:
        return None
    path = Path(artifact.path)
    if not path.is_absolute():
        path = bundle_path.parent / path
    try:
        path = path.resolve()
        path.relative_to(bundle_path.parent.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    contents = path.read_text()
    try:
        parsed = yaml.safe_load(contents)
    except Exception:
        parsed = None
    if isinstance(parsed, (dict, list)):
        return yaml.safe_dump(redact_recursive(parsed), sort_keys=False)
    return str(redact_recursive(contents))


class ScenarioEditorModel:
    """Schema/capability projection for the console's ordinary YAML editor."""

    def __init__(self, tool_inventory: list[dict[str, Any]] | None = None,
                 capabilities: dict[str, Any] | None = None) -> None:
        self.tool_inventory = [item for item in (tool_inventory or []) if isinstance(item, dict)]
        self.capabilities = capabilities or {}

    @classmethod
    def from_bundle(cls, path: Path | None) -> "ScenarioEditorModel":
        if path is None:
            return cls()
        bundle = load_bundle(path)
        if bundle is None:
            return cls()
        # CommandAPI stores the browser-native report directly in preflight;
        # accept the nested spelling for older discovery bundles as well.
        report = bundle.preflight.get("compatibility_report", bundle.preflight)
        capabilities = {
            "actors": (bundle.preflight.get("capabilities") or {}).get("actors", ["agent", "human", "system"]),
            "actions": (bundle.preflight.get("capabilities") or {}).get("actions", ["invoke", "retry", "cancel", "click", "fill", "select", "navigate", "wait"]),
            "invariant_operators": ["<", "<=", "==", "!=", ">", ">="],
            "browser_mode": report.get("webmcp", {}).get("native_mode"),
            "report_status": report.get("summary", {}).get("status"),
        }
        return cls(bundle.tool_inventory, capabilities)

    @property
    def tool_names(self) -> list[str]:
        return sorted({str(item["name"]) for item in self.tool_inventory if item.get("name")})

    @property
    def read_only_state_tools(self) -> list[str]:
        return sorted({
            str(item["name"]) for item in self.tool_inventory
            if item.get("name") and (item.get("annotations") or {}).get("readOnlyHint") is True
        })

    def schema_for(self, name: str) -> dict[str, Any] | None:
        for tool in self.tool_inventory:
            if tool.get("name") == name:
                return tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else None
        return None

    def describe_tool(self, name: str) -> str:
        schema = self.schema_for(name)
        if schema is None:
            return "Tool is not in the discovered inventory; validate will reject unknown tools when inventory is enforced."
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        return f"schema object · fields: {', '.join(sorted(properties)) or 'none'} · required: {', '.join(required) or 'none'}"

    def build_scenario(self, *, name: str, actor: str = "agent", at: str = "0ms", tool: str = "",
                       args: dict[str, Any] | None = None, state_tool: str = "",
                       invariant: str = "", invariants: list[str] | None = None,
                       result_invariants: list[str] | None = None,
                       faults: list[dict[str, Any] | str] | None = None,
                       actors: dict[str, list[dict[str, Any]]] | None = None,
                       actions: list[dict[str, Any]] | None = None,
                       fault_label: str = "") -> dict[str, Any]:
        if fault_label:
            raise ValueError("metadata-only fault labels are not supported; declare an executable fault")
        if actors is not None and actions is not None:
            raise ValueError("provide either actors JSON or ordered actions JSON, not both")
        if actors is None:
            actor_actions = actions or [{"at": at or "0ms", "invoke": tool, "args": args or {}}]
            actors = {actor or "agent": actor_actions}
        scenario: dict[str, Any] = {
            "name": name,
            "actors": actors,
            "compatibility": {
                "schema": "webmcp-resilience/scenario-1",
                "requires": dict(SUPPORTED_COMPATIBILITY_GROUPS),
            },
        }
        if state_tool:
            scenario["state"] = {"tool": state_tool}
        declared_invariants = list(invariants or [])
        if invariant:
            declared_invariants.append(invariant)
        if declared_invariants:
            scenario["invariants"] = declared_invariants
        if result_invariants:
            scenario["result_invariants"] = list(result_invariants)
        if faults:
            scenario["faults"] = faults
        return scenario

    @staticmethod
    def cli_operations(path: Path, project_root: Path = Path.cwd()) -> dict[str, str]:
        relative = path.resolve().relative_to(project_root.resolve()).as_posix()
        return {
            "validate": f"webmcp validate {relative} --json",
            "run": f"webmcp run {relative} --ci --json",
            "replay": f"webmcp replay .webmcp/runs/<run-id>/bundle.json --json",
        }

    @staticmethod
    def mcp_operations(path: Path, project_root: Path = Path.cwd()) -> dict[str, dict[str, Any]]:
        relative = path.resolve().relative_to(project_root.resolve()).as_posix()
        return {
            "validate": {"tool": "validate_scenario", "arguments": {"scenario_id": relative}},
            "run": {"tool": "run_scenario", "arguments": {"scenario_id": relative, "adversarial": False, "seed": 0}},
            "replay": {"tool": "replay_run", "arguments": {"source_run_id": "<run-id>"}},
        }

    @staticmethod
    def yaml_text(scenario: dict[str, Any]) -> str:
        return yaml.safe_dump(scenario, sort_keys=False)


def json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)
