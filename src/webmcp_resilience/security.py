"""Small, shared validation helpers for browser-facing navigation."""
from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


def canonical_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("navigation URL must have an HTTP(S) origin")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("navigation URL has an invalid port") from error
    hostname = parsed.hostname.lower()
    netloc = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if port is not None and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def resolve_navigation_url(value: str, base_url: str) -> str:
    """Resolve a scenario URL using strict URL syntax before browser parsing.

    Backslashes, scheme-relative references, encoded separators and ambiguous
    scheme-like forms are rejected because browsers normalize them differently
    from ordinary URL joining libraries.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("navigation URL must be a non-empty string")
    if value != value.strip() or any(ord(char) < 0x20 for char in value):
        raise ValueError("navigation URL contains untrusted whitespace or control characters")
    decoded = unquote(value)
    if "\\" in value or "\\" in decoded:
        raise ValueError("navigation URL contains an untrusted backslash")
    if value.startswith("//") or decoded.startswith("//"):
        raise ValueError("scheme-relative navigation URL is not allowed")
    if re.search(r"%(?:2f|5c|2e)", value, re.IGNORECASE):
        raise ValueError("navigation URL contains encoded path separators or traversal")
    parsed = urlsplit(value)
    if parsed.scheme:
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("navigation URL has an ambiguous or unsupported scheme")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("navigation URL must not contain credentials")
    elif parsed.netloc:
        raise ValueError("scheme-relative navigation URL is not allowed")
    resolved = urljoin(base_url, value)
    origin = canonical_origin(resolved)
    # Rebuild only after validation; Playwright receives a fully resolved URL.
    final = urlsplit(resolved)
    return urlunsplit((final.scheme.lower(), final.netloc, final.path or "/", final.query, final.fragment))


def validate_navigation_url(value: str, base_url: str, allowed_origins: set[str] | tuple[str, ...]) -> str:
    resolved = resolve_navigation_url(value, base_url)
    if canonical_origin(resolved) not in set(allowed_origins):
        raise ValueError(f"navigation URL resolves outside the allowed target origins: {resolved}")
    return resolved


def validate_scenario_navigation(scenario: object, base_url: str,
                                 allowed_origins: set[str] | tuple[str, ...] | None = None) -> None:
    """Validate every URL that can reach a browser navigation call."""
    origins = set(allowed_origins or (canonical_origin(base_url),))
    urls = [getattr(scenario, "url", None)]
    urls.extend(
        fault.url for fault in getattr(scenario, "faults", [])
        if getattr(fault, "type", None) == "navigation"
    )
    urls.extend(
        action.value
        for actions in getattr(scenario, "actors", {}).values()
        for action in actions
        if getattr(action, "action", None) == "navigate"
    )
    for value in urls:
        if value:
            validate_navigation_url(value, base_url, origins)


def agent_policy_snapshot(
    allowed_origins: set[str] | tuple[str, ...] | list[str],
    *,
    mutation_permission: bool,
    artifact_sensitivity: str = "redacted_text_only",
    concurrency_limit: int = 1,
    authority_source: str = "command",
) -> dict[str, object]:
    """Describe the immutable safety policy shared by all execution adapters."""
    origins = sorted(set(allowed_origins))
    return {
        "allowed_origins": origins,
        # Compatibility aliases keep older bundle consumers readable while
        # the explicit policy vocabulary is adopted by new consumers.
        "allowed_target_origins": origins,
        "mutation_authority": "mutation_authorized" if mutation_permission else "read_only",
        "mutation_permission": mutation_permission,
        "authority_source": authority_source,
        "blocked_ui_actions": [] if mutation_permission else ["click", "fill", "select"],
        "blocked_cancellation": not mutation_permission,
        "blocked_cancellation_actions": [] if mutation_permission else ["cancel"],
        "artifact_policy": {
            "sensitivity": artifact_sensitivity,
            "text_evidence_redacted": True,
            "sensitive_artifacts": "metadata_only",
            "screenshots_retrievable": False,
        },
        "navigation_validation": {
            "mode": "strict_origin_allowlist",
            "pre_execution_resolution": True,
            "final_origin_validation": True,
            "rejects": [
                "cross_origin",
                "scheme_relative",
                "encoded_separators",
                "backslash_normalisation",
                "credentials",
            ],
        },
        "concurrency_limit": concurrency_limit,
        "agent_override": False,
    }
