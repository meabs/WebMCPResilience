"""Portable, redacted evidence emitted by every command run.

The bundle is deliberately independent of Typer, Textual and Playwright.  It is
the public interchange format used by CLI, CI and future adapters.
"""
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .scenario import Scenario
from .trace import TraceRun


BUNDLE_VERSION = "2.0"
COMPATIBILITY_VERSION = "webmcp-resilience/2"
ENGINE_VERSION = "0.1.0"
# These are semantic contracts, not a grab-bag of optional feature flags.  A
# replay may proceed only when every recorded group/version is installed.
SUPPORTED_COMPATIBILITY_GROUPS = {
    "scenario": "1",
    "fault_model": "1",
    "invariant_model": "1",
    "trace_model": "1",
    "browser_webmcp_adapter": "1",
}
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_QUERY_SECRET = re.compile(
    r"(?:token|secret|password|credential|credentials|api[-_]?key|signature|sig|x[-_]?amz[-_]?signature|access[-_]?key)",
    re.I,
)
_SENSITIVE_NORMALIZED_KEYS = {
    "authorization", "cookie", "token", "secret", "password", "credential",
    "credentials", "apikey", "signature", "sig", "accesskey", "accesstoken",
    "refreshtoken", "xamzsignature",
}
_INLINE_SECRET = re.compile(
    r"(?i)\b(?:authorization|cookie|token|secret|password|credential|credentials|api[-_]?key|access[-_]?key|access[-_]?token|signature|sig|x[-_]?amz[-_]?signature)\s*([=:])\s*([^\s,;&]+)"
)


def validate_identifier(value: str, *, label: str = "identifier") -> str:
    if not _RUN_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"invalid {label}")
    return value


def redact_url(value: str) -> str:
    """Keep URL shape useful while never persisting credential query values."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    # Userinfo is always a credential boundary, even when there is no query.
    # Preserve the destination for diagnostics but never its login component.
    safe_netloc = parts.netloc
    if parts.username is not None or parts.password is not None:
        safe_netloc = "[REDACTED]@" + parts.netloc.rpartition("@")[2]
    if not parts.query:
        return urlunsplit((parts.scheme, safe_netloc, parts.path, parts.query, parts.fragment))
    query = [(key, "[REDACTED]" if _QUERY_SECRET.search(key) else item)
             for key, item in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, safe_netloc, parts.path, urlencode(query), parts.fragment))


def redact_recursive(value: Any) -> Any:
    """Remove secrets from every value which crosses the evidence boundary."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_sensitive_key(key)
            else redact_recursive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_recursive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_recursive(item) for item in value]
    if isinstance(value, str):
        if value.lower().startswith(("http://", "https://")) or "?" in value:
            return redact_url(value)
        return _INLINE_SECRET.sub(r"[REDACTED]", value)
    return value


def _is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[-_]", "", str(key).lower())
    return normalized in _SENSITIVE_NORMALIZED_KEYS or any(
        token in normalized for token in ("token", "secret", "password", "credential", "signature")
    )


class CompatibilityRequirements(BaseModel):
    """Versioned semantic groups required to interpret a replay correctly."""
    model_config = ConfigDict(extra="forbid")
    scenario: str = "1"
    fault_model: str = "1"
    invariant_model: str = "1"
    trace_model: str = "1"
    browser_webmcp_adapter: str = "1"

    @classmethod
    def installed(cls) -> "CompatibilityRequirements":
        return cls(**SUPPORTED_COMPATIBILITY_GROUPS)


class Compatibility(BaseModel):
    bundle_version: str = BUNDLE_VERSION
    runner: str = COMPATIBILITY_VERSION
    engine_version: str = ENGINE_VERSION
    webmcp_draft: str = "draft"
    browser: str | None = None
    browser_version: str | None = None
    headless: bool | None = None
    webmcp_profile: str = "auto"
    webmcp_argument_mode: str | None = None
    capability_fingerprint: str | None = None
    tool_inventory_fingerprint: str | None = None
    groups: CompatibilityRequirements = Field(default_factory=CompatibilityRequirements.installed)


class StateObservation(BaseModel):
    """Evidence describing the declared source of observable application state."""

    mode: Literal["state_script", "scenario_state_tool", "none"] = "none"
    configured_source: str | None = None
    validation: dict[str, Any] = Field(default_factory=lambda: {
        "status": "not_configured",
        "valid": None,
        "message": "No state observation source is configured.",
    })
    # These nested records preserve that a state script and a scenario state
    # tool have different jobs even when both are configured.
    state_script: dict[str, Any] = Field(default_factory=dict)
    scenario_state_tool: dict[str, Any] = Field(default_factory=dict)


class Artifact(BaseModel):
    kind: Literal["trace", "screenshot", "network", "failure", "scenario", "report"]
    path: str
    redacted: bool = False
    sensitivity: Literal["redacted", "potentially_sensitive"] = "potentially_sensitive"
    description: str | None = None
    run_id: str | None = None


class ApprovalRecord(BaseModel):
    authority: Literal["read_only", "mutation_authorized"] = "read_only"
    source: str = "command"
    reason: str | None = None


class RunBundle(BaseModel):
    """A self-contained run record which can be safely persisted as JSON."""
    schema_version: str = BUNDLE_VERSION
    contract_version: str = BUNDLE_VERSION
    engine_version: str = ENGINE_VERSION
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    command: str
    compatibility: Compatibility = Field(default_factory=Compatibility)
    requirements: CompatibilityRequirements = Field(default_factory=CompatibilityRequirements.installed)
    preflight: dict[str, Any] = Field(default_factory=dict)
    state_observation: StateObservation = Field(default_factory=StateObservation)
    scenario: dict[str, Any] | None = None
    browser_environment: dict[str, Any] = Field(default_factory=dict)
    tool_inventory: list[dict[str, Any]] = Field(default_factory=list)
    inventory_contract: dict[str, Any] = Field(default_factory=dict)
    tool_contract_drift: dict[str, Any] = Field(default_factory=lambda: {
        "version": "1.0", "status": "not_compared", "changed": False, "policy_impact": "none"
    })
    tool_contract_replay_decision: dict[str, Any] = Field(default_factory=dict)
    tool_contract_expectations: dict[str, Any] = Field(default_factory=lambda: {
        "version": "1.0", "status": "not_declared", "passed": True, "checks": [], "failures": []
    })
    actions: list[dict[str, Any]] = Field(default_factory=list)
    faults: list[dict[str, Any]] = Field(default_factory=list)
    state_changes: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    trace: TraceRun | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    replay_command: list[str] = Field(default_factory=list)
    execution: dict[str, Any] = Field(default_factory=lambda: {"adversarial": False, "schedule": None})
    agent_policy: dict[str, Any] = Field(default_factory=dict)
    redaction: dict[str, Any] = Field(default_factory=lambda: {"enabled": True, "fields": ["authorization", "cookie", "token", "password", "secret"]})

    def model_post_init(self, __context: Any) -> None:
        validate_identifier(self.run_id, label="run id")

    @classmethod
    def for_scenario(cls, command: str, scenario: Scenario, *, run_id: str | None = None) -> "RunBundle":
        return cls(command=command, run_id=run_id or str(uuid4()), scenario=scenario.model_dump(mode="json"))

    def write(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.persisted_dict(), indent=2, default=str))
        return destination

    def persisted_dict(self) -> dict[str, Any]:
        """The only machine-readable representation allowed to leave memory."""
        # Tool schemas need a context-aware redactor: names such as
        # ``access_token`` are structural, while their defaults/examples may
        # still be sensitive. The local import avoids a module import cycle.
        from ..tool_contracts import redact_inventory_contract, redact_tool_inventory

        payload = self.model_dump(mode="json")
        inventory = payload.pop("tool_inventory", [])
        inventory_contract = payload.pop("inventory_contract", {})
        preflight_inventory_contract = (
            payload.get("preflight", {}).get("inventory_contract", {})
            if isinstance(payload.get("preflight"), dict)
            else {}
        )
        safe = redact_recursive(payload)
        safe["tool_inventory"] = redact_tool_inventory(inventory)
        safe["inventory_contract"] = redact_inventory_contract(inventory_contract)
        if preflight_inventory_contract and isinstance(safe.get("preflight"), dict):
            safe["preflight"]["inventory_contract"] = redact_inventory_contract(
                preflight_inventory_contract
            )
        return safe
