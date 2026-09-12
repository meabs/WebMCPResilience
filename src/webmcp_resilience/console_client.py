"""Pure, bundle-aware helpers used by the terminal console.

This module deliberately contains presentation and request construction only.
Execution remains in :class:`CommandAPI` (or the CLI/MCP adapters that call it).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from .commands import CommandAPI, diff_bundles
from .models.bundle import RunBundle, SUPPORTED_COMPATIBILITY_GROUPS, redact_recursive


HIDDEN_TRACE_EVENTS = {"tool.discovered", "tool.change"}
FAILURE_EVENTS = {"tool.error", "invariant.fail", "result_invariant.fail"}


class ConsoleCommandClient:
    """Thin console seam over CommandAPI; it owns no browser or engine logic."""

    def __init__(self, api: CommandAPI) -> None:
        self.api = api

    def validate(self, scenario_path: Path, **kwargs: Any) -> RunBundle:
        return self.api.validate(scenario_path, **kwargs)

    async def run(self, scenario_path: Path, **kwargs: Any) -> RunBundle:
        if kwargs.get("allow_mutations"):
            raise ValueError("console client cannot authorize mutations; use CLI or AgentPolicy")
        kwargs["allow_mutations"] = False
        return await self.api.run(scenario_path, **kwargs)

    async def replay(self, bundle_path: Path, **kwargs: Any) -> RunBundle:
        if kwargs.get("allow_mutations"):
            raise ValueError("console client cannot authorize mutations; use CLI or AgentPolicy")
        kwargs["allow_mutations"] = False
        return await self.api.replay(bundle_path, **kwargs)

    def save(self, bundle: RunBundle, output: Path | None = None) -> Path:
        return self.api.save(bundle, output)


def load_bundle(path: Path) -> RunBundle | None:
    """Load a portable bundle, returning ``None`` for a legacy bare trace."""
    try:
        return RunBundle.model_validate(redact_recursive(json.loads(path.read_text())))
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
    """Return a deterministic shallow state diff suitable for a detail pane."""
    added = {key: current[key] for key in sorted(set(current) - set(previous))}
    removed = {key: previous[key] for key in sorted(set(previous) - set(current))}
    changed = {
        key: {"before": previous[key], "after": current[key]}
        for key in sorted(set(previous) & set(current))
        if previous[key] != current[key]
    }
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
            "actions": (bundle.preflight.get("capabilities") or {}).get("actions", ["invoke", "retry", "cancel", "navigate"]),
            "invariant_operators": ["<", "<=", "==", "!=", ">", ">="],
            "browser_mode": report.get("webmcp", {}).get("native_mode"),
            "report_status": report.get("summary", {}).get("status"),
        }
        return cls(bundle.tool_inventory, capabilities)

    @property
    def tool_names(self) -> list[str]:
        return sorted({str(item["name"]) for item in self.tool_inventory if item.get("name")})

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

    def build_scenario(self, *, name: str, actor: str, at: str, tool: str,
                       args: dict[str, Any], state_tool: str = "",
                       invariant: str = "", fault_label: str = "") -> dict[str, Any]:
        scenario: dict[str, Any] = {
            "name": name,
            "actors": {actor or "agent": [{"at": at or "0ms", "invoke": tool, "args": args}]},
            "compatibility": {
                "schema": "webmcp-resilience/scenario-1",
                "requires": dict(SUPPORTED_COMPATIBILITY_GROUPS),
            },
        }
        if state_tool:
            scenario["state"] = {"tool": state_tool}
        if invariant:
            scenario["result_invariants"] = [invariant]
        if fault_label:
            scenario["metadata"] = {"fault_label": fault_label}
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
        }

    @staticmethod
    def yaml_text(scenario: dict[str, Any]) -> str:
        return yaml.safe_dump(scenario, sort_keys=False)


def json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)
