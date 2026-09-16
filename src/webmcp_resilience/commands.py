"""Typed command interface shared by CLI, console and Python callers."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import platform
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

import yaml

from .browser import BrowserClient, WebMCPAdapter
from .config import Config
from .models.bundle import (ApprovalRecord, Artifact, Compatibility, RunBundle,
                            CompatibilityRequirements, SUPPORTED_COMPATIBILITY_GROUPS, redact_recursive,
                            StateObservation, validate_identifier)
from .models.scenario import Scenario
from .engine import ScenarioRunner, schedules
from .engine.reducer import FailureSignature, ReductionBudget, reduce_failure_report, signature_from_failure
from .engine.explorer import ScheduledAction
from .engine.invariants import InvariantError, validate_syntax
from .security import agent_policy_snapshot, canonical_origin, resolve_navigation_url, validate_scenario_navigation
from .tool_contracts import (
    build_inventory_contract,
    compare_tool_inventories,
    concise_drift_lines,
    no_contract_comparison,
    redact_tool_inventory,
    tool_contract_replay_decision,
    validate_contract_expectations,
)


WEBMCP_COMPATIBILITY_REPORT_VERSION = "1.0"


def _browser_version(client: BrowserClient) -> str:
    """Return Playwright's browser version without making it a required API."""
    version = getattr(getattr(client, "browser", None), "version", None)
    return str(version() if callable(version) else version or "unknown")


def _argument_mode(inventory: list[dict[str, Any]]) -> str | None:
    """Return a single discovered argument encoding, or None when unknown/mixed."""
    modes = {str(tool["argumentMode"]) for tool in inventory if tool.get("argumentMode")}
    return next(iter(modes)) if len(modes) == 1 else None


def _recorded_replay_config(config: Config, saved: RunBundle) -> Config:
    """Restore a bundle's explicit state boundary and target origin for replay."""
    updates: dict[str, str | None] = {}
    observation = saved.state_observation
    if observation.mode == "state_script" and observation.configured_source:
        updates["state_script"] = observation.configured_source
    else:
        # A replay must preserve an explicit lack of a state script rather than
        # inheriting a different boundary from the local project config.
        updates["state_script"] = None

    recorded_url = saved.browser_environment.get("url")
    if isinstance(recorded_url, str):
        parsed = urlsplit(recorded_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            updates["base_url"] = f"{parsed.scheme}://{parsed.netloc}"

    return config.model_copy(update=updates)


def _finding(identifier: str, severity: str, recommendation: str, source: str, value: Any, *, title: str | None = None) -> dict[str, Any]:
    return {
        "id": identifier,
        "severity": severity,
        "title": title or identifier.replace("-", " ").capitalize(),
        "recommendation": recommendation,
        "source_evidence": {"path": source, "value": redacted(value)},
    }


def _inventory_entry(tool: dict[str, Any], index: int) -> dict[str, Any]:
    schema = tool.get("inputSchema")
    schema_valid = (
        isinstance(schema, dict)
        and schema.get("type", "object") == "object"
        and isinstance(schema.get("properties", {}), dict)
        and isinstance(schema.get("required", []), list)
        and all(isinstance(item, str) for item in schema.get("required", []))
    )
    annotations = tool.get("annotations")
    annotation_quality = "valid_object" if isinstance(annotations, dict) else "missing"
    return {
        "index": index,
        "name": tool.get("name"),
        "description": tool.get("description"),
        "schema_quality": "valid_object" if schema_valid else "missing_or_invalid",
        "schema_evidence": {
            "type": schema.get("type") if isinstance(schema, dict) else None,
            "property_count": len(schema.get("properties", {})) if isinstance(schema, dict) and isinstance(schema.get("properties", {}), dict) else 0,
            "required_count": len(schema.get("required", [])) if isinstance(schema, dict) and isinstance(schema.get("required", []), list) else 0,
        },
        "annotation_quality": annotation_quality,
        "annotations": annotations if isinstance(annotations, dict) else {},
        "mutation_signal": (
            "read_only" if isinstance(annotations, dict) and annotations.get("readOnlyHint") is True
            else "consequential" if isinstance(annotations, dict) and annotations.get("destructiveHint") is True
            else "unknown"
        ),
    }


def build_state_observation(
    config: Config,
    scenario: Scenario | None = None,
    *,
    script_validation: dict[str, Any] | None = None,
    tool_inventory: list[dict[str, Any]] | None = None,
) -> StateObservation:
    """Describe only declared state sources; never derive state from the UI."""
    script = config.state_script
    state_tool = scenario.state.tool if scenario and scenario.state else None
    script_result = script_validation or {
        "status": "configured_not_checked" if script else "not_configured",
        "valid": None,
        "message": "State script will be evaluated before scenario actions." if script else "No state_script is configured.",
    }
    if script:
        mode = "state_script"
        source = script
        primary = script_result
    elif state_tool:
        mode = "scenario_state_tool"
        source = f"scenario.state.tool: {state_tool}"
        descriptor = next((item for item in (tool_inventory or []) if item.get("name") == state_tool), None)
        if tool_inventory is None:
            tool_result = {"status": "discovery_pending", "valid": None, "message": "The scenario state tool must be discovered before execution."}
        elif descriptor is None:
            tool_result = {"status": "invalid", "valid": False, "message": f"Scenario state tool {state_tool!r} was not discovered."}
        elif (descriptor.get("annotations") or {}).get("readOnlyHint") is not True:
            tool_result = {"status": "invalid", "valid": False, "message": f"Scenario state tool {state_tool!r} must be marked readOnlyHint: true."}
        else:
            tool_result = {"status": "valid", "valid": True, "message": "Discovered state tool is explicitly read-only."}
        primary = tool_result
    else:
        mode = "none"
        source = None
        primary = {"status": "not_configured", "valid": None, "message": "No state observation source is configured."}
    return StateObservation(
        mode=mode,
        configured_source=source,
        validation=primary,
        state_script={"configured": bool(script), "source": script, "validation": script_result},
        scenario_state_tool={
            "configured": bool(state_tool),
            "tool": state_tool,
            "validation": (
                primary if mode == "scenario_state_tool" else
                {"status": "not_configured", "valid": None, "message": "No scenario.state.tool is configured."}
            ),
        },
    )


def _validate_state_requirements(config: Config, scenario: Scenario) -> None:
    expressions_to_check = [*scenario.invariants, *scenario.result_invariants]
    if expressions_to_check and not config.state_script:
        expressions = ", ".join(repr(expression) for expression in expressions_to_check)
        raise CommandError(
            f"invariant state is unavailable for {expressions}; configure state_script to a JavaScript expression "
            "that returns the application's observable state. scenario.state.tool only supplies action arguments."
        )


def build_webmcp_compatibility_report(
    probe: dict[str, Any],
    inventory: list[dict[str, Any]],
    *,
    browser: dict[str, Any],
    inventory_before: list[dict[str, Any]] | None = None,
    inventory_error: str | None = None,
    registration_grace_ms: int = 0,
    inventory_checked_after_grace: bool = False,
    state_observation: StateObservation | None = None,
    include_contract_descriptions: bool = False,
) -> dict[str, Any]:
    """Build the browser-native, non-mutating WebMCP compatibility report."""
    api = probe.get("api", {})
    document = probe.get("document", {})
    permissions = probe.get("permissionsPolicy", {})
    runtime = probe.get("runtime", {})
    lifecycle = probe.get("lifecycle", {})
    tools = [_inventory_entry(tool, index) for index, tool in enumerate(inventory)]
    duplicate_names = sorted(name for name, count in Counter(tool.get("name") for tool in inventory).items() if name and count > 1)
    baseline_inventory = inventory if inventory_before is None else inventory_before
    before_names = [tool.get("name") for tool in baseline_inventory if isinstance(tool, dict)]
    after_names = [tool.get("name") for tool in inventory if isinstance(tool, dict)]
    added = sorted(set(after_names) - set(before_names))
    removed = sorted(set(before_names) - set(after_names))
    inventory_contract = build_inventory_contract(
        inventory, include_descriptions=include_contract_descriptions
    )
    contract_drift = compare_tool_inventories(
        baseline_inventory,
        inventory,
        include_descriptions=include_contract_descriptions,
    )
    findings: list[dict[str, Any]] = []

    if not api.get("available"):
        findings.append(_finding(
            "webmcp-api-unavailable", "error",
            "Use a browser build and target page that expose the WebMCP model context API.",
            "webmcp.api.available", api.get("available"), title="WebMCP API is unavailable",
        ))
    elif api.get("getTools") is not True:
        findings.append(_finding(
            "tool-discovery-unavailable", "error",
            "Expose the WebMCP getTools discovery method so the target's tool contract can be inspected.",
            "webmcp.api.getTools", api.get("getTools"), title="WebMCP tool discovery is unavailable",
        ))
    elif api.get("executeTool") is not True:
        findings.append(_finding(
            "tool-execution-unavailable", "error",
            "Expose the WebMCP executeTool method; preflight will still remain non-mutating.",
            "webmcp.api.executeTool", api.get("executeTool"), title="WebMCP tool execution is unavailable",
        ))
    elif api.get("mode") == "compatibility_host":
        findings.append(_finding(
            "compatibility-host-mode", "warning",
            "Use a browser with native WebMCP support for production compatibility confidence; keep the host for local fallback testing.",
            "webmcp.api.mode", api.get("mode"), title="Compatibility host is active",
        ))

    if document.get("secureContext") is False:
        findings.append(_finding(
            "insecure-context", "error",
            "Serve the target over HTTPS or a browser-trusted local secure context.",
            "document.secureContext", document.get("secureContext"), title="Target is not in a secure context",
        ))
    if document.get("crossOriginIsolated") is not True:
        findings.append(_finding(
            "origin-not-isolated", "info",
            "Enable cross-origin isolation when the target requires SharedArrayBuffer or other isolated browser capabilities.",
            "document.crossOriginIsolated", document.get("crossOriginIsolated"), title="Origin is not cross-origin isolated",
        ))
    if permissions.get("available") is False:
        findings.append(_finding(
            "permissions-policy-unavailable", "warning",
            "Expose and verify the Permissions Policy used to delegate model context to the target document.",
            "permissionsPolicy.available", permissions.get("available"), title="Permissions Policy evidence is unavailable",
        ))
    elif permissions.get("modelContextAllowed") is False:
        findings.append(_finding(
            "permissions-policy-denied", "error",
            "Allow tools for this origin in the response Permissions-Policy and iframe allow attributes.",
            "permissionsPolicy.modelContextAllowed", permissions.get("modelContextAllowed"), title="Permissions Policy denies tools",
        ))
    elif permissions.get("modelContextAllowed") is None:
        findings.append(_finding(
            "permissions-policy-unverified", "warning",
            "Verify tools delegation explicitly; this browser did not expose a definitive permission result.",
            "permissionsPolicy.modelContextAllowed", permissions.get("modelContextAllowed"), title="Tools permission is unverified",
        ))

    frames = document.get("frames", []) if isinstance(document.get("frames"), list) else []
    if frames:
        findings.append(_finding(
            "iframe-topology", "warning",
            "Verify each embedded document's origin, sandbox, and allow attributes before relying on WebMCP from an iframe.",
            "document.frames", frames, title="Document contains iframes",
        ))
    cross_origin_frames = [frame for frame in frames if isinstance(frame, dict) and frame.get("sameOrigin") is False]
    if cross_origin_frames:
        findings.append(_finding(
            "cross-origin-iframe", "warning",
            "Configure explicit Permissions Policy delegation and test the iframe's own model context; parent-page evidence does not prove iframe access.",
            "document.frames[sameOrigin=false]", cross_origin_frames, title="Cross-origin iframe detected",
        ))

    if api.get("cancellation") is not True:
        findings.append(_finding(
            "cancellation-unavailable", "warning",
            "Provide AbortController support and ensure executeTool propagates AbortSignal cancellation.",
            "webmcp.api.cancellation", api.get("cancellation"), title="Cancellation capability is unavailable",
        ))
    else:
        findings.append(_finding(
            "cancellation-not-exercised", "info",
            "Run a declared cancellation scenario to verify tool-level cancellation; preflight intentionally never invokes a page tool.",
            "webmcp.api.cancellation", {"host_signal": True, "tested": False}, title="Cancellation is host-capable but untested",
        ))
    if runtime.get("navigation") is not True:
        findings.append(_finding(
            "navigation-unavailable", "warning",
            "Expose standard document navigation APIs and test navigation-related scenarios separately.",
            "runtime.navigation", runtime.get("navigation"), title="Navigation capability is unavailable",
        ))

    for item in tools:
        if item["schema_quality"] != "valid_object":
            findings.append(_finding(
                "tool-schema-quality", "warning",
                "Publish a JSON object inputSchema with valid properties and required entries.",
                f"tool_inventory[{item['index']}].inputSchema", item["schema_quality"], title=f"Tool schema needs review: {item.get('name')}",
            ))
        if item["annotation_quality"] != "valid_object":
            findings.append(_finding(
                "tool-annotations-missing", "warning",
                "Publish tool annotations, especially readOnlyHint or destructiveHint, so callers can apply safe execution policy.",
                f"tool_inventory[{item['index']}].annotations", item["annotation_quality"], title=f"Tool annotations missing: {item.get('name')}",
            ))
    if inventory_error:
        findings.append(_finding(
            "tool-inventory-error", "error", "Fix the WebMCP getTools implementation so the browser can expose a stable inventory.",
            "tool_inventory.error", inventory_error, title="Tool inventory could not be collected",
        ))
    elif api.get("available") and api.get("getTools") and not inventory:
        findings.append(_finding(
            "tool-inventory-empty", "error",
            "WebMCP is available but its inventory is empty. Check that registration ran, registerTool options/AbortSignal, origin-trial setup, and the registration grace period.",
            "tool_inventory.count", 0, title="WebMCP tool inventory is empty",
        ))
        if inventory_checked_after_grace and registration_grace_ms:
            findings.append(_finding(
                "tool-registration-hung", "error",
                "The WebMCP API is available but no tools registered after the configured grace period. "
                "Inspect registration promises, AbortSignal handling, and page startup errors.",
                "tool_inventory.registration", {"count": 0, "grace_ms": registration_grace_ms},
                title="Tool registration appears hung",
            ))
    if state_observation and state_observation.validation.get("valid") is False:
        findings.append(_finding(
            "state-observation-invalid", "error",
            state_observation.validation.get("message", "Fix the configured state observation source before running invariants."),
            "state_observation.validation", state_observation.validation,
            title="State observation is invalid",
        ))
    if added or removed:
        findings.append(_finding(
            "tool-inventory-changed", "warning", "Investigate dynamic tool registration and rerun preflight before relying on the inventory.",
            "tool_inventory.changes", {"added": added, "removed": removed}, title="Tool inventory changed during preflight",
        ))
    if duplicate_names:
        findings.append(_finding(
            "ambiguous-tool-names", "error",
            "Declare an origin/frame-qualified tool selector before invoking duplicate names.",
            "tool_inventory.duplicate_names", duplicate_names, title="Tool names are ambiguous",
        ))
    if contract_drift["changed"]:
        findings.append(_finding(
            "tool-contract-drift", "warning", "Investigate schema or annotation mutation and rerun preflight before relying on the inventory.",
            "tool_contract_drift", contract_drift, title="Tool contract changed during preflight",
        ))

    counts = Counter(item["severity"] for item in findings)
    status = "unsupported" if counts["error"] else "degraded" if counts["warning"] else "compatible"
    report = {
        "report_version": WEBMCP_COMPATIBILITY_REPORT_VERSION,
        "report_kind": "webmcp_compatibility",
        "scope": "browser_webmcp_compatibility",
        "non_mutating": True,
        "browser": browser,
        "webmcp": {
            "api": api,
            "native_mode": api.get("mode"),
            "argument_profiles": sorted({tool.get("argumentMode") for tool in inventory if tool.get("argumentMode")} ),
            "toolchange_listener_supported": lifecycle.get("toolchangeListenerSupported"),
        },
        "security": {
            "secure_context": document.get("secureContext"),
            "origin": document.get("origin"),
            "top_origin": document.get("topOrigin"),
            "cross_origin_isolated": document.get("crossOriginIsolated"),
            "permissions_policy": permissions,
        },
        "document_topology": {
            "is_top_level": document.get("isTopLevel"),
            "same_origin_with_top": document.get("sameOriginWithTop"),
            "iframe_count": document.get("iframeCount", len(frames)),
            "frames": frames,
        },
        "runtime": {
            "user_agent": runtime.get("userAgent"),
            "headless": runtime.get("headless"),
            "cancellation": {"host_signal_supported": api.get("cancellation"), "tested": False},
            "navigation": {"browser_navigation_api": runtime.get("navigation"), "tested": False},
        },
        "state_observation": state_observation.model_dump(mode="json") if state_observation else build_state_observation(Config()).model_dump(mode="json"),
        "tool_inventory": tools,
        "inventory_changes": {"observed": bool(added or removed), "added": added, "removed": removed},
        "inventory_contract": inventory_contract,
        "tool_contract_drift": contract_drift,
        "findings": findings,
        "summary": {
            "status": status,
            "finding_counts": dict(sorted(counts.items())),
            "tool_count": len(tools),
        },
    }
    safe_report = redacted(report)
    # The contract module has already redacted this with schema awareness;
    # retain secret-looking property names as useful structure.
    safe_report["inventory_contract"] = inventory_contract
    return safe_report


class CommandError(RuntimeError):
    """Expected command failure; frontends map it to a stable exit code."""

    exit_code = 2
    code = "invalid_request"


class ToolContractDriftError(CommandError):
    """A replay was rejected because its saved inventory contract changed."""

    code = "tool_contract_drift"

    def __init__(self, report: dict[str, Any], decision: dict[str, Any] | None = None) -> None:
        safe_report = redacted(report)
        safe_decision = redacted(decision or tool_contract_replay_decision(safe_report))
        self.details = safe_report | {
            "tool_contract_drift": safe_report,
            "policy_impact": safe_decision.get("policy_impact", safe_report.get("policy_impact")),
            "replay_decision": safe_decision,
            "per_tool_reasons": {
                name: [change.get("summary", "changed") for change in detail.get("changes", [])]
                for name, detail in safe_report.get("details", {}).items()
            },
        }
        lines = concise_drift_lines(safe_report)
        if safe_decision.get("status"):
            lines.append(f"replay decision: {safe_decision['status']}")
        super().__init__("unsafe replay rejected: " + ("\n".join(lines) if lines else "tool inventory fingerprint differs"))


class ToolContractEvidenceIntegrityError(ToolContractDriftError):
    """Saved replay evidence cannot safely establish its own baseline."""

    def __init__(self, saved: RunBundle, reasons: list[str]) -> None:
        report = {
            "version": "1.0",
            "status": "evidence_integrity_error",
            "changed": True,
            "policy_impact": "evidence_integrity",
            "baseline_inventory_fingerprint": saved.inventory_contract.get("inventory_fingerprint"),
            "current_inventory_fingerprint": saved.compatibility.tool_inventory_fingerprint,
            "recorded_inventory_fingerprint": saved.compatibility.tool_inventory_fingerprint,
            "evidence_integrity": {"valid": False, "reasons": reasons},
            "added_tools": [], "removed_tools": [], "changed_input_schemas": [],
            "changed_output_schemas": [], "changed_annotations": [],
            "descriptive_only_changes": [], "unchanged_tools": [],
            "details": {"inventory_evidence": {"policy_impact": "evidence_integrity", "changes": [
                {"kind": "evidence_integrity", "summary": reason, "policy_impact": "evidence_integrity"}
                for reason in reasons
            ]}},
        }
        super().__init__(report, tool_contract_replay_decision(report))


class ToolContractExpectationError(CommandError):
    """A scenario's explicit live-contract expectation did not hold."""

    code = "tool_contract_expectation_failed"

    def __init__(self, report: dict[str, Any]) -> None:
        self.details = redacted(report)
        messages = "; ".join(
            f"{item.get('tool')}: {item.get('message')}" for item in self.details.get("failures", [])
        )
        super().__init__(f"tool contract expectation failed: {messages or 'live contract did not match'}")


class PolicyDeniedError(CommandError):
    """A request rejected before browser execution by the active policy."""

    code = "policy_denied"


def load_scenario(path: Path) -> Scenario:
    try:
        return Scenario.model_validate(yaml.safe_load(path.read_text()))
    except Exception as error:
        raise CommandError(f"invalid scenario {path}: {error}") from error


def _validate_discovered_arguments(action: Any, descriptor: dict[str, Any]) -> None:
    """Validate the portable action arguments against discovered JSON schema."""
    schema = descriptor.get("inputSchema")
    if not isinstance(schema, dict):
        return
    arguments = action.args or {}
    if not isinstance(arguments, dict):
        raise CommandError(f"arguments for {action.invoke or action.retry!r} must be an object")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if isinstance(required, list):
        missing = [key for key in required if key not in arguments]
        if missing:
            raise CommandError(f"missing required arguments for {action.invoke or action.retry!r}: {missing}")
    if isinstance(properties, dict) and schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise CommandError(f"unknown arguments for {action.invoke or action.retry!r}: {unknown}")
    for key, value in arguments.items():
        expected = properties.get(key, {}).get("type") if isinstance(properties.get(key), dict) else None
        valid = {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }.get(expected, True)
        if not valid:
            raise CommandError(f"argument {key!r} for {action.invoke or action.retry!r} must be {expected}")


def redacted(value: Any) -> Any:
    return redact_recursive(value)


def failure_handoff(bundle: RunBundle, bundle_path: Path | None = None) -> dict[str, Any]:
    """Return the compact, portable handoff shared by CLI and control clients."""
    failed = next(
        (event for event in reversed(bundle.trace.events if bundle.trace else [])
         if event.type in {"invariant.fail", "result_invariant.fail", "tool_contract.assertion.fail"}),
        None,
    )
    reduced = next((artifact.path for artifact in bundle.artifacts if artifact.kind == "scenario"), None)
    return {
        "failed_invariant": (failed.data.get("expression") or failed.data.get("assertion") if failed else None),
        "observed_state": redacted(failed.state_snapshot if failed else {}),
        "capability_fingerprint": bundle.compatibility.capability_fingerprint,
        "tool_inventory_fingerprint": bundle.compatibility.tool_inventory_fingerprint,
        "tool_contract_drift": redacted(bundle.tool_contract_drift),
        "tool_contract_replay_decision": redacted(bundle.tool_contract_replay_decision),
        "bundle_path": str(bundle_path) if bundle_path else None,
        "reduced_repro_path": reduced,
        "replay_command": list(bundle.replay_command),
    }


class CommandAPI:
    """Deep command module: each operation returns one portable RunBundle."""

    def __init__(self, config: Config, *, output_dir: Path = Path(".webmcp/runs")) -> None:
        self.config, self.output_dir = config, output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _safe_output(self, *parts: str) -> Path:
        """Resolve a framework-generated artifact and prove it stays contained."""
        target = self.output_dir.joinpath(*parts).resolve()
        try:
            target.relative_to(self.output_dir)
        except ValueError as error:
            raise CommandError("unsafe output path rejected: traversal outside output root") from error
        return target

    @staticmethod
    def _safe_write(target: Path, contents: str) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
        return target

    async def _prepare_application(self, page: Any, adapter: WebMCPAdapter) -> dict[str, Any]:
        """Apply the explicitly configured application-state boundary.

        A new browser context is not a server reset.  Projects that need
        backend isolation can provide a narrowly scoped page script (usually a
        local reset endpoint); its use is recorded so replay cannot pretend it
        happened implicitly.
        """
        boundary = {
            "setup_configured": bool(self.config.setup_script),
            "reset_configured": bool(self.config.reset_script),
            "initial_state_configured": self.config.initial_state is not None,
            "reset_applied": False,
            "setup_applied": False,
            "initial_state_checked": False,
        }
        for key, script in (("reset_applied", self.config.reset_script), ("setup_applied", self.config.setup_script)):
            if script:
                await page.evaluate(
                    "script => (new Function(`return (async () => {${script}})()`))()",
                    script,
                )
                boundary[key] = True
        if self.config.initial_state is not None:
            observed = await adapter.get_state()
            boundary["initial_state_checked"] = True
            boundary["observed_initial_state"] = redacted(observed)
            if observed != self.config.initial_state:
                raise CommandError(
                    "initial observable state does not match configured initial_state: "
                    f"expected {self.config.initial_state!r}, observed {observed!r}"
                )
        return boundary

    async def preflight(self, path: str = "/", *, headless: bool = True, run_id: str | None = None) -> RunBundle:
        bundle = RunBundle(command="preflight", **({"run_id": run_id} if run_id else {}))
        bundle.agent_policy = agent_policy_snapshot(
            [canonical_origin(self.config.base_url)],
            mutation_permission=False,
            authority_source="preflight",
        )
        try:
            preflight_url = resolve_navigation_url(path, self.config.base_url)
        except ValueError as error:
            raise CommandError(f"preflight navigation rejected before browser execution: {error}") from error
        async with BrowserClient(self.config.browser, headless=headless, args=self.config.browser_args, channel=self.config.browser_channel) as client:
            assert client.page and client.browser
            await client.page.goto(preflight_url)
            adapter = WebMCPAdapter(client.page, self.config.state_script, self.config.from_origins, self.config.webmcp_profile)
            await adapter.install()
            probe = await adapter.probe()
            inventory_before: list[dict[str, Any]] = []
            inventory: list[dict[str, Any]] = []
            inventory_error: str | None = None
            inventory_checked_after_grace = False
            if probe.get("api", {}).get("available") and probe.get("api", {}).get("getTools"):
                try:
                    # Discovery is read-only. It never executes a page tool.
                    inventory_before = await adapter.get_tools()
                    if not inventory_before and self.config.tool_registration_grace_ms:
                        await asyncio.sleep(self.config.tool_registration_grace_ms / 1000)
                        inventory_checked_after_grace = True
                    inventory = await adapter.get_tools()
                except Exception as error:
                    inventory_error = str(error)
                    inventory = inventory_before
            script_validation: dict[str, Any] | None = None
            if self.config.state_script:
                try:
                    state = await adapter.get_state()
                    script_validation = {
                        "status": "valid",
                        "valid": True,
                        "returns_object": True,
                        "message": "state_script returned an observable state object.",
                        "keys": sorted(state),
                    }
                except Exception as error:
                    script_validation = {
                        "status": "invalid",
                        "valid": False,
                        "returns_object": False,
                        "message": f"state_script must return an object: {error}",
                    }
            state_observation = build_state_observation(self.config, script_validation=script_validation)
            environment = {
                "browser": self.config.browser,
                "version": _browser_version(client),
                "channel": self.config.browser_channel or "default",
                "headless": headless,
                "platform": platform.platform(),
                "url": client.page.url,
                "user_agent": probe.get("runtime", {}).get("userAgent"),
            }
            probe["tools"] = {
                "count": len(inventory),
                "declarative": sum(isinstance(tool.get("inputSchema"), dict) for tool in inventory),
                "schema_valid": sum(_inventory_entry(tool, index)["schema_quality"] == "valid_object" for index, tool in enumerate(inventory)),
                "annotations_present": sum(isinstance(tool.get("annotations"), dict) for tool in inventory),
                "inventory_error": inventory_error,
                "registration": {
                    "grace_ms": self.config.tool_registration_grace_ms,
                    "checked_after_grace": inventory_checked_after_grace,
                    "hung_heuristic": bool(inventory_checked_after_grace and not inventory and not inventory_error),
                },
            }
            browser_evidence = {
                "name": self.config.browser,
                "version": environment["version"],
                "channel": environment["channel"],
                "headless": headless,
                "user_agent": environment["user_agent"],
                "webmcp_profile": self.config.webmcp_profile,
                "webmcp_argument_mode": _argument_mode(inventory),
            }
            report = build_webmcp_compatibility_report(
                probe,
                inventory,
                browser=browser_evidence,
                inventory_before=inventory_before,
                inventory_error=inventory_error,
                registration_grace_ms=self.config.tool_registration_grace_ms,
                inventory_checked_after_grace=inventory_checked_after_grace,
                state_observation=state_observation,
                include_contract_descriptions=self.config.tool_contract_include_descriptions,
            )
            fingerprint = hashlib.sha256(json.dumps(redacted(report), sort_keys=True).encode()).hexdigest()[:16]
            bundle.compatibility = Compatibility(
                browser=self.config.browser,
                browser_version=environment["version"],
                headless=headless,
                webmcp_profile=self.config.webmcp_profile,
                webmcp_argument_mode=_argument_mode(inventory),
                capability_fingerprint=fingerprint,
                tool_inventory_fingerprint=report["inventory_contract"]["inventory_fingerprint"],
            )
            bundle.preflight = report
            bundle.state_observation = state_observation
            bundle.browser_environment = redacted(environment)
            bundle.tool_inventory = redact_tool_inventory(inventory)
            bundle.inventory_contract = report["inventory_contract"]
            bundle.tool_contract_drift = report["tool_contract_drift"]
            bundle.result = {
                "passed": report["summary"]["status"] != "unsupported" and state_observation.validation.get("valid") is not False,
                "summary": "WebMCP browser compatibility report",
                "report_kind": report["report_kind"],
                "report_version": report["report_version"],
                "status": report["summary"]["status"],
                "finding_counts": report["summary"]["finding_counts"],
                "contract_version": bundle.contract_version,
                "engine_version": bundle.engine_version,
                "tool_contract_drift": bundle.tool_contract_drift,
                "tool_inventory_fingerprint": bundle.compatibility.tool_inventory_fingerprint,
            }
            report_path = self._safe_output(bundle.run_id, "preflight-report.json")
            self._safe_write(report_path, json.dumps(report, indent=2, default=str))
            bundle.artifacts.append(Artifact(
                kind="report", path=str(report_path), redacted=True, sensitivity="redacted",
                description="browser-native WebMCP compatibility report",
                run_id=bundle.run_id,
            ))
        return bundle

    def validate(self, path: Path | None = None, *, scenario: Scenario | dict[str, Any] | None = None,
                 run_id: str | None = None,
                 tool_inventory: list[dict[str, Any]] | None = None) -> RunBundle:
        if (path is None) == (scenario is None):
            raise CommandError("provide exactly one scenario path or in-memory scenario")
        scenario = load_scenario(path) if path is not None else (scenario if isinstance(scenario, Scenario) else Scenario.model_validate(scenario))
        self._validate_scenario_semantics(scenario, tool_inventory)
        _validate_state_requirements(self.config, scenario)
        bundle = RunBundle.for_scenario("validate", scenario, run_id=run_id)
        bundle.state_observation = build_state_observation(self.config, scenario)
        bundle.agent_policy = agent_policy_snapshot(
            [canonical_origin(self.config.base_url)],
            mutation_permission=False,
            authority_source="validate",
        )
        bundle.requirements = self._requirements(scenario)
        bundle.compatibility = Compatibility(groups=bundle.requirements)
        bundle.actions = [dict(actor=actor, **action.model_dump(mode="json")) for actor, actions in scenario.actors.items() for action in actions]
        bundle.faults = [fault.model_dump(mode="json") for fault in scenario.faults]
        bundle.tool_inventory = redact_tool_inventory(tool_inventory or [])
        if tool_inventory is not None:
            bundle.inventory_contract = build_inventory_contract(
                tool_inventory,
                include_descriptions=self.config.tool_contract_include_descriptions,
            )
            bundle.compatibility.tool_inventory_fingerprint = bundle.inventory_contract["inventory_fingerprint"]
            bundle.tool_contract_drift = no_contract_comparison(
                inventory_fingerprint=bundle.compatibility.tool_inventory_fingerprint
            )
            bundle.tool_contract_expectations = validate_contract_expectations(
                scenario.tool_contracts, tool_inventory,
                include_descriptions=self.config.tool_contract_include_descriptions,
            )
        bundle.result = {"passed": True, "summary": "scenario is valid", "source": str(path) if path else "in_memory", "contract_version": bundle.schema_version, "engine_version": bundle.compatibility.engine_version}
        return bundle

    @staticmethod
    def _requirements(scenario: Scenario) -> CompatibilityRequirements:
        declared = scenario.compatibility.get("requires")
        if not isinstance(declared, dict) or set(declared) != set(SUPPORTED_COMPATIBILITY_GROUPS):
            raise CommandError("scenario compatibility.requires must declare every typed versioned group")
        try:
            return CompatibilityRequirements.model_validate(declared)
        except Exception as error:
            raise CommandError(f"scenario compatibility.requires is invalid: {error}") from error

    def _validate_scenario_semantics(
        self,
        scenario: Scenario,
        tool_inventory: list[dict[str, Any]] | None = None,
        *,
        include_contract_descriptions: bool | None = None,
    ) -> None:
        required = self._requirements(scenario)
        unsupported = {key: value for key, value in required.model_dump().items() if SUPPORTED_COMPATIBILITY_GROUPS.get(key) != value}
        if unsupported:
            raise CommandError(f"scenario requires unsupported compatibility groups: {unsupported}")
        for expression in [*scenario.invariants, *scenario.final_invariants, *scenario.result_invariants]:
            try:
                validate_syntax(expression)
            except InvariantError as error:
                raise CommandError(f"invalid invariant {expression!r}: {error}") from error
        for name, expectation in scenario.tool_contracts.items():
            for expression in expectation.result_invariants:
                try:
                    validate_syntax(expression)
                except InvariantError as error:
                    raise CommandError(f"invalid tool contract result invariant for {name!r}: {expression!r}: {error}") from error
        if tool_inventory is not None:
            known = {tool.get("name") for tool in tool_inventory}
            referenced = {action.invoke or action.retry for actions in scenario.actors.values() for action in actions if action.invoke or action.retry}
            if scenario.state:
                referenced.add(scenario.state.tool)
            missing = referenced - known
            if missing:
                raise CommandError(f"scenario references unavailable tools: {sorted(missing)}")
            descriptors: dict[str, list[dict[str, Any]]] = {}
            for tool in tool_inventory:
                if isinstance(tool, dict):
                    descriptors.setdefault(str(tool.get("name")), []).append(tool)
            for actions in scenario.actors.values():
                for action in actions:
                    name = action.invoke or action.retry
                    if not name or name not in descriptors:
                        continue
                    matches = descriptors[name]
                    if len(matches) > 1 and (action.tool_origin or action.tool_frame):
                        matches = [item for item in matches if
                                   (not action.tool_origin or (item.get("identity") or {}).get("origin") == action.tool_origin)
                                   and (not action.tool_frame or (item.get("identity") or {}).get("frame") == action.tool_frame)]
                    if len(matches) != 1:
                        raise CommandError(
                            f"ambiguous WebMCP tool name {name!r}; declare tool_origin/tool_frame to select one"
                        )
                    _validate_discovered_arguments(action, matches[0])
            if scenario.state:
                state_matches = [tool for tool in tool_inventory if tool.get("name") == scenario.state.tool]
                if len(state_matches) != 1:
                    raise CommandError(f"scenario state tool {scenario.state.tool!r} was not discovered")
                state_tool = state_matches[0]
                if (state_tool.get("annotations") or {}).get("readOnlyHint") is not True:
                    raise CommandError(f"scenario state tool {scenario.state.tool!r} must be marked readOnlyHint: true")
            expectation_report = validate_contract_expectations(
                scenario.tool_contracts, tool_inventory,
                include_descriptions=(
                    self.config.tool_contract_include_descriptions
                    if include_contract_descriptions is None
                    else include_contract_descriptions
                ),
            )
            if not expectation_report["passed"]:
                raise ToolContractExpectationError(expectation_report)

    @staticmethod
    def _validate_execution_policy(scenario: Scenario, allow_mutations: bool) -> None:
        if allow_mutations:
            return
        denied = [
            (actor, action.action or "cancel")
            for actor, actions in scenario.actors.items()
            for action in actions
            if action.action in {"click", "fill", "select"} or action.cancel is not None
        ]
        if denied:
            details = ", ".join(f"{actor}:{action}" for actor, action in denied)
            raise PolicyDeniedError(
                f"read-only agent policy denied state-changing UI/cancellation actions: {details}"
            )

    @staticmethod
    def schedule_descriptor(scenario: Scenario, schedule: list[ScheduledAction]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for offset, actor, action in schedule:
            try:
                index = scenario.actors[actor].index(action)
            except (KeyError, ValueError) as error:
                raise CommandError("schedule does not belong to the declared scenario") from error
            result.append({"offset_ms": offset, "actor": actor, "action_index": index})
        return result

    @staticmethod
    def requested_action_offsets(scenario: Scenario) -> list[dict[str, Any]]:
        """Return declared offsets in actor/action order, independent of runtime time."""
        return [
            {"actor": actor, "action_index": index, "offset_ms": action.offset_ms}
            for actor, actions in scenario.actors.items()
            for index, action in enumerate(actions)
        ]

    @staticmethod
    def _record_scheduler_trace(bundle: RunBundle) -> None:
        scheduler = bundle.execution.get("scheduler")
        if not isinstance(scheduler, dict) or bundle.trace is None:
            return
        scheduler["recorded_trace_timestamps"] = [
            {
                "timestamp_ms": event.timestamp_ms,
                "actor": event.actor,
                "type": event.type,
                "name": event.name,
            }
            for event in bundle.trace.events
        ]

    @staticmethod
    def schedule_from_descriptor(scenario: Scenario, descriptor: list[dict[str, Any]]) -> list[ScheduledAction]:
        try:
            return [(int(item["offset_ms"]), str(item["actor"]), scenario.actors[str(item["actor"])][int(item["action_index"])]) for item in descriptor]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise CommandError("unsafe replay rejected: recorded schedule does not match its scenario") from error

    def save(self, bundle: RunBundle, output: Path | None = None) -> Path:
        validate_identifier(bundle.run_id, label="run id")
        if output is None:
            target = self._safe_output(bundle.run_id, "bundle.json")
        else:
            # Explicit CLI/Python destinations are caller-managed. Agent-facing
            # methods never use this escape hatch.
            target = output.resolve()
        return bundle.write(target)

    def bundle_path(self, run_id: str) -> Path:
        """Return the framework-owned path for a saved run bundle."""
        validate_identifier(run_id, label="run id")
        return self._safe_output(run_id, "bundle.json")

    def load_bundle(self, run_id: str) -> RunBundle:
        """Load a framework-owned bundle by its repository-local run id."""
        path = self.bundle_path(run_id)
        if not path.is_file():
            raise CommandError("run bundle not found")
        try:
            return RunBundle.model_validate_json(path.read_text())
        except Exception as error:
            raise CommandError(f"invalid run bundle {run_id}: {error}") from error

    def diff(self, left: Path, right: Path) -> dict[str, Any]:
        return diff_bundles(
            RunBundle.model_validate_json(left.read_text()),
            RunBundle.model_validate_json(right.read_text()),
        )

    def report(self, bundle_path: Path, output: Path | None = None) -> dict[str, Any]:
        record = RunBundle.model_validate_json(bundle_path.read_text())
        # A report is framework output; external bundle locations must not
        # select a write destination. Explicit output is caller-managed.
        destination = output or self._safe_output(record.run_id, "report.html")
        events = record.trace.events if record.trace else []
        # Bundles are external input at this boundary; redact again rather
        # than trusting a producer's redaction declaration.
        report_payload = redacted({
            "run_id": record.run_id,
            "result": record.result,
            "execution": record.execution,
            "state_changes": record.state_changes,
            "artifacts": [item.model_dump(mode="json") for item in record.artifacts],
            "events": [event.model_dump(mode="json") for event in events],
        })
        contents = json.dumps(report_payload, indent=2, default=str)
        scheduler = record.execution.get("scheduler", {})
        summary = (
            f"<p>Outcome: <strong>{html.escape(str(record.result.get('passed')))}</strong> · "
            f"requested schedules: {html.escape(str(record.result.get('requested_schedules', record.result.get('schedules', 0))))} · "
            f"unique effective schedules: {html.escape(str(record.result.get('effective_unique_schedules', scheduler.get('effective_schedule_count', 0))))}</p>"
        )
        destination.write_text(
            "<html><body><h1>WebMCP Resilience report</h1>" + summary
            + "<p>This offline report is read-only. Replay is performed only by the webmcp CLI.</p>"
            + "<h2>Evidence</h2><pre>" + html.escape(contents) + "</pre></body></html>"
        )
        return {"schema_version": "1.0", "contract_version": "1.0", "engine_version": record.engine_version, "run_id": record.run_id, "output": str(destination), "passed": record.result.get("passed")}

    def export_handoff(self, bundle_path: Path, output: Path | None = None) -> dict[str, Path]:
        """Export safe incident metadata without reading sensitive artifacts."""
        record = RunBundle.model_validate(redacted(json.loads(bundle_path.read_text())))
        handoff = record.result.get("failure_handoff")
        if not isinstance(handoff, dict):
            handoff = failure_handoff(record, bundle_path)
        safe_artifacts = [
            {
                "kind": artifact.kind,
                "sensitivity": artifact.sensitivity,
                "redacted": artifact.redacted,
                "description": artifact.description,
                "available": artifact.kind in {"failure", "scenario"} and artifact.redacted and artifact.sensitivity == "redacted",
            }
            for artifact in record.artifacts
        ]
        summary = redacted({
            "run_id": record.run_id,
            "scenario": (record.scenario or {}).get("name"),
            "failed_invariant": handoff.get("failed_invariant"),
            "observed_state": handoff.get("observed_state", {}),
            "minimal_repro": [item for item in safe_artifacts if item["kind"] in {"failure", "scenario"}],
            "selected_schedule": record.execution.get("schedule"),
            "policy": {
                "approvals": [approval.model_dump(mode="json") for approval in record.approvals],
                "agent_policy": record.agent_policy,
            },
            "capability_fingerprint": record.compatibility.capability_fingerprint,
            "tool_inventory_fingerprint": record.compatibility.tool_inventory_fingerprint,
            "tool_contract_drift": record.tool_contract_drift,
            "tool_contract_replay_decision": record.tool_contract_replay_decision,
            "tool_contract_expectations": record.tool_contract_expectations,
            "artifacts": safe_artifacts,
            "replay_command": record.replay_command,
        })
        target = (output or self._safe_output(record.run_id, "incident")).resolve()
        # An explicit output path is caller-managed; generated paths remain in
        # the framework output root and never contain imported evidence.
        target.parent.mkdir(parents=True, exist_ok=True)
        json_path = target if target.suffix == ".json" else target.with_suffix(".json")
        md_path = target.with_suffix(".md")
        json_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str))
        lines = [
            f"# WebMCP resilience incident · {summary['run_id']}", "",
            f"- Scenario: {summary.get('scenario') or 'unknown'}",
            f"- Failed invariant: {summary.get('failed_invariant') or 'unknown'}",
            f"- Capability fingerprint: {summary.get('capability_fingerprint') or 'unknown'}",
            f"- Tool inventory fingerprint: {summary.get('tool_inventory_fingerprint') or 'unknown'}",
            f"- Tool contract drift: {(summary.get('tool_contract_drift') or {}).get('status', 'not_compared')} ({(summary.get('tool_contract_drift') or {}).get('policy_impact', 'none')})",
            f"- Tool-contract replay decision: {(summary.get('tool_contract_replay_decision') or {}).get('status', 'not_recorded')}",
            f"- Bundle: {bundle_path}",
            f"- Replay: `{' '.join(summary.get('replay_command') or [])}`",
            "", "## Observed state", "", "```json",
            json.dumps(summary.get("observed_state", {}), indent=2, sort_keys=True),
            "```", "", "## Selected schedule", "", "```json",
            json.dumps(summary.get("selected_schedule"), indent=2, sort_keys=True, default=str),
            "```", "", "## Safe artifact metadata", "", "```json",
            json.dumps(summary.get("artifacts", []), indent=2, sort_keys=True), "```", "",
            "Sensitive artifact contents (screenshots, network payloads, and other potentially sensitive files) are intentionally omitted.",
        ]
        md_path.write_text("\n".join(lines) + "\n")
        return {"json": json_path, "markdown": md_path}

    async def run(self, scenario_path: Path | None = None, *, scenario: Scenario | dict[str, Any] | None = None, run_id: str | None = None, headless: bool = True,
                  allow_mutations: bool = False, adversarial: bool = False, seed: int = 0,
                  exploration_limit: int | None = None, exploration_budget_ms: int | None = None,
                  reduction_max_attempts: int | None = None, reduction_budget_ms: int | None = None,
                  expected_capability_fingerprint: str | None = None,
                  expected_webmcp_argument_mode: str | None = None,
                  expected_tool_inventory_fingerprint: str | None = None,
                  expected_tool_inventory: list[dict[str, Any]] | None = None,
                  contract_include_descriptions: bool | None = None,
                  replay_fingerprint_policy: dict[str, Any] | None = None,
                  strict_tool_contracts: bool = False,
                  recorded_schedule: list[dict[str, Any]] | None = None,
                  allowed_target_origins: Iterable[str] | None = None) -> RunBundle:
        if (scenario_path is None) == (scenario is None):
            raise CommandError("provide exactly one scenario path or in-memory scenario")
        scenario = load_scenario(scenario_path) if scenario_path is not None else (scenario if isinstance(scenario, Scenario) else Scenario.model_validate(scenario))
        include_contract_descriptions = (
            self.config.tool_contract_include_descriptions
            if contract_include_descriptions is None
            else contract_include_descriptions
        )
        self._validate_scenario_semantics(scenario)
        _validate_state_requirements(self.config, scenario)
        try:
            validate_scenario_navigation(scenario, self.config.base_url, set(allowed_target_origins or ()))
        except ValueError as error:
            raise CommandError(f"scenario navigation rejected before browser execution: {error}") from error
        initial_url = resolve_navigation_url(scenario.url, self.config.base_url)
        self._validate_execution_policy(scenario, allow_mutations)
        bundle = RunBundle.for_scenario("run", scenario, run_id=run_id)
        bundle.agent_policy = agent_policy_snapshot(
            [canonical_origin(origin) for origin in (allowed_target_origins or [self.config.base_url])],
            mutation_permission=allow_mutations,
            authority_source="command",
        )
        bundle.state_observation = build_state_observation(self.config, scenario)
        bundle.approvals = [ApprovalRecord(authority="mutation_authorized" if allow_mutations else "read_only",
                                           reason="--allow-mutations" if allow_mutations else "default read-only policy")]
        bundle.actions = [dict(actor=actor, **action.model_dump(mode="json")) for actor, actions in scenario.actors.items() for action in actions]
        bundle.faults = [fault.model_dump(mode="json") for fault in scenario.faults]
        forced = self.schedule_from_descriptor(scenario, recorded_schedule) if recorded_schedule is not None else None
        exploration = None
        if forced is not None:
            chosen = [forced]
        elif adversarial:
            generation_limit = exploration_limit if exploration_limit is not None else self.config.exploration_limit
            generation_time_budget = exploration_budget_ms if exploration_budget_ms is not None else self.config.exploration_budget_ms
            exploration = schedules(
                scenario, limit=generation_limit, seed=seed,
                time_budget_ms=generation_time_budget,
            )
            chosen = exploration
        else:
            chosen = [None]
        requirements = self._requirements(scenario)
        bundle.requirements = requirements
        bundle.compatibility.groups = requirements
        bundle.execution = {
            "adversarial": adversarial,
            "seed": seed,
            "schedule": None,
            "schedule_index": None,
            "requirements": requirements.model_dump(mode="json"),
            "fault_configuration": bundle.faults,
            "approval_policy": [approval.model_dump(mode="json") for approval in bundle.approvals],
            "agent_policy": bundle.agent_policy,
            "scheduler": {
                "version": "1.0",
                "requested_action_offsets": self.requested_action_offsets(scenario),
                "selected_logical_schedule": None,
                "adversarial_seed": seed,
                "recorded_trace_timestamps": [],
                "generation_budget": {
                    "max_schedules": exploration_limit if exploration_limit is not None else self.config.exploration_limit,
                    "time_budget_ms": exploration_budget_ms if exploration_budget_ms is not None else self.config.exploration_budget_ms,
                    "examined": getattr(exploration, "examined", 0),
                    "exhausted": getattr(exploration, "exhausted", False),
                },
            },
        }
        output_root = self._safe_output(validate_identifier(bundle.run_id, label="run id"))
        output_root.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        effective_schedules: set[str] = set()
        for index, schedule in enumerate(chosen):
            actual_schedule = schedule or [(action.offset_ms, actor, action) for actor, actions in scenario.actors.items() for action in actions]
            # A new browser context for each schedule prevents state bleed between exploration variants.
            async with BrowserClient(self.config.browser, headless=headless, args=self.config.browser_args, channel=self.config.browser_channel) as client:
                assert client.page
                await client.page.goto(initial_url)
                readiness = WebMCPAdapter(client.page, self.config.state_script, self.config.from_origins, self.config.webmcp_profile)
                await readiness.install()
                probe = await readiness.probe()
                fingerprint = hashlib.sha256(json.dumps(redacted(probe), sort_keys=True).encode()).hexdigest()[:16]
                if expected_capability_fingerprint and fingerprint != expected_capability_fingerprint:
                    raise CommandError("unsafe replay rejected: browser capability fingerprint differs from the recorded run")
                bundle.preflight = redacted(probe)
                browser_version = _browser_version(client)
                bundle.browser_environment = redacted({
                    "browser": self.config.browser,
                    "version": browser_version,
                    "channel": self.config.browser_channel or "default",
                    "headless": headless,
                    "platform": platform.platform(),
                    "url": client.page.url,
                    "user_agent": probe.get("runtime", {}).get("userAgent"),
                    "webmcp_profile": self.config.webmcp_profile,
                    "webmcp_argument_mode": None,
                })
                bundle.compatibility = Compatibility(
                    browser=self.config.browser,
                    browser_version=browser_version,
                    headless=headless,
                    webmcp_profile=self.config.webmcp_profile,
                    webmcp_argument_mode=None,
                    capability_fingerprint=fingerprint,
                    groups=requirements,
                )
                script_validation: dict[str, Any] | None = None
                if self.config.state_script:
                    try:
                        state = await readiness.get_state()
                        script_validation = {
                            "status": "valid",
                            "valid": True,
                            "returns_object": True,
                            "message": "state_script returned an observable state object.",
                            "keys": sorted(state),
                        }
                    except Exception as error:
                        script_validation = {
                            "status": "invalid",
                            "valid": False,
                            "returns_object": False,
                            "message": f"state_script must return an object: {error}",
                        }
                bundle.state_observation = build_state_observation(self.config, scenario, script_validation=script_validation)
                if bundle.state_observation.validation.get("valid") is False:
                    raise CommandError(
                        f"{bundle.state_observation.validation.get('message')} Configure state_script to return the application's observable state object."
                    )
                network: list[dict[str, Any]] = []
                client.page.on("response", lambda response: network.append({"url": response.url, "status": response.status, "method": response.request.method}))
                state_boundary = await self._prepare_application(client.page, readiness)
                bundle.browser_environment["state_boundary"] = redacted(state_boundary)
                # Setup/reset may register or withdraw tools.  Re-discover
                # after applying it; the runner receives the same live
                # descriptors that were used for contract validation.
                runner = ScenarioRunner(client.page, scenario, self.config.state_script, self.config.from_origins, allow_mutations, base_url=self.config.base_url, webmcp_profile=self.config.webmcp_profile, invoke_timeout_ms=self.config.invoke_timeout_ms, state_settle_ms=self.config.state_settle_ms)
                # Discovery supplies the live compatibility contract. Validate
                # and compare it before any scenario tool or UI action runs.
                available_tools = await readiness.get_tools()
                bundle.compatibility.webmcp_argument_mode = _argument_mode(available_tools)
                bundle.browser_environment["webmcp_argument_mode"] = bundle.compatibility.webmcp_argument_mode
                if (expected_webmcp_argument_mode is not None
                        and bundle.compatibility.webmcp_argument_mode != expected_webmcp_argument_mode):
                    raise CommandError(
                        "unsafe replay rejected: WebMCP argument encoding differs from the recorded run "
                        f"({expected_webmcp_argument_mode!r} vs {bundle.compatibility.webmcp_argument_mode!r})"
                    )
                live_contract = build_inventory_contract(
                    available_tools,
                    include_descriptions=include_contract_descriptions,
                )
                bundle.tool_inventory = redact_tool_inventory(available_tools)
                bundle.inventory_contract = live_contract
                bundle.compatibility.tool_inventory_fingerprint = live_contract["inventory_fingerprint"]
                if expected_tool_inventory_fingerprint:
                    contract_drift = compare_tool_inventories(
                        expected_tool_inventory or [],
                        available_tools,
                        include_descriptions=include_contract_descriptions,
                    )
                    contract_drift["fingerprint_policy"] = replay_fingerprint_policy or {
                        "include_descriptions": include_contract_descriptions
                    }
                    # Keep the rebuilt baseline and recorded fingerprint as
                    # distinct evidence.  Replay evidence validation has
                    # already required them to agree; neither overwrites the
                    # other during later drift classification.
                    contract_drift["recorded_baseline_inventory_fingerprint"] = expected_tool_inventory_fingerprint
                    contract_drift["recorded_live_inventory_fingerprint"] = live_contract["inventory_fingerprint"]
                    if (
                        live_contract["inventory_fingerprint"] != expected_tool_inventory_fingerprint
                        and not contract_drift["changed"]
                    ):
                        contract_drift.update({
                            "status": "drift",
                            "changed": True,
                            "policy_impact": "unknown",
                            "details": {"inventory": {"policy_impact": "unknown", "changes": [{
                                "kind": "inventory_fingerprint",
                                "summary": "fingerprint changed without a classifiable contract difference",
                                "policy_impact": "unknown",
                            }]}},
                        })
                    if replay_fingerprint_policy is None:
                        contract_drift.update({
                            "status": "policy_mismatch",
                            "changed": True,
                            "policy_impact": "policy_mismatch",
                            "policy_mismatch": "saved bundle has no usable fingerprint_policy",
                        })
                    bundle.tool_contract_drift = contract_drift
                    decision = tool_contract_replay_decision(contract_drift, strict=strict_tool_contracts)
                    bundle.tool_contract_replay_decision = decision
                    if not decision["allowed"]:
                        raise ToolContractDriftError(contract_drift, decision)
                else:
                    bundle.tool_contract_drift = no_contract_comparison(
                        inventory_fingerprint=live_contract["inventory_fingerprint"]
                    )
                bundle.tool_contract_expectations = validate_contract_expectations(
                    scenario.tool_contracts, available_tools,
                    include_descriptions=include_contract_descriptions,
                )
                if not bundle.tool_contract_expectations["passed"]:
                    raise ToolContractExpectationError(bundle.tool_contract_expectations)
                self._validate_scenario_semantics(
                    scenario,
                    available_tools,
                    include_contract_descriptions=include_contract_descriptions,
                )
                try:
                    trace = await runner.run(actual_schedule)
                    bundle.trace = trace
                    effective_dispatch = getattr(runner, "effective_dispatch", [])
                    effective_schedules.add(json.dumps(effective_dispatch, sort_keys=True, default=str))
                    bundle.execution["scheduler"].update({
                        "last_effective_dispatch": effective_dispatch,
                        "effective_schedule_count": len(effective_schedules),
                    })
                    self._record_scheduler_trace(bundle)
                    bundle.state_changes = [event.model_dump(mode="json") for event in trace.events if event.type == "state.observed"]
                    shot = output_root / f"schedule-{index}.png"
                    await client.page.screenshot(path=str(shot), full_page=True)
                    bundle.artifacts.append(Artifact(kind="screenshot", path=str(shot), description="final page state", redacted=False, sensitivity="potentially_sensitive"))
                    network_path = self._safe_output(bundle.run_id, f"network-{index}.json"); self._safe_write(network_path, json.dumps(redacted(network), indent=2))
                    bundle.artifacts.append(Artifact(kind="network", path=str(network_path), description="redacted response metadata", redacted=True, sensitivity="redacted"))
                except Exception as error:
                    last_error = error
                    bundle.trace = getattr(getattr(runner, "recorder", None), "run", None)
                    effective_dispatch = getattr(runner, "effective_dispatch", [])
                    effective_schedules.add(json.dumps(effective_dispatch, sort_keys=True, default=str))
                    bundle.execution["scheduler"].update({
                        "last_effective_dispatch": effective_dispatch,
                        "effective_schedule_count": len(effective_schedules),
                    })
                    self._record_scheduler_trace(bundle)
                    bundle.execution.update({"schedule": self.schedule_descriptor(scenario, actual_schedule), "schedule_index": index})
                    bundle.execution["scheduler"]["selected_logical_schedule"] = bundle.execution["schedule"]
                    failure = runner.write_failure(output_root / "failure.yaml", bundle.execution["schedule"], run_id=bundle.run_id, requirements=requirements.model_dump(mode="json"))
                    bundle.artifacts.append(Artifact(kind="failure", path=str(failure), redacted=True, sensitivity="redacted", description="redacted diagnostic; replay bundle.json, not this file", run_id=bundle.run_id))
                    shot = output_root / "failure.png"
                    with suppress(Exception):
                        await client.page.screenshot(path=str(shot), full_page=True)
                        bundle.artifacts.append(Artifact(kind="screenshot", path=str(shot), description="failure page state", redacted=False, sensitivity="potentially_sensitive"))
                    network_path = self._safe_output(bundle.run_id, "network-failure.json"); self._safe_write(network_path, json.dumps(redacted(network), indent=2))
                    bundle.artifacts.append(Artifact(kind="network", path=str(network_path), description="redacted response metadata", redacted=True, sensitivity="redacted"))
                    target_signature = signature_from_failure(error, trace=getattr(getattr(runner, "recorder", None), "run", None))
                    reduction_budget = ReductionBudget(
                        max_attempts=reduction_max_attempts if reduction_max_attempts is not None else self.config.reduction_max_attempts,
                        time_budget_ms=reduction_budget_ms if reduction_budget_ms is not None else self.config.reduction_budget_ms,
                    )

                    async def reproduces(candidate: Scenario) -> FailureSignature | None:
                        async with BrowserClient(self.config.browser, headless=headless, args=self.config.browser_args, channel=self.config.browser_channel) as fresh:
                            assert fresh.page
                            await fresh.page.goto(resolve_navigation_url(candidate.url, self.config.base_url))
                            try:
                                candidate_schedule = [item for item in actual_schedule if item[1] in candidate.actors and item[2] in candidate.actors[item[1]]]
                                candidate_adapter = WebMCPAdapter(fresh.page, self.config.state_script, self.config.from_origins, self.config.webmcp_profile)
                                await candidate_adapter.install()
                                await self._prepare_application(fresh.page, candidate_adapter)
                                candidate_runner = ScenarioRunner(
                                    fresh.page, candidate, self.config.state_script, self.config.from_origins,
                                    allow_mutations, base_url=self.config.base_url,
                                    webmcp_profile=self.config.webmcp_profile, invoke_timeout_ms=self.config.invoke_timeout_ms,
                                    state_settle_ms=self.config.state_settle_ms,
                                )
                                await candidate_runner.run(candidate_schedule)
                            except Exception as candidate_error:
                                return signature_from_failure(candidate_error, trace=locals().get("candidate_runner", None).recorder.run if "candidate_runner" in locals() else None)
                            return None

                    if target_signature is not None:
                        reduction = await reduce_failure_report(
                            scenario, reproduces, target_signature=target_signature, budget=reduction_budget
                        )
                        reduced = reduction.scenario
                    else:
                        reduction = None
                        reduced = scenario
                    repro = output_root / "repro.yaml"
                    reduced_schedule = [item for item in actual_schedule if item[1] in reduced.actors and item[2] in reduced.actors[item[1]]]
                    repro_execution = bundle.execution | {"schedule": self.schedule_descriptor(reduced, reduced_schedule), "reduction": {
                        "status": "completed" if reduction else "not_attempted",
                        "target_signature": target_signature.as_dict() if target_signature else None,
                        "attempts": reduction.attempts if reduction else 0,
                        "budget": reduction_budget.__dict__,
                        "budget_exhausted": reduction.exhausted if reduction else False,
                        "rejected_signatures": [item.as_dict() for item in reduction.rejected_signatures] if reduction else [],
                    }}
                    self._safe_write(repro, yaml.safe_dump(redacted({"schema_version": bundle.schema_version, "run_id": bundle.run_id, "requirements": requirements.model_dump(mode="json"), "compatibility": bundle.compatibility.model_dump(mode="json"), "execution": repro_execution, "scenario": reduced.model_dump(mode="json")}), sort_keys=False))
                    bundle.artifacts.append(Artifact(kind="scenario", path=str(repro), redacted=True, sensitivity="redacted", description="redacted minimized diagnostic with schedule; replay bundle.json", run_id=bundle.run_id))
                    break
        if not bundle.compatibility.capability_fingerprint:
            bundle.compatibility = Compatibility(browser=self.config.browser, headless=headless, webmcp_profile=self.config.webmcp_profile, groups=requirements)
        if bundle.execution["schedule"] is None and chosen:
            bundle.execution.update({"schedule": self.schedule_descriptor(scenario, chosen[0] or [(action.offset_ms, actor, action) for actor, actions in scenario.actors.items() for action in actions]), "schedule_index": 0})
            bundle.execution["scheduler"]["selected_logical_schedule"] = bundle.execution["schedule"]
        assertion_events = [
            event for event in (bundle.trace.events if bundle.trace else [])
            if event.type.startswith("tool_contract.assertion.")
        ]
        if assertion_events:
            bundle.tool_contract_expectations["semantic_assertions"] = {
                "status": "failed" if any(event.type.endswith(".fail") for event in assertion_events) else "passed",
                "checks": [redacted(event.data | {"tool": event.name, "passed": event.type.endswith(".pass")}) for event in assertion_events],
            }
            if any(event.type.endswith(".fail") for event in assertion_events):
                bundle.tool_contract_expectations["status"] = "failed"
                bundle.tool_contract_expectations["passed"] = False
        bundle.execution["scheduler"]["effective_schedule_count"] = len(effective_schedules)
        bundle.result = {"passed": last_error is None, "error": str(last_error) if last_error else None, "error_code": getattr(last_error, "code", None) if last_error else None, "schedules": len(chosen), "requested_schedules": len(chosen), "effective_unique_schedules": len(effective_schedules), "seed": seed, "contract_version": bundle.schema_version, "engine_version": bundle.compatibility.engine_version,
                         "tool_inventory_fingerprint": bundle.compatibility.tool_inventory_fingerprint,
                         "tool_contract_drift": bundle.tool_contract_drift,
                         "tool_contract_replay_decision": bundle.tool_contract_replay_decision,
                         "tool_contract_expectations": bundle.tool_contract_expectations}
        bundle.replay_command = ["webmcp", "replay", str(output_root / "bundle.json"), "--run-id", bundle.run_id]
        if allow_mutations:
            bundle.replay_command.append("--allow-mutations")
        if strict_tool_contracts:
            bundle.replay_command.append("--strict-tool-contracts")
        if last_error is not None:
            bundle.result["failure_handoff"] = failure_handoff(bundle, output_root / "bundle.json")
        if bundle.trace:
            trace_path = output_root / "trace.json"
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            self._safe_write(trace_path, json.dumps(redacted(bundle.trace.model_dump(mode="json")), indent=2, default=str))
            bundle.artifacts.append(Artifact(kind="trace", path=str(trace_path), redacted=True, sensitivity="redacted"))
        return bundle

    def replay_bundle(self, path: Path, *, run_id: str | None = None) -> tuple[Scenario, int, RunBundle]:
        try:
            saved = RunBundle.model_validate_json(path.read_text())
        except Exception as error:
            raise CommandError(f"unsafe replay rejected: {path} is not a versioned run bundle: {error}") from error
        if saved.schema_version != "2.0" or saved.compatibility.runner != "webmcp-resilience/2" or not saved.scenario:
            raise CommandError("unsafe replay rejected: incompatible bundle version or missing scenario")
        self._validate_replay_inventory_evidence(saved)
        if saved.compatibility.browser and saved.compatibility.browser != self.config.browser:
            raise CommandError(f"unsafe replay rejected: bundle requires {saved.compatibility.browser}, configured browser is {self.config.browser}")
        if saved.compatibility.webmcp_profile != self.config.webmcp_profile:
            raise CommandError(
                "unsafe replay rejected: bundle requires WebMCP argument profile "
                f"{saved.compatibility.webmcp_profile!r}, configured profile is {self.config.webmcp_profile!r}"
            )
        scenario = Scenario.model_validate(saved.scenario)
        self._validate_scenario_semantics(scenario)
        requirements = saved.requirements
        if requirements != saved.compatibility.groups or requirements.model_dump() != saved.execution.get("requirements"):
            raise CommandError("unsafe replay rejected: inconsistent compatibility requirements")
        if any(SUPPORTED_COMPATIBILITY_GROUPS.get(key) != value for key, value in requirements.model_dump().items()):
            raise CommandError("unsafe replay rejected: incompatible compatibility groups")
        fault_configuration = [fault.model_dump(mode="json") for fault in scenario.faults]
        if saved.faults != fault_configuration or saved.execution.get("fault_configuration") != saved.faults:
            raise CommandError("unsafe replay rejected: fault configuration differs from its scenario")
        if saved.execution.get("approval_policy") != [approval.model_dump(mode="json") for approval in saved.approvals]:
            raise CommandError("unsafe replay rejected: approval policy differs from the recorded bundle")
        scheduler = saved.execution.get("scheduler")
        recorded_schedule = scheduler.get("selected_logical_schedule") if isinstance(scheduler, dict) else saved.execution.get("schedule")
        if not isinstance(recorded_schedule, list):
            raise CommandError("unsafe replay rejected: bundle has no replayable recorded schedule")
        if isinstance(scheduler, dict) and scheduler.get("version") != "1.0":
            raise CommandError("unsafe replay rejected: unsupported scheduler evidence version")
        if isinstance(scheduler, dict) and isinstance(saved.execution.get("schedule"), list) and recorded_schedule != saved.execution["schedule"]:
            raise CommandError("unsafe replay rejected: scheduler evidence disagrees with execution schedule")
        self.schedule_from_descriptor(scenario, recorded_schedule)
        return scenario, int(saved.execution.get("seed", saved.result.get("seed", 0))), saved

    @staticmethod
    def _validate_replay_inventory_evidence(saved: RunBundle) -> None:
        """Verify saved inventory evidence before replay can open a browser."""
        policy = saved.inventory_contract.get("fingerprint_policy")
        reasons: list[str] = []
        if not isinstance(policy, dict) or not isinstance(policy.get("include_descriptions"), bool):
            reasons.append("missing or malformed saved fingerprint_policy")
            include_descriptions = False
        else:
            include_descriptions = policy["include_descriptions"]
        recorded_contract_fingerprint = saved.inventory_contract.get("inventory_fingerprint")
        recorded_compatibility_fingerprint = saved.compatibility.tool_inventory_fingerprint
        if not isinstance(recorded_contract_fingerprint, str) or not recorded_contract_fingerprint:
            reasons.append("missing or malformed inventory_contract.inventory_fingerprint")
        if not isinstance(recorded_compatibility_fingerprint, str) or not recorded_compatibility_fingerprint:
            reasons.append("missing or malformed compatibility.tool_inventory_fingerprint")
        rebuilt = build_inventory_contract(
            saved.tool_inventory, include_descriptions=include_descriptions
        )
        rebuilt_fingerprint = rebuilt["inventory_fingerprint"]
        if isinstance(recorded_contract_fingerprint, str) and rebuilt_fingerprint != recorded_contract_fingerprint:
            reasons.append("stored tool_inventory does not match inventory_contract fingerprint")
        if isinstance(recorded_compatibility_fingerprint, str) and rebuilt_fingerprint != recorded_compatibility_fingerprint:
            reasons.append("stored tool_inventory does not match compatibility fingerprint")
        if (
            isinstance(recorded_contract_fingerprint, str)
            and isinstance(recorded_compatibility_fingerprint, str)
            and recorded_contract_fingerprint != recorded_compatibility_fingerprint
        ):
            reasons.append("inventory_contract and compatibility fingerprints disagree")
        if reasons:
            raise ToolContractEvidenceIntegrityError(saved, reasons)

    async def replay(self, path: Path, *, run_id: str | None = None, headless: bool = True,
                     allow_mutations: bool = False,
                     strict_tool_contracts: bool = False,
                     allowed_target_origins: Iterable[str] | None = None) -> RunBundle:
        """Replay a contract bundle without any frontend-specific filesystem shim."""
        scenario, seed, saved = self.replay_bundle(path)
        boundary = saved.browser_environment.get("state_boundary", {})
        if isinstance(boundary, dict):
            if boundary.get("reset_configured") and not self.config.reset_script:
                raise CommandError("unsafe replay rejected: saved run required a reset_script but the current config has none")
            if boundary.get("setup_configured") and not self.config.setup_script:
                raise CommandError("unsafe replay rejected: saved run required a setup_script but the current config has none")
        replay_api = CommandAPI(_recorded_replay_config(self.config, saved), output_dir=self.output_dir)
        fingerprint_policy = saved.inventory_contract.get("fingerprint_policy")
        saved_policy = (
            fingerprint_policy if isinstance(fingerprint_policy, dict)
            and isinstance(fingerprint_policy.get("include_descriptions"), bool)
            else None
        )
        include_contract_descriptions = bool(saved_policy.get("include_descriptions")) if saved_policy else False
        result = await replay_api.run(None, scenario=scenario, run_id=run_id, headless=headless,
                                allow_mutations=allow_mutations,
                                adversarial=bool(saved.execution["adversarial"]), seed=seed,
                                recorded_schedule=(saved.execution.get("scheduler", {}).get("selected_logical_schedule", saved.execution["schedule"])
                                                   if isinstance(saved.execution.get("scheduler"), dict) else saved.execution["schedule"]),
                                expected_capability_fingerprint=saved.compatibility.capability_fingerprint,
                                expected_webmcp_argument_mode=saved.compatibility.webmcp_argument_mode,
                                expected_tool_inventory_fingerprint=saved.compatibility.tool_inventory_fingerprint,
                                expected_tool_inventory=saved.tool_inventory,
                                contract_include_descriptions=include_contract_descriptions,
                                replay_fingerprint_policy=saved_policy,
                                strict_tool_contracts=strict_tool_contracts,
                                allowed_target_origins=allowed_target_origins)
        result.command = "replay"
        result.result["tool_contract_replay_decision"] = result.tool_contract_replay_decision
        return result


def diff_bundles(left: RunBundle, right: RunBundle) -> dict[str, Any]:
    """Stable semantic comparison for CI and the console baseline lane."""
    left_events = [(event.actor, event.type, event.name) for event in (left.trace.events if left.trace else [])]
    right_events = [(event.actor, event.type, event.name) for event in (right.trace.events if right.trace else [])]
    browser_left = {"browser": left.compatibility.browser, "version": left.compatibility.browser_version,
                    "headless": left.compatibility.headless, "fingerprint": left.compatibility.capability_fingerprint}
    browser_right = {"browser": right.compatibility.browser, "version": right.compatibility.browser_version,
                     "headless": right.compatibility.headless, "fingerprint": right.compatibility.capability_fingerprint}
    def recorded_fingerprint_policy(bundle: RunBundle) -> dict[str, bool]:
        policy = bundle.inventory_contract.get("fingerprint_policy", {})
        return {
            "include_descriptions": (
                policy.get("include_descriptions") is True
                if isinstance(policy, dict)
                else False
            )
        }

    def recorded_inventory_fingerprint(bundle: RunBundle) -> str | None:
        fingerprint = bundle.inventory_contract.get("inventory_fingerprint")
        return (
            fingerprint
            if isinstance(fingerprint, str)
            else bundle.compatibility.tool_inventory_fingerprint
        )

    left_policy = recorded_fingerprint_policy(left)
    right_policy = recorded_fingerprint_policy(right)
    if left_policy == right_policy:
        tool_contract_drift = compare_tool_inventories(
            left.tool_inventory,
            right.tool_inventory,
            include_descriptions=left_policy["include_descriptions"],
        )
        tool_contract_drift["fingerprint_policy"] = left_policy
        tool_contract_drift["policy_mismatch"] = None
    else:
        # There is no single valid canonicalization for this pair. Report the
        # saved evidence verbatim instead of creating fingerprints under one
        # side's policy that would contradict the other bundle.
        tool_contract_drift = {
            "version": "1.0",
            "status": "policy_mismatch",
            "changed": True,
            "policy_impact": "policy_mismatch",
            "fingerprint_policy": None,
            "policy_mismatch": {
                "baseline": left_policy,
                "current": right_policy,
            },
            "baseline_inventory_fingerprint": recorded_inventory_fingerprint(left),
            "current_inventory_fingerprint": recorded_inventory_fingerprint(right),
            "added_tools": [],
            "removed_tools": [],
            "changed_input_schemas": [],
            "changed_output_schemas": [],
            "changed_annotations": [],
            "descriptive_only_changes": [],
            "unchanged_tools": [],
            "details": {},
        }
    def result_codes(bundle: RunBundle) -> list[str]:
        return [
            str((event.data.get("result") or {}).get("code"))
            for event in (bundle.trace.events if bundle.trace else [])
            if event.type == "tool.result" and isinstance(event.data.get("result"), dict) and "code" in event.data["result"]
        ]

    def invariant_outcome(bundle: RunBundle) -> dict[str, Any]:
        failures = [
            {"type": event.type, "expression": event.data.get("expression"), "state": redacted(event.state_snapshot)}
            for event in (bundle.trace.events if bundle.trace else [])
            if event.type in {"invariant.fail", "result_invariant.fail", "tool_contract.assertion.fail"}
        ]
        return {"passed": bundle.result.get("passed"), "failures": failures}

    def state_observations(bundle: RunBundle) -> list[dict[str, Any]]:
        observations = [redacted(event.state_snapshot) for event in (bundle.trace.events if bundle.trace else []) if event.state_snapshot]
        deltas: list[dict[str, Any]] = []
        previous: dict[str, Any] = {}
        for current in observations:
            added = {key: current[key] for key in sorted(set(current) - set(previous))}
            removed = {key: previous[key] for key in sorted(set(previous) - set(current))}
            changed = {key: {"before": previous[key], "after": current[key]} for key in sorted(set(previous) & set(current)) if previous[key] != current[key]}
            deltas.append({"added": added, "removed": removed, "changed": changed})
            previous = current
        return deltas

    def schedule(bundle: RunBundle) -> Any:
        scheduler = bundle.execution.get("scheduler")
        return scheduler.get("selected_logical_schedule") if isinstance(scheduler, dict) else bundle.execution.get("schedule")

    def artifact_metadata(bundle: RunBundle) -> list[dict[str, Any]]:
        return sorted([{"kind": item.kind, "redacted": item.redacted, "sensitivity": item.sensitivity, "description": item.description} for item in bundle.artifacts], key=lambda item: (item["kind"], str(item["description"])))

    compatibility_left = {
        "requirements": left.requirements.model_dump(mode="json"),
        "browser": browser_left,
        "approval_policy": left.execution.get("approval_policy", [item.model_dump(mode="json") for item in left.approvals]),
        "tool_inventory_fingerprint": left.compatibility.tool_inventory_fingerprint,
        "tool_contract_replay_decision": left.tool_contract_replay_decision,
    }
    compatibility_right = {
        "requirements": right.requirements.model_dump(mode="json"),
        "browser": browser_right,
        "approval_policy": right.execution.get("approval_policy", [item.model_dump(mode="json") for item in right.approvals]),
        "tool_inventory_fingerprint": right.compatibility.tool_inventory_fingerprint,
        "tool_contract_replay_decision": right.tool_contract_replay_decision,
    }
    behaviour_left = {
        "invariant_outcome": invariant_outcome(left),
        "observed_state_deltas": state_observations(left),
        "schedule_order": schedule(left),
        "faults": redacted(left.faults),
        "tool_result_codes": result_codes(left),
        "artifacts": artifact_metadata(left),
    }
    behaviour_right = {
        "invariant_outcome": invariant_outcome(right),
        "observed_state_deltas": state_observations(right),
        "schedule_order": schedule(right),
        "faults": redacted(right.faults),
        "tool_result_codes": result_codes(right),
        "artifacts": artifact_metadata(right),
    }
    compatibility_changes = {
        key: {"left": compatibility_left[key], "right": compatibility_right[key]}
        for key in compatibility_left if compatibility_left[key] != compatibility_right[key]
    }
    if tool_contract_drift["changed"]:
        compatibility_changes["tool_contracts"] = tool_contract_drift
    behavioural_changes = {
        key: {"left": behaviour_left[key], "right": behaviour_right[key]}
        for key in behaviour_left if behaviour_left[key] != behaviour_right[key]
    }
    return {"schema_version": "1.0", "contract_version": "1.0", "engine_version": left.engine_version, "left": left.run_id, "right": right.run_id,
            "result_changed": left.result.get("passed") != right.result.get("passed"),
            "browser_changed": browser_left != browser_right, "browser_left": browser_left, "browser_right": browser_right,
            "events_only_left": [item for item in left_events if item not in right_events],
            "events_only_right": [item for item in right_events if item not in left_events],
            "compatibility_drift": {"changed": bool(compatibility_changes), "changes": compatibility_changes},
            "tool_contract_drift": tool_contract_drift,
            "tool_contract_replay_decisions": {"left": left.tool_contract_replay_decision, "right": right.tool_contract_replay_decision},
            "behavioural_drift": {"changed": bool(behavioural_changes), "changes": behavioural_changes},
            "semantic": {"left": {"compatibility": compatibility_left, "behaviour": behaviour_left}, "right": {"compatibility": compatibility_right, "behaviour": behaviour_right}},
            }
