"""Policy-enforcing local MCP control adapter.

The adapter is deliberately a thin transport seam over :class:`CommandAPI`.
It does not import the browser or resilience engine and it does not create an
agent-specific execution path.  The stdio transport supports the MCP JSON-RPC
messages used by MCP clients and a small legacy one-line form for local smoke
tests.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal
from urllib.parse import urlsplit, urlunsplit

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .commands import CommandAPI, CommandError, diff_bundles, load_scenario
from .models.bundle import (
    ApprovalRecord,
    Artifact,
    BUNDLE_VERSION,
    ENGINE_VERSION,
    RunBundle,
    SUPPORTED_COMPATIBILITY_GROUPS,
    redact_recursive,
    validate_identifier,
)
from .models.scenario import SUPPORTED_FAULT_TIMINGS, Scenario
from .security import agent_policy_snapshot, validate_navigation_url


CONTROL_CONTRACT_VERSION = "1.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_ACTORS = ("agent", "human", "system")
SUPPORTED_ACTIONS = ("invoke", "retry", "cancel", "click", "fill", "select", "navigate", "wait")
INVARIANT_OPERATORS = ("<", "<=", "==", "!=", ">", ">=")
TEMPLATE_FOCUSES = (
    "basic", "concurrency", "retry", "cancellation", "latency",
    "timeout", "http_error", "duplicate_invocation", "navigation",
)


def _canonical_origin(value: str) -> str:
    """Validate and normalise an HTTP(S) origin, dropping path/query data."""
    if not isinstance(value, str):
        raise ValueError("target origin must be a string")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("target origin must be an HTTP(S) origin")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("target origin must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("target origin must not include a path, query, or fragment")
    port = parsed.port
    hostname = parsed.hostname.lower()
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def _url_origin(value: str) -> str:
    """Return the origin portion of an absolute URL while keeping its route."""
    parsed = urlsplit(value)
    return _canonical_origin(urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))


@dataclass(frozen=True, init=False)
class AgentPolicy:
    """Immutable authority supplied by the human/operator at server startup.

    ``target_origin`` remains accepted as a compatibility spelling for the
    single-origin case. New callers should use ``allowed_target_origins``.
    No tool request can modify any of these values.
    """

    project_root: Path
    allowed_target_origins: tuple[str, ...]
    artifact_sensitivity: str
    concurrency_limit: int
    mutation_permission: bool
    approval_context: str

    def __init__(
        self,
        project_root: Path,
        target_origin: str | Iterable[str] | None = None,
        *,
        allowed_target_origins: Iterable[str] | None = None,
        artifact_sensitivity: str = "redacted_text_only",
        concurrency_limit: int = 1,
        mutation_permission: bool = False,
        approval_context: str = "operator started local control server",
    ) -> None:
        root = Path(project_root).resolve()
        if not root.is_dir():
            raise ValueError("project_root must be an existing directory")
        if isinstance(allowed_target_origins, str):
            supplied = [allowed_target_origins]
        else:
            supplied = list(allowed_target_origins or ())
        if target_origin is not None:
            supplied.extend([target_origin] if isinstance(target_origin, str) else list(target_origin))
        if not supplied:
            raise ValueError("at least one allowed target origin is required")
        origins = tuple(dict.fromkeys(_canonical_origin(origin) for origin in supplied))
        if concurrency_limit < 1:
            raise ValueError("concurrency_limit must be positive")
        if artifact_sensitivity not in {"redacted_text_only", "include_sensitive"}:
            raise ValueError("artifact_sensitivity must be redacted_text_only or include_sensitive")
        object.__setattr__(self, "project_root", root)
        object.__setattr__(self, "allowed_target_origins", origins)
        object.__setattr__(self, "artifact_sensitivity", artifact_sensitivity)
        object.__setattr__(self, "concurrency_limit", int(concurrency_limit))
        object.__setattr__(self, "mutation_permission", bool(mutation_permission))
        object.__setattr__(self, "approval_context", approval_context)

    @property
    def target_origin(self) -> str:
        """Compatibility view of the first configured origin."""
        return self.allowed_target_origins[0]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyRequest(_Request):
    pass


class ScenarioRequest(_Request):
    scenario_id: str | None = None
    scenario_yaml: str | None = None
    scenario: dict[str, Any] | None = None
    run_id: str | None = None

    @model_validator(mode="after")
    def one_source(self) -> "ScenarioRequest":
        if sum(value is not None for value in (self.scenario_id, self.scenario_yaml, self.scenario)) != 1:
            raise ValueError("provide exactly one of scenario_id, scenario_yaml, or scenario")
        return self

    @field_validator("run_id")
    @classmethod
    def valid_run_id(cls, value: str | None) -> str | None:
        return validate_identifier(value, label="run id") if value is not None else None


class PreflightRequest(_Request):
    path: str = "/"
    run_id: str | None = None

    @field_validator("path")
    @classmethod
    def relative_site_path(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme or parsed.netloc or "\\" in value or "%2f" in value.lower() or
                "%5c" in value.lower() or "%2e" in value.lower() or ".." in Path(parsed.path).parts):
            raise ValueError("preflight path must be relative to an allowed target origin")
        return value or "/"

    @field_validator("run_id")
    @classmethod
    def valid_run_id(cls, value: str | None) -> str | None:
        return validate_identifier(value, label="run id") if value is not None else None


class ReplayRequest(_Request):
    bundle_id: str | None = None
    source_run_id: str | None = None
    run_id: str | None = None

    @model_validator(mode="after")
    def one_bundle_reference(self) -> "ReplayRequest":
        if (self.bundle_id is None) == (self.source_run_id is None):
            raise ValueError("provide exactly one of bundle_id or source_run_id")
        return self

    @field_validator("run_id")
    @classmethod
    def valid_run_id(cls, value: str | None) -> str | None:
        return validate_identifier(value, label="run id") if value is not None else None


class RunRequest(ScenarioRequest):
    adversarial: bool = False
    seed: int = 0


class RunIdRequest(_Request):
    run_id: str

    @field_validator("run_id")
    @classmethod
    def valid_run_id(cls, value: str) -> str:
        return validate_identifier(value, label="run id")


class TraceSliceRequest(RunIdRequest):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = None
    actor: str | None = None
    event_type: str | None = Field(default=None, validation_alias=AliasChoices("event_type", "type", "event"))
    name: str | None = None


class ListRunsRequest(_Request):
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = None
    status: Literal["all", "passed", "failed"] = "all"
    command: str | None = None
    query: str | None = None


class FailureRequest(RunIdRequest):
    artifact: Literal["failure", "repro", "scenario", "screenshot"] = Field(
        default="failure", validation_alias=AliasChoices("artifact", "kind")
    )


class ArtifactMetadataRequest(RunIdRequest):
    artifact: str | None = Field(default=None, validation_alias=AliasChoices("artifact", "kind", "artifact_id"))


class ScenarioTemplateRequest(_Request):
    discovery_run_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("discovery_run_id", "preflight_run_id"),
    )
    tool_name: str | None = None
    name: str = "agent-review-template"
    focus: str = "basic"

    @model_validator(mode="after")
    def valid_template_request(self) -> "ScenarioTemplateRequest":
        if self.discovery_run_id is None:
            raise ValueError("discovery_run_id is required; run preflight first")
        validate_identifier(self.discovery_run_id, label="run id")
        if self.focus not in TEMPLATE_FOCUSES:
            raise ValueError(f"unsupported template focus: {self.focus}")
        if not self.name.strip() or "\n" in self.name or "\r" in self.name:
            raise ValueError("template name must be a non-empty single-line value")
        return self

    @field_validator("discovery_run_id")
    @classmethod
    def valid_discovery_run_id(cls, value: str | None) -> str | None:
        return validate_identifier(value, label="run id") if value is not None else None


class DiffRequest(_Request):
    left_run_id: str
    right_run_id: str

    _valid_left = field_validator("left_run_id")(lambda value: validate_identifier(value, label="run id"))
    _valid_right = field_validator("right_run_id")(lambda value: validate_identifier(value, label="run id"))


class ControlResult(BaseModel):
    """Versioned public result envelope used by every adapter operation."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[CONTROL_CONTRACT_VERSION] = CONTROL_CONTRACT_VERSION
    contract_version: Literal[CONTROL_CONTRACT_VERSION] = CONTROL_CONTRACT_VERSION
    engine_version: str = ENGINE_VERSION
    operation: str
    run_id: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    bundle: dict[str, Any] | None = None


def control_error(operation: str | None, message: str, *, code: str = "invalid_request",
                  details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Stable, redacted error envelope shared by stdio and tool callers."""
    return {
        "version": CONTROL_CONTRACT_VERSION,
        "operation": operation,
        "contract_version": CONTROL_CONTRACT_VERSION,
        "engine_version": ENGINE_VERSION,
        "error": {
            "code": code,
            "message": redact_recursive(message),
            **({"details": redact_recursive(details)} if details is not None else {}),
        },
    }


def redact_text(value: str) -> str:
    """Redact structured or line-oriented textual evidence before returning it."""
    try:
        parsed = yaml.safe_load(value)
        if isinstance(parsed, (dict, list)):
            return yaml.safe_dump(redact_recursive(parsed), sort_keys=False)
    except Exception:
        pass
    redacted = re.sub(
        r"(?im)^(\s*(?:authorization|cookie|token|secret|password|credential|credentials|api[-_]?key|access[-_]?key|access[-_]?token|signature|sig|x[-_]?amz[-_]?signature)\s*:\s*).*$",
        r"\1[REDACTED]",
        value,
    )
    redacted = re.sub(
        r"(?i)\b(authorization|cookie|token|secret|password|credential|credentials|api[-_]?key|access[-_]?key|access[-_]?token|signature|sig|x[-_]?amz[-_]?signature)\s*=\s*([^\s&,]+)",
        r"\1=[REDACTED]",
        redacted,
    )
    return str(redact_recursive(redacted))


REQUEST_MODELS: dict[str, type[_Request]] = {
    "get_capabilities": EmptyRequest,
    "preflight": PreflightRequest,
    "list_scenarios": EmptyRequest,
    "validate_scenario": ScenarioRequest,
    "run_scenario": RunRequest,
    "replay_run": ReplayRequest,
    "list_runs": ListRunsRequest,
    "get_run_summary": RunIdRequest,
    "get_trace_slice": TraceSliceRequest,
    "get_failure_or_repro": FailureRequest,
    "get_artifact_metadata": ArtifactMetadataRequest,
    "diff_runs": DiffRequest,
    "create_scenario_template": ScenarioTemplateRequest,
}

TOOL_DESCRIPTIONS = {
    "get_capabilities": "Return framework and immutable agent-policy capabilities.",
    "preflight": "Collect redacted, non-mutating WebMCP readiness evidence.",
    "list_scenarios": "List repository-local scenario YAML files.",
    "validate_scenario": "Validate a repository scenario or an in-memory scenario declaration.",
    "run_scenario": "Run a scenario through the normal CommandAPI and return a redacted bundle.",
    "replay_run": "Replay a saved compatible run bundle through the normal CommandAPI.",
    "list_runs": "List saved repository-local run summaries with stable pagination and filters.",
    "get_run_summary": "Return a redacted summary of a saved run bundle.",
    "get_trace_slice": "Return a paginated, filtered, redacted slice of a saved trace.",
    "get_failure_or_repro": "Retrieve only redacted textual failure or minimized reproduction evidence.",
    "get_artifact_metadata": "List safe metadata for saved artifacts without exposing their contents.",
    "diff_runs": "Compare two saved run bundles by repository-local run id.",
    "create_scenario_template": "Create deterministic ordinary YAML from discovered tools and supported DSL features.",
}


def tool_definitions() -> list[dict[str, Any]]:
    """Return explicit MCP tool schemas generated from typed request models."""
    return [
        {"name": name, "description": TOOL_DESCRIPTIONS[name], "inputSchema": model.model_json_schema()}
        for name, model in REQUEST_MODELS.items()
    ]


class LocalMCPControlAdapter:
    """Policy-enforcing transport adapter over :class:`CommandAPI`."""

    def __init__(self, api: CommandAPI, policy: AgentPolicy) -> None:
        self.api, self.policy = api, policy
        configured = _canonical_origin(api.config.base_url)
        if configured not in policy.allowed_target_origins:
            raise ValueError("configured target origin is not allowed by agent policy")
        output_dir = api.output_dir.resolve()
        try:
            output_dir.relative_to(policy.project_root)
        except ValueError as error:
            raise ValueError("CommandAPI output_dir must be inside the configured project root") from error
        self._limit = asyncio.Semaphore(policy.concurrency_limit)

    def _relative(self, identifier: str, *, suffixes: tuple[str, ...]) -> Path:
        if not isinstance(identifier, str) or not identifier or Path(identifier).is_absolute() or "\\" in identifier:
            raise CommandError("agent identifier must be repository-relative")
        raw = Path(identifier)
        if any(part in {"", ".", ".."} for part in raw.parts):
            raise CommandError("agent path traversal rejected")
        candidate = (self.policy.project_root / raw).resolve()
        try:
            candidate.relative_to(self.policy.project_root)
        except ValueError as error:
            raise CommandError("agent path traversal rejected") from error
        if candidate.suffix.lower() not in suffixes:
            raise CommandError("agent identifier has an unsupported file type")
        return candidate

    def _scenario(self, request: ScenarioRequest) -> Scenario:
        if request.scenario_id is not None:
            scenario = load_scenario(self._relative(request.scenario_id, suffixes=(".yaml", ".yml")))
        elif request.scenario_yaml is not None:
            try:
                scenario = Scenario.model_validate(yaml.safe_load(request.scenario_yaml))
            except Exception as error:
                raise CommandError(f"invalid scenario YAML: {error}") from error
        else:
            try:
                scenario = Scenario.model_validate(request.scenario)
            except Exception as error:
                raise CommandError(f"invalid scenario: {error}") from error
        self._validate_target(scenario)
        return scenario

    def _validate_target(self, scenario: Scenario) -> None:
        allowed = set(self.policy.allowed_target_origins)
        urls = [scenario.url, *(fault.url for fault in scenario.faults if fault.type == "navigation"),
                *(action.value for actions in scenario.actors.values() for action in actions if action.action == "navigate")]
        for value in urls:
            if not value:
                continue
            try:
                validate_navigation_url(value, self.api.config.base_url, allowed)
            except ValueError as error:
                raise CommandError(f"scenario target is outside the immutable agent policy origin or is untrusted: {error}") from error

    def _bundle(self, run_id: str) -> RunBundle:
        return self.api.load_bundle(run_id)

    @staticmethod
    def _cursor(value: str | None) -> int:
        if value is None:
            return 0
        if not value.isdigit():
            raise CommandError("invalid pagination cursor")
        return int(value)

    def _record_agent_policy(self, bundle: RunBundle) -> None:
        """Stamp every agent-triggered execution with operator authority."""
        bundle.approvals.append(
            ApprovalRecord(
                authority="mutation_authorized" if self.policy.mutation_permission else "read_only",
                source="agent_policy",
                reason=self.policy.approval_context,
            )
        )
        bundle.agent_policy = agent_policy_snapshot(
            self.policy.allowed_target_origins,
            mutation_permission=self.policy.mutation_permission,
            artifact_sensitivity=self.policy.artifact_sensitivity,
            concurrency_limit=self.policy.concurrency_limit,
            authority_source="agent_policy",
        )
        bundle.execution["approval_policy"] = [approval.model_dump(mode="json") for approval in bundle.approvals]
        bundle.execution["agent_policy"] = bundle.agent_policy

    @staticmethod
    def _artifact_id(run_id: str, index: int, artifact: Artifact) -> str:
        return f"{run_id}/{index}:{artifact.kind}"

    def _artifact_metadata(self, bundle: RunBundle, artifact: str | None = None) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for index, item in enumerate(bundle.artifacts):
            identifier = self._artifact_id(bundle.run_id, index, item)
            if artifact is not None and artifact not in {item.kind, identifier}:
                continue
            selected.append({
                "id": identifier,
                "kind": item.kind,
                "sensitivity": item.sensitivity,
                "redacted": item.redacted,
                "description": item.description,
                "retrievable": item.kind in {"failure", "scenario"} and item.redacted and item.sensitivity == "redacted",
            })
        if artifact is not None and not selected:
            raise CommandError("artifact not found")
        return selected

    def _discovered_tool(self, discovery: RunBundle, requested: str | None) -> str:
        discovered = sorted({
            str(tool["name"])
            for tool in discovery.tool_inventory
            if isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool["name"]
        })
        if not discovered:
            raise CommandError("discovery bundle contains no discovered tools")
        if requested is not None:
            if requested not in discovered:
                raise CommandError("template tool must be present in the discovery bundle")
            return requested
        return discovered[0]

    def _scenario_template(self, request: ScenarioTemplateRequest) -> dict[str, Any]:
        discovery = self._bundle(request.discovery_run_id or "")
        if discovery.command != "preflight":
            raise CommandError("discovery_run_id must reference a preflight bundle")
        tool = self._discovered_tool(discovery, request.tool_name)
        template: dict[str, Any] = {
            "name": request.name,
            "url": "/",
            "actors": {"agent": [{"at": "0ms", "invoke": tool, "args": {}}]},
            "compatibility": {
                "schema": "webmcp-resilience/scenario-1",
                "requires": dict(SUPPORTED_COMPATIBILITY_GROUPS),
            },
        }
        if request.focus == "concurrency":
            template["actors"] = {
                "agent": [{"at": "0ms", "invoke": tool, "args": {}}],
                "human": [{"at": "0ms", "invoke": tool, "args": {}}],
            }
        elif request.focus == "retry":
            template["actors"]["agent"].append({"at": "1ms", "retry": tool, "args": {}})
        elif request.focus in SUPPORTED_FAULT_TIMINGS:
            timing = "before_invoke" if "before_invoke" in SUPPORTED_FAULT_TIMINGS[request.focus] else sorted(SUPPORTED_FAULT_TIMINGS[request.focus])[0]
            fault: dict[str, Any] = {"type": request.focus, "tool": tool, "at": timing}
            if request.focus in {"latency", "timeout"}:
                fault["duration_ms"] = 100
            if request.focus in {"http_error"}:
                fault["status"] = 503
            if request.focus in {"navigation"}:
                fault["url"] = "/"
            template["faults"] = [fault]
        return template

    def _source_run_id(self, reference: str) -> str:
        """Resolve a run id or ``.webmcp/runs/<id>/bundle.json`` safely."""
        if not isinstance(reference, str) or not reference:
            raise CommandError("bundle identifier must be repository-relative")
        try:
            return validate_identifier(reference, label="run id")
        except ValueError:
            path = self._relative(reference, suffixes=(".json",))
            relative = path.relative_to(self.policy.project_root)
            parts = relative.parts
            if len(parts) != 4 or parts[:2] != (".webmcp", "runs") or parts[3] != "bundle.json":
                raise CommandError("bundle identifier must name a saved repository-local run bundle")
            return validate_identifier(parts[2], label="run id")

    def _public_bundle(self, bundle: RunBundle) -> dict[str, Any]:
        """Project a bundle without exposing host paths or sensitive artifacts."""
        payload = bundle.persisted_dict()
        payload["artifacts"] = [
            {
                "kind": artifact.kind,
                "id": self._artifact_id(bundle.run_id, index, artifact),
                # Preserve the RunBundle shape while never exposing the host
                # filesystem path. The identifier is descriptive only; no MCP
                # operation accepts it as a file path.
                "path": f".webmcp/runs/{bundle.run_id}/{artifact.kind}",
                "sensitivity": artifact.sensitivity,
                "redacted": artifact.redacted,
                "description": artifact.description,
            }
            for index, artifact in enumerate(bundle.artifacts)
        ]
        payload["replay_command"] = ["webmcp", "replay", f".webmcp/runs/{bundle.run_id}/bundle.json", "--json"]
        return payload

    def _result(self, operation: str, bundle: RunBundle) -> dict[str, Any]:
        envelope = ControlResult(
            operation=operation,
            run_id=bundle.run_id,
            engine_version=bundle.engine_version,
            result=redact_recursive(bundle.result),
            bundle=self._public_bundle(bundle),
        )
        return envelope.model_dump(mode="json", exclude_none=True)

    def _parse(self, tool: str, request: dict[str, Any]) -> _Request:
        model = REQUEST_MODELS.get(tool)
        if model is None:
            raise CommandError(f"unknown local control tool: {tool}")
        try:
            return model.model_validate(request)
        except ValidationError as error:
            raise CommandError(f"invalid {tool} request: {error}") from error

    async def call(self, tool: str, request: dict[str, Any] | None = None) -> dict[str, Any]:
        request = dict(request or {})
        tool = {"scenario_template": "create_scenario_template", "create_scenario": "create_scenario_template"}.get(tool, tool)
        # Check before typed parsing so an escalation attempt gets an explicit
        # policy error even when its value is true/false.
        if "allow_mutations" in request:
            raise CommandError("agent mutation escalation is not accepted; policy is fixed at server startup")
        parsed = self._parse(tool, request)

        if tool == "get_capabilities":
            return {
                "version": CONTROL_CONTRACT_VERSION,
                "operation": tool,
                "contract_version": CONTROL_CONTRACT_VERSION,
                "engine_version": ENGINE_VERSION,
                "bundle_version": BUNDLE_VERSION,
                "compatibility_groups": SUPPORTED_COMPATIBILITY_GROUPS,
                "actors": {"supported": list(SUPPORTED_ACTORS), "custom_names": True},
                "actions": list(SUPPORTED_ACTIONS),
                "faults": {name: sorted(timings) for name, timings in sorted(SUPPORTED_FAULT_TIMINGS.items())},
                "invariant_operators": list(INVARIANT_OPERATORS),
                "browser": {
                    "browser": self.api.config.browser,
                    "mode": "headless",
                    "headless": True,
                    "target_origins": list(self.policy.allowed_target_origins),
                },
                "redaction": {
                    "mode": self.policy.artifact_sensitivity,
                    "sensitive_artifacts_blocked": ["screenshot"],
                    "text_evidence_redacted": True,
                    "host_paths_exposed": False,
                },
                "mutation_policy": {
                    "permission": self.policy.mutation_permission,
                    "agent_override": False,
                    "approval_recorded": True,
                },
                "agent_policy": agent_policy_snapshot(
                    self.policy.allowed_target_origins,
                    mutation_permission=self.policy.mutation_permission,
                    artifact_sensitivity=self.policy.artifact_sensitivity,
                    concurrency_limit=self.policy.concurrency_limit,
                    authority_source="agent_policy",
                ),
                "state_observation": {
                    "mode": "state_script" if self.api.config.state_script else "none",
                    "configured_source": self.api.config.state_script,
                    "validation": {
                        "status": "configured_not_checked" if self.api.config.state_script else "not_configured",
                        "valid": None,
                    },
                },
                "policy": {
                    "project_root": ".",
                    "allowed_target_origins": list(self.policy.allowed_target_origins),
                    "mutation_permission": self.policy.mutation_permission,
                    "artifact_sensitivity": self.policy.artifact_sensitivity,
                    "concurrency_limit": self.policy.concurrency_limit,
                },
                "tools": [item["name"] for item in tool_definitions()],
                "tool_contract_drift": {
                    "supported": True,
                    "contract_version": "1.0",
                    "fingerprint_fields": ["name", "input_schema", "output_schema", "annotations"],
                    "descriptions_included": self.api.config.tool_contract_include_descriptions,
                    "replay_rejection_code": "tool_contract_drift",
                    "semantic_scope": "declared structural and observable contracts only",
                },
            }
        if tool == "list_scenarios":
            root = self.policy.project_root / ".webmcp" / "scenarios"
            items: list[str] = []
            if root.exists():
                for path in sorted(root.rglob("*")):
                    if path.suffix.lower() not in {".yaml", ".yml"} or not path.is_file():
                        continue
                    try:
                        relative = path.resolve().relative_to(self.policy.project_root)
                    except ValueError:
                        continue
                    items.append(relative.as_posix())
            return {
                "version": CONTROL_CONTRACT_VERSION,
                "operation": tool,
                "contract_version": CONTROL_CONTRACT_VERSION,
                "engine_version": ENGINE_VERSION,
                "scenarios": items,
            }
        if tool == "validate_scenario":
            assert isinstance(parsed, ScenarioRequest)
            bundle = self.api.validate(scenario=self._scenario(parsed), run_id=parsed.run_id)
            self._record_agent_policy(bundle)
            return self._result(tool, bundle)
        if tool == "preflight":
            assert isinstance(parsed, PreflightRequest)
            async with self._limit:
                bundle = await self.api.preflight(parsed.path, headless=True, run_id=parsed.run_id)
            # Discovery evidence is persisted so template creation can only
            # consume tools observed by the normal browser adapter.
            self._record_agent_policy(bundle)
            self.api.save(bundle)
            return self._result(tool, bundle)
        if tool == "run_scenario":
            assert isinstance(parsed, RunRequest)
            scenario = self._scenario(parsed)
            async with self._limit:
                bundle = await self.api.run(
                    scenario=scenario,
                    run_id=parsed.run_id,
                    headless=True,
                    allow_mutations=self.policy.mutation_permission,
                    adversarial=parsed.adversarial,
                    seed=parsed.seed,
                    allowed_target_origins=self.policy.allowed_target_origins,
                )
            self._record_agent_policy(bundle)
            self.api.save(bundle)
            return self._result(tool, bundle)
        if tool == "list_runs":
            assert isinstance(parsed, ListRunsRequest)
            start = self._cursor(parsed.cursor)
            records: list[RunBundle] = []
            for candidate in sorted(self.api.output_dir.iterdir(), key=lambda item: item.name):
                if not candidate.is_dir() or candidate.name in {".", ".."}:
                    continue
                try:
                    validate_identifier(candidate.name, label="run id")
                    bundle = self.api.load_bundle(candidate.name)
                except (CommandError, ValueError):
                    continue
                status = "passed" if bundle.result.get("passed") is True else "failed" if bundle.result.get("passed") is False else "unknown"
                if parsed.status != "all" and status != parsed.status:
                    continue
                if parsed.command is not None and bundle.command != parsed.command:
                    continue
                scenario_name = str((bundle.scenario or {}).get("name", ""))
                if parsed.query is not None and parsed.query.lower() not in f"{bundle.run_id} {scenario_name}".lower():
                    continue
                records.append(bundle)
            records.sort(key=lambda item: (item.created_at, item.run_id), reverse=True)
            page = records[start:start + parsed.limit]
            end = start + len(page)
            return {
                "version": CONTROL_CONTRACT_VERSION,
                "operation": tool,
                "contract_version": CONTROL_CONTRACT_VERSION,
                "engine_version": ENGINE_VERSION,
                "cursor": str(start),
                "next_cursor": str(end) if end < len(records) else None,
                "total": len(records),
                "runs": [
                    {
                        "run_id": item.run_id,
                        "command": item.command,
                        "status": "passed" if item.result.get("passed") is True else "failed" if item.result.get("passed") is False else "unknown",
                        "scenario": (item.scenario or {}).get("name"),
                        "created_at": item.created_at.isoformat(),
                        "artifact_kinds": sorted({artifact.kind for artifact in item.artifacts}),
                        "has_failure": any(artifact.kind == "failure" for artifact in item.artifacts),
                        "tool_inventory_fingerprint": item.compatibility.tool_inventory_fingerprint,
                        "tool_contract_drift": {
                            "status": item.tool_contract_drift.get("status", "not_compared"),
                            "policy_impact": item.tool_contract_drift.get("policy_impact", "none"),
                        },
                    }
                    for item in page
                ],
            }
        if tool == "replay_run":
            assert isinstance(parsed, ReplayRequest)
            source_run_id = self._source_run_id(parsed.bundle_id or parsed.source_run_id or "")
            async with self._limit:
                bundle = await self.api.replay(
                    self.api.bundle_path(source_run_id),
                    run_id=parsed.run_id,
                    headless=True,
                    allow_mutations=self.policy.mutation_permission,
                    allowed_target_origins=self.policy.allowed_target_origins,
                )
            self._record_agent_policy(bundle)
            self.api.save(bundle)
            return self._result(tool, bundle)
        if tool == "get_run_summary":
            assert isinstance(parsed, RunIdRequest)
            return self._result(tool, self._bundle(parsed.run_id))
        if tool == "get_trace_slice":
            assert isinstance(parsed, TraceSliceRequest)
            bundle = self._bundle(parsed.run_id)
            events = [] if not bundle.trace else bundle.trace.events
            if parsed.actor is not None:
                events = [event for event in events if event.actor == parsed.actor]
            if parsed.event_type is not None:
                events = [event for event in events if event.type == parsed.event_type]
            if parsed.name is not None:
                events = [event for event in events if event.name == parsed.name]
            start = self._cursor(parsed.cursor) if parsed.cursor is not None else parsed.offset
            end = start + parsed.limit
            return {
                "version": CONTROL_CONTRACT_VERSION,
                "operation": tool,
                "contract_version": CONTROL_CONTRACT_VERSION,
                "engine_version": bundle.engine_version,
                "run_id": bundle.run_id,
                "offset": start,
                "next_offset": end if end < len(events) else None,
                "cursor": str(start),
                "next_cursor": str(end) if end < len(events) else None,
                "total": len(events),
                "filters": {"actor": parsed.actor, "event_type": parsed.event_type, "name": parsed.name},
                "events": redact_recursive([event.model_dump(mode="json") for event in events[start:end]]),
            }
        if tool == "get_failure_or_repro":
            assert isinstance(parsed, FailureRequest)
            return await self.get_redacted_artifact(parsed.run_id, parsed.artifact)
        if tool == "get_artifact_metadata":
            assert isinstance(parsed, ArtifactMetadataRequest)
            bundle = self._bundle(parsed.run_id)
            return {
                "version": CONTROL_CONTRACT_VERSION,
                "operation": tool,
                "contract_version": CONTROL_CONTRACT_VERSION,
                "engine_version": bundle.engine_version,
                "run_id": bundle.run_id,
                "artifacts": redact_recursive(self._artifact_metadata(bundle, parsed.artifact)),
            }
        if tool == "diff_runs":
            assert isinstance(parsed, DiffRequest)
            left, right = self._bundle(parsed.left_run_id), self._bundle(parsed.right_run_id)
            return {
                "version": CONTROL_CONTRACT_VERSION,
                "operation": tool,
                "contract_version": CONTROL_CONTRACT_VERSION,
                "engine_version": left.engine_version,
                "diff": redact_recursive(diff_bundles(left, right)),
            }
        if tool == "create_scenario_template":
            assert isinstance(parsed, ScenarioTemplateRequest)
            template = self._scenario_template(parsed)
            safe_template = redact_recursive(template)
            return {
                "version": CONTROL_CONTRACT_VERSION,
                "operation": tool,
                "contract_version": CONTROL_CONTRACT_VERSION,
                "engine_version": ENGINE_VERSION,
                "discovery_run_id": parsed.discovery_run_id,
                "tool_name": template["actors"]["agent"][0]["invoke"],
                "focus": parsed.focus,
                "scenario": safe_template,
                "scenario_yaml": yaml.safe_dump(safe_template, sort_keys=False),
            }
        raise CommandError(f"unknown local control tool: {tool}")

    async def call_enveloped(self, tool: str, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a stable result/error envelope for non-MCP local callers."""
        try:
            return {"ok": True, "result": await self.call(tool, request)}
        except CommandError as error:
            return {"ok": False, "result": None, **control_error(
                tool, str(error), code=getattr(error, "code", "invalid_request"),
                details=getattr(error, "details", None),
            )}
        except Exception as error:
            return {"ok": False, "result": None, **control_error(tool, str(error), code="internal_error")}

    async def get_redacted_artifact(self, run_id: str, kind: str) -> dict[str, Any]:
        """Compatibility helper for textual failure/repro evidence only."""
        bundle = self._bundle(run_id)
        lookup_kind = "scenario" if kind == "repro" else kind
        artifact: Artifact | None = next((item for item in bundle.artifacts if item.kind == lookup_kind), None)
        if artifact is None or lookup_kind not in {"failure", "scenario"} or artifact.sensitivity != "redacted" or not artifact.redacted:
            raise CommandError("sensitive or unavailable artifact denied by agent policy")
        path = Path(artifact.path).resolve()
        try:
            path.relative_to(self.api.output_dir)
        except ValueError as error:
            raise CommandError("artifact traversal rejected") from error
        if not path.is_file():
            raise CommandError("artifact unavailable")
        contents = redact_text(path.read_text()).replace(str(self.policy.project_root), "[PROJECT_ROOT]")
        return {
            "version": CONTROL_CONTRACT_VERSION,
            "operation": "get_failure_or_repro",
            "contract_version": CONTROL_CONTRACT_VERSION,
            "engine_version": bundle.engine_version,
            "run_id": bundle.run_id,
            "artifact": kind,
            "artifact_id": self._artifact_id(bundle.run_id, bundle.artifacts.index(artifact), artifact),
            "contents": contents,
        }


async def serve_stdio(adapter: LocalMCPControlAdapter) -> None:
    """Serve MCP JSON-RPC over newline-delimited stdio."""

    def response(message_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    def error_response(message_id: Any, code: int, message: str, operation: str | None = None,
                       contract_code: str = "invalid_request", details: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {
                "code": code,
                "message": message,
                "data": control_error(operation, message, code=contract_code, details=details),
            },
        }

    for line in sys.stdin:
        if not line.strip():
            continue
        message: Any = None
        try:
            message = json.loads(line)
            # Keep the compact local form usable for smoke tests and backwards
            # compatibility, while MCP clients use JSON-RPC below.
            if "tool" in message:
                result = await adapter.call_enveloped(message["tool"], message.get("arguments"))
                print(json.dumps(result, default=str), flush=True)
                continue
            message_id = message.get("id")
            method = message.get("method")
            if method == "initialize":
                result = {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "webmcp-resilience", "version": ENGINE_VERSION},
                }
            elif method == "notifications/initialized":
                continue
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"version": CONTROL_CONTRACT_VERSION, "contract_version": CONTROL_CONTRACT_VERSION, "engine_version": ENGINE_VERSION, "tools": tool_definitions()}
            elif method == "tools/call":
                params = message.get("params") or {}
                name = params.get("name")
                if not isinstance(name, str):
                    raise CommandError("tools/call requires a tool name")
                result = await adapter.call(name, params.get("arguments") or {})
                result = {
                    "content": [{"type": "text", "text": json.dumps(result, default=str, sort_keys=True)}],
                    "structuredContent": result,
                    "isError": False,
                }
            else:
                print(json.dumps(error_response(message_id, -32601, f"method not found: {method}", method)), flush=True)
                continue
            if "id" in message:
                print(json.dumps(response(message_id, result), default=str), flush=True)
        except CommandError as error:
            message_id = message.get("id") if isinstance(message, dict) else None
            operation = message.get("method") if isinstance(message, dict) else None
            if isinstance(message, dict) and "tool" in message:
                operation = message.get("tool")
            if isinstance(message, dict) and message.get("method") == "tools/call":
                operation = (message.get("params") or {}).get("name")
            print(json.dumps(error_response(
                message_id, -32602, str(error), operation,
                getattr(error, "code", "invalid_request"), getattr(error, "details", None),
            )), flush=True)
        except Exception as error:
            message_id = message.get("id") if isinstance(message, dict) else None
            operation = message.get("method") if isinstance(message, dict) else None
            print(json.dumps(error_response(message_id, -32603, str(error), operation)), flush=True)
