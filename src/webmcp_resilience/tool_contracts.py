"""Deterministic, redacted WebMCP tool-contract evidence and drift policy.

This module is the single seam used by preflight, execution, replay, diff and
the presentation adapters.  It deliberately compares only declared structure
and observable metadata; it cannot infer whether business meaning is stable.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Iterable, Mapping

from .models.bundle import redact_recursive


TOOL_CONTRACT_VERSION = "1.0"
_DESCRIPTION_KEYS = {"description", "$comment"}
_SECRET_KEY = re.compile(
    r"(?:token|secret|password|credential|authorization|cookie|api[-_]?key|signature|access[-_]?key)",
    re.I,
)
_SCHEMA_NAME_MAPS = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
_SENSITIVE_SCHEMA_NAME_MAPS = {"properties", "$defs", "definitions", "dependentSchemas"}
_SCHEMA_FIELDS = {"inputSchema", "outputSchema", "input_schema", "output_schema"}
_SCHEMA_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SCHEMA_SUBTREES = {
    "additionalProperties",
    "contains",
    "contentSchema",
    "else",
    "if",
    "items",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
}
_JSON_SCHEMA_TYPES = {"array", "boolean", "integer", "null", "number", "object", "string"}
_INVALID_SCHEMA = {"$invalidSchema": "[REDACTED]"}


def _normalize_schema(value: Any) -> Any:
    """Parse string-encoded schemas and discard unparseable raw contents."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return deepcopy(_INVALID_SCHEMA)
    # JSON Schema roots are objects or boolean schemas. Other decoded JSON is
    # not safe or useful contract structure and may itself contain opaque data.
    if not isinstance(value, (dict, bool)) and value is not None:
        return deepcopy(_INVALID_SCHEMA)
    return deepcopy(value)


def _normalize_schema_fields(value: Any) -> Any:
    """Normalize schemas in imported inventory or canonical evidence trees."""
    if isinstance(value, dict):
        return {
            str(key): (
                _normalize_schema(item)
                if str(key) in _SCHEMA_FIELDS
                else _normalize_schema_fields(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_schema_fields(item) for item in value]
    return value


def _safe_schema_type(value: Any) -> Any:
    if isinstance(value, str) and value in _JSON_SCHEMA_TYPES:
        return value
    if (
        isinstance(value, list)
        and value
        and all(isinstance(item, str) and item in _JSON_SCHEMA_TYPES for item in value)
    ):
        return list(value)
    return "[REDACTED]"


def _redact_sensitive_schema(value: Any) -> Any:
    """Keep schema topology beneath a secret property, never its data."""
    if isinstance(value, bool):
        return value
    if not isinstance(value, dict):
        return "[REDACTED]"

    result: dict[str, Any] = {}
    for raw_key in sorted(value, key=lambda item: str(item)):
        key = str(raw_key)
        item = value[raw_key]
        if key in _SENSITIVE_SCHEMA_NAME_MAPS and isinstance(item, dict):
            # These mappings are schema topology. Their keys are property or
            # definition names and remain available for drift detection.
            result[key] = {
                str(name): _redact_sensitive_schema(schema)
                for name, schema in sorted(item.items(), key=lambda pair: str(pair[0]))
            }
        elif key in _SCHEMA_LISTS and isinstance(item, (list, tuple)):
            result[key] = [_redact_sensitive_schema(schema) for schema in item]
        elif key in _SCHEMA_SUBTREES:
            if isinstance(item, bool):
                result[key] = item
            else:
                result[key] = _redact_sensitive_schema(item)
        elif key == "type":
            result[key] = _safe_schema_type(item)
        elif key == "required" and isinstance(item, list) and all(
            isinstance(name, str) for name in item
        ):
            result[key] = list(item)
        elif key == "dependentRequired" and isinstance(item, dict):
            result[key] = {
                str(name): list(required)
                if isinstance(required, list) and all(isinstance(entry, str) for entry in required)
                else "[REDACTED]"
                for name, required in sorted(item.items(), key=lambda pair: str(pair[0]))
            }
        else:
            # Descriptions, titles, patterns, formats, media types, examples,
            # extensions and unknown keywords are all data-bearing. Replacing
            # the complete value also prevents leaks through nested dict keys.
            result[key] = "[REDACTED]"
    return result


def _redact_contract(
    value: Any,
    *,
    names_are_structural: bool = False,
    sensitive_property: bool = False,
) -> Any:
    """Redact values without erasing schema property names such as ``token``."""
    if sensitive_property:
        return _redact_sensitive_schema(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key in sorted(value, key=lambda item: str(item)):
            key = str(raw_key)
            item = value[raw_key]
            if _SECRET_KEY.search(key) and not names_are_structural:
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact_contract(
                    item,
                    names_are_structural=key in _SCHEMA_NAME_MAPS,
                    sensitive_property=(
                        sensitive_property
                        or (names_are_structural and bool(_SECRET_KEY.search(key)))
                    ),
                )
        return result
    if isinstance(value, (list, tuple)):
        return [
            _redact_contract(item, sensitive_property=sensitive_property)
            for item in value
        ]
    return redact_recursive(value)


def canonicalize(value: Any) -> Any:
    """Return JSON-compatible data with recursively deterministic object order."""
    if isinstance(value, dict):
        return {str(key): canonicalize(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    return value


def _without_descriptions(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_descriptions(item)
            for key, item in value.items()
            if key not in _DESCRIPTION_KEYS
        }
    if isinstance(value, list):
        return [_without_descriptions(item) for item in value]
    return value


def _digest(value: Any) -> str:
    encoded = json.dumps(canonicalize(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_tool(tool: Mapping[str, Any], *, include_descriptions: bool = False) -> dict[str, Any]:
    """Project one discovered descriptor onto the compatibility contract."""
    contract: dict[str, Any] = {
        "annotations": deepcopy(tool.get("annotations")),
        "input_schema": _normalize_schema(tool.get("inputSchema")),
        "name": tool.get("name"),
        "output_schema": _normalize_schema(tool.get("outputSchema")),
    }
    if include_descriptions:
        contract["description"] = tool.get("description")
    else:
        contract = _without_descriptions(contract)
    return canonicalize(_redact_contract(contract))


def redact_tool_inventory(inventory: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Persist safe descriptors while retaining schema property structure."""
    fields = ("name", "description", "inputSchema", "outputSchema", "annotations", "semanticVersion")
    result = []
    for tool in inventory:
        projected = {key: deepcopy(tool.get(key)) for key in fields}
        projected["inputSchema"] = _normalize_schema(projected["inputSchema"])
        projected["outputSchema"] = _normalize_schema(projected["outputSchema"])
        result.append(canonicalize(_redact_contract(projected)))
    return sorted(result, key=lambda item: str(item.get("name") or ""))


def redact_inventory_contract(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Re-redact imported or model-constructed canonical contract evidence."""
    normalized = _normalize_schema_fields(deepcopy(dict(evidence)))
    return canonicalize(_redact_contract(normalized))


def build_inventory_contract(
    inventory: Iterable[Mapping[str, Any]], *, include_descriptions: bool = False
) -> dict[str, Any]:
    """Build stable per-tool and complete inventory fingerprints plus summary."""
    tools: list[dict[str, Any]] = []
    for descriptor in inventory:
        contract = canonical_tool(descriptor, include_descriptions=include_descriptions)
        tools.append({
            "name": contract.get("name"),
            "fingerprint": _digest(contract),
            "contract": contract,
        })
    tools.sort(key=lambda item: (str(item.get("name") or ""), item["fingerprint"]))
    inventory_fingerprint = _digest([item["contract"] for item in tools])
    return {
        "version": TOOL_CONTRACT_VERSION,
        "fingerprint_policy": {
            "fields": ["name", "input_schema", "output_schema", "annotations"],
            "include_descriptions": include_descriptions,
            "volatile_runtime_fields": "excluded",
            "redacted": True,
        },
        "inventory_fingerprint": inventory_fingerprint,
        "tools": tools,
        "summary": {
            "tool_count": len(tools),
            "tool_names": [item["name"] for item in tools],
        },
    }


def no_contract_comparison(*, inventory_fingerprint: str | None = None) -> dict[str, Any]:
    return {
        "version": TOOL_CONTRACT_VERSION,
        "status": "not_compared",
        "changed": False,
        "policy_impact": "none",
        "baseline_inventory_fingerprint": None,
        "current_inventory_fingerprint": inventory_fingerprint,
        "added_tools": [],
        "removed_tools": [],
        "changed_input_schemas": [],
        "changed_output_schemas": [],
        "changed_annotations": [],
        "descriptive_only_changes": [],
        "unchanged_tools": [],
        "details": {},
    }


def _types(schema: Any) -> set[str] | None:
    if not isinstance(schema, dict) or "type" not in schema:
        return None
    value = schema["type"]
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return None


def _enums(schema: Any) -> set[str] | None:
    if not isinstance(schema, dict) or not isinstance(schema.get("enum"), list):
        return None
    return {json.dumps(item, sort_keys=True, default=str) for item in schema["enum"]}


def _schema_policy(before: Any, after: Any, *, output: bool) -> tuple[str, list[str]]:
    """Classify common JSON-Schema compatibility changes conservatively."""
    if before == after:
        return "none", []
    reasons: list[str] = []
    unknown_reasons: list[str] = []
    if not isinstance(before, dict) or not isinstance(after, dict):
        return "breaking" if output else "unknown", ["schema representation changed"]

    before_types, after_types = _types(before), _types(after)
    if before_types is None and after_types is not None:
        reasons.append("type narrowed")
    elif before_types and after_types and after_types < before_types:
        reasons.append("type narrowed")
    elif before_types and after_types and before_types != after_types:
        (reasons if output else unknown_reasons).append("output type changed" if output else "type changed")
    before_enum, after_enum = _enums(before), _enums(after)
    if before_enum is None and after_enum is not None:
        reasons.append("enum narrowed")
    elif before_enum and after_enum and after_enum < before_enum:
        reasons.append("enum narrowed")

    before_required = set(before.get("required", [])) if isinstance(before.get("required", []), list) else set()
    after_required = set(after.get("required", [])) if isinstance(after.get("required", []), list) else set()
    newly_required = sorted(after_required - before_required)
    if newly_required:
        label = "output schema" if output else "input schema"
        reasons.append(f"{label}: {', '.join(newly_required)} now required")
    if output and before_required - after_required:
        reasons.append(f"output schema fields no longer required: {', '.join(sorted(before_required - after_required))}")

    before_properties = before.get("properties") if isinstance(before.get("properties"), dict) else {}
    after_properties = after.get("properties") if isinstance(after.get("properties"), dict) else {}
    removed = sorted(set(before_properties) - set(after_properties))
    if removed:
        reasons.append(f"properties removed: {', '.join(removed)}")
    if before.get("additionalProperties") is not False and after.get("additionalProperties") is False:
        reasons.append("additional properties are no longer accepted")

    shared = sorted(set(before_properties) & set(after_properties))
    for name in shared:
        nested_policy, nested_reasons = _schema_policy(before_properties[name], after_properties[name], output=output)
        if nested_policy == "breaking":
            reasons.extend(f"{name}: {reason}" for reason in nested_reasons)
        elif nested_policy == "unknown":
            unknown_reasons.extend(f"{name}: {reason}" for reason in nested_reasons)
    if reasons:
        return "breaking", reasons
    if unknown_reasons:
        return "unknown", unknown_reasons

    added = sorted(set(after_properties) - set(before_properties))
    if added and not any(name in after_required for name in added):
        reduced_before = deepcopy(before)
        reduced_after = deepcopy(after)
        reduced_after["properties"] = {key: value for key, value in after_properties.items() if key not in added}
        if reduced_before == reduced_after:
            return "compatible", [f"optional {'output field' if output else 'input'} added: {', '.join(added)}"]

    # Removing an input requirement widens accepted input; adding an output
    # field widens observable data. Both are compatible when that is all.
    if not output and before_required - after_required:
        reduced_before = deepcopy(before)
        if after_required:
            reduced_before["required"] = sorted(after_required)
        else:
            reduced_before.pop("required", None)
        if reduced_before == after:
            return "compatible", ["required fields became optional"]
    if output and added:
        return "compatible", [f"optional output field added: {', '.join(added)}"]
    return "unknown", ["schema changed and needs human review"]


def _impact(values: Iterable[str]) -> str:
    ranked = {"none": 0, "compatible": 1, "unknown": 2, "breaking": 3}
    return max(values, key=lambda item: ranked[item], default="none")


def _descriptor_map(inventory: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        str(tool.get("name")): tool
        for tool in inventory
        if isinstance(tool.get("name"), str) and tool.get("name")
    }


def compare_tool_inventories(
    baseline: Iterable[Mapping[str, Any]],
    current: Iterable[Mapping[str, Any]],
    *,
    include_descriptions: bool = False,
) -> dict[str, Any]:
    """Compare two inventories and keep contract drift separate from behaviour."""
    left, right = _descriptor_map(baseline), _descriptor_map(current)
    left_contract = build_inventory_contract(
        left.values(), include_descriptions=include_descriptions
    )
    right_contract = build_inventory_contract(
        right.values(), include_descriptions=include_descriptions
    )
    added = sorted(set(right) - set(left))
    removed = sorted(set(left) - set(right))
    changed_input: list[str] = []
    changed_output: list[str] = []
    changed_annotations: list[str] = []
    descriptive: list[str] = []
    unchanged: list[str] = []
    details: dict[str, Any] = {}
    impacts: list[str] = ["compatible" for _ in added] + ["breaking" for _ in removed]

    for name in added:
        details[name] = {"policy_impact": "compatible", "changes": [{"kind": "tool", "summary": "tool added", "policy_impact": "compatible"}]}
    for name in removed:
        details[name] = {"policy_impact": "breaking", "changes": [{"kind": "tool", "summary": "tool removed", "policy_impact": "breaking"}]}

    for name in sorted(set(left) & set(right)):
        before, after = left[name], right[name]
        changes: list[dict[str, Any]] = []
        for key, report_key, output in (
            ("inputSchema", changed_input, False),
            ("outputSchema", changed_output, True),
        ):
            structural_before = _without_descriptions(
                _redact_contract(_normalize_schema(before.get(key)))
            )
            structural_after = _without_descriptions(
                _redact_contract(_normalize_schema(after.get(key)))
            )
            if structural_before != structural_after:
                report_key.append(name)
                policy, reasons = _schema_policy(structural_before, structural_after, output=output)
                changes.append({
                    "kind": "output_schema" if output else "input_schema",
                    "summary": "; ".join(reasons),
                    "policy_impact": policy,
                })
        annotations_before = _without_descriptions(_redact_contract(before.get("annotations")))
        annotations_after = _without_descriptions(_redact_contract(after.get("annotations")))
        if annotations_before != annotations_after:
            changed_annotations.append(name)
            sensitive_hints = {"readOnlyHint", "destructiveHint"}
            before_hints = annotations_before if isinstance(annotations_before, dict) else {}
            after_hints = annotations_after if isinstance(annotations_after, dict) else {}
            changed_hints = sorted(
                key for key in sensitive_hints
                if before_hints.get(key) != after_hints.get(key)
            )
            policy = "breaking" if changed_hints else "unknown"
            summary = f"safety annotations changed: {', '.join(changed_hints)}" if changed_hints else "annotations changed and need human review"
            changes.append({"kind": "annotations", "summary": summary, "policy_impact": policy})

        compatibility_before = canonical_tool(before)
        compatibility_after = canonical_tool(after)
        full_before = canonical_tool(before, include_descriptions=True)
        full_after = canonical_tool(after, include_descriptions=True)
        if full_before.get("description") != full_after.get("description"):
            descriptive.append(name)
            if include_descriptions:
                changes.append({
                    "kind": "description",
                    "summary": "description changed under recorded fingerprint policy",
                    "policy_impact": "unknown",
                })
        if changes:
            tool_impact = _impact(change["policy_impact"] for change in changes)
            impacts.append(tool_impact)
            details[name] = {"policy_impact": tool_impact, "changes": changes}
        elif name not in descriptive:
            unchanged.append(name)

    changed = bool(details)
    policy_impact = _impact(impacts)
    return {
        "version": TOOL_CONTRACT_VERSION,
        "status": "drift" if changed else "unchanged",
        "changed": changed,
        "policy_impact": policy_impact,
        "baseline_inventory_fingerprint": left_contract["inventory_fingerprint"],
        "current_inventory_fingerprint": right_contract["inventory_fingerprint"],
        "added_tools": added,
        "removed_tools": removed,
        "changed_input_schemas": changed_input,
        "changed_output_schemas": changed_output,
        "changed_annotations": changed_annotations,
        "descriptive_only_changes": descriptive,
        "unchanged_tools": unchanged,
        "details": details,
    }


def semantic_version(tool: Mapping[str, Any]) -> str | None:
    annotations = tool.get("annotations")
    candidates = [
        tool.get("semanticVersion"),
        annotations.get("semanticVersion") if isinstance(annotations, dict) else None,
        annotations.get("version") if isinstance(annotations, dict) else None,
    ]
    return next((str(value) for value in candidates if value is not None), None)


def validate_contract_expectations(
    expectations: Mapping[str, Any], inventory: Iterable[Mapping[str, Any]], *,
    include_descriptions: bool = False,
) -> dict[str, Any]:
    """Validate declared structural expectations against live discovery."""
    tools = _descriptor_map(inventory)
    failures: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    evidence = build_inventory_contract(tools.values(), include_descriptions=include_descriptions)
    fingerprints = {item["name"]: item["fingerprint"] for item in evidence["tools"]}
    for name in sorted(expectations):
        expectation = expectations[name]
        expected = expectation.model_dump(mode="json") if hasattr(expectation, "model_dump") else dict(expectation)
        tool = tools.get(name)
        if tool is None:
            failures.append({"tool": name, "check": "discovered", "message": "tool was not discovered"})
            continue
        checks.append({"tool": name, "check": "discovered", "passed": True})
        if expected.get("fingerprint") and expected["fingerprint"] != fingerprints.get(name):
            failures.append({"tool": name, "check": "fingerprint", "message": "pinned fingerprint does not match the live contract"})
        input_schema = _normalize_schema(tool.get("inputSchema"))
        required = set(input_schema.get("required", [])) if isinstance(input_schema, dict) else set()
        missing_required = sorted(set(expected.get("required_inputs") or []) - required)
        if missing_required:
            failures.append({"tool": name, "check": "required_inputs", "message": f"inputs are not required by the live schema: {', '.join(missing_required)}"})
        if expected.get("read_only") is not None:
            live_read_only = (tool.get("annotations") or {}).get("readOnlyHint", False) if isinstance(tool.get("annotations"), dict) else False
            if live_read_only is not expected["read_only"]:
                failures.append({"tool": name, "check": "read_only", "message": f"expected read_only={str(expected['read_only']).lower()}, live annotation is {live_read_only!r}"})
        if expected.get("semantic_version") is not None and semantic_version(tool) != str(expected["semantic_version"]):
            failures.append({"tool": name, "check": "semantic_version", "message": f"expected semantic version {expected['semantic_version']!r}, live declared version is {semantic_version(tool)!r}"})
    return {
        "version": TOOL_CONTRACT_VERSION,
        "status": "failed" if failures else "passed" if expectations else "not_declared",
        "passed": not failures,
        "checks": checks,
        "failures": failures,
    }


def concise_drift_lines(report: Mapping[str, Any]) -> list[str]:
    if not report.get("changed"):
        return []
    lines = ["TOOL CONTRACT DRIFT"]
    for name in sorted(report.get("details", {})):
        summaries = "; ".join(change.get("summary", "changed") for change in report["details"][name].get("changes", []))
        lines.append(f"changed: {name} ({summaries})")
    lines.append(f"policy impact: {report.get('policy_impact', 'unknown')}")
    return lines


def tool_contract_replay_decision(
    report: Mapping[str, Any], *, strict: bool = False
) -> dict[str, Any]:
    """Make the replay gate explicit without changing canonical evidence.

    The caller supplies the report built using the fingerprint policy saved in
    the source bundle.  This intentionally never consults ambient config.
    """
    impact = str(report.get("policy_impact", "unknown"))
    reasons = [
        change.get("summary", "contract changed")
        for detail in (report.get("details", {}) or {}).values()
        if isinstance(detail, Mapping)
        for change in detail.get("changes", [])
        if isinstance(change, Mapping)
    ]
    description_only_fingerprint_drift = bool(report.get("descriptive_only_changes")) and all(
        change.get("kind") == "description"
        for detail in (report.get("details", {}) or {}).values()
        if isinstance(detail, Mapping)
        for change in detail.get("changes", [])
        if isinstance(change, Mapping)
    )
    if report.get("status") == "evidence_integrity_error":
        status, allowed = "rejected_evidence_integrity", False
    elif report.get("status") == "policy_mismatch":
        status, allowed = "rejected_policy_mismatch", False
    elif (
        not report.get("changed")
        and report.get("baseline_inventory_fingerprint") is not None
        and report.get("baseline_inventory_fingerprint") == report.get("current_inventory_fingerprint")
    ):
        status, allowed = "allowed_exact_match", True
    elif strict and description_only_fingerprint_drift:
        status, allowed = "rejected_strict_tool_contracts", False
    elif impact == "compatible" and not strict:
        status, allowed = "allowed_compatible_drift", True
    elif impact == "compatible":
        status, allowed = "rejected_strict_tool_contracts", False
    elif impact == "breaking":
        status, allowed = "rejected_breaking_drift", False
    else:
        status, allowed = "rejected_unknown_drift", False
    return {
        "status": status,
        "allowed": allowed,
        "strict_tool_contracts": strict,
        "baseline_fingerprint": report.get("baseline_inventory_fingerprint"),
        "live_fingerprint": report.get("current_inventory_fingerprint"),
        "policy_impact": "policy_mismatch" if report.get("status") == "policy_mismatch" else impact,
        "reasons": reasons,
    }
