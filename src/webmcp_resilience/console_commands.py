"""Console-facing command seam over the typed :class:`CommandAPI`.

The terminal UI owns presentation only.  This module owns the small amount of
console orchestration needed to call the normal command contract, persist a
fresh bundle, and classify replay results from the bundle data rather than a
process exit code.
"""
from __future__ import annotations

import json
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .commands import CommandAPI, failure_handoff
from .models.bundle import RunBundle, redact_recursive


ReplayStatus = Literal[
    "replay_execution_failure",
    "passed_expected_failure",
    "reproduced_expected_failure",
    "passed",
    "unexpected_failure",
]


@dataclass(frozen=True)
class ReplayOutcome:
    status: ReplayStatus
    bundle: RunBundle | None
    fresh_path: Path | None
    invariant: str | None = None
    observed_state: dict[str, Any] | None = None
    replay_command: tuple[str, ...] = ()
    error: str | None = None

    @property
    def is_success(self) -> bool:
        """Whether the replay operation achieved its semantic outcome."""
        return self.status in {"passed", "reproduced_expected_failure"}

    @property
    def message(self) -> str:
        return {
            "replay_execution_failure": "Replay execution/contract failure",
            "passed_expected_failure": "Replay passed when failure was expected",
            "reproduced_expected_failure": "Failure reproduced",
            "passed": "Replay passed",
            "unexpected_failure": "Replay produced an unexpected failure",
        }[self.status]


def _failure_details(bundle: RunBundle) -> tuple[str | None, dict[str, Any]]:
    handoff = bundle.result.get("failure_handoff")
    if not isinstance(handoff, dict):
        handoff = failure_handoff(bundle)
    observed = handoff.get("observed_state", {})
    return handoff.get("failed_invariant"), observed if isinstance(observed, dict) else {}


def _failure_identity(bundle: RunBundle) -> tuple[str | None, dict[str, Any]]:
    """Return the invariant and the values it actually compared."""
    expression, observed = _failure_details(bundle)
    if not expression or not observed:
        return expression, {}
    try:
        tree = ast.parse(expression, mode="eval").body
        if not isinstance(tree, ast.Compare) or len(tree.comparators) != 1:
            return expression, {}

        def resolve(node: ast.AST) -> Any:
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                return observed[node.id]
            if isinstance(node, ast.Attribute):
                parent = resolve(node.value)
                return parent[node.attr] if isinstance(parent, dict) else None
            return None

        return expression, {"left": resolve(tree.left), "right": resolve(tree.comparators[0])}
    except (KeyError, TypeError, ValueError, SyntaxError):
        return expression, {}


def _json_result(stdout: str) -> dict[str, Any] | None:
    """Parse the last JSON object emitted by the CLI contract."""
    for line in reversed(stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def replay_outcome(
    source: RunBundle,
    fresh: RunBundle | None,
    *,
    fresh_path: Path | None = None,
    process_returncode: int = 0,
    error: str | None = None,
) -> ReplayOutcome:
    """Classify replay from the structured bundle/result contract.

    A non-zero process status is not decisive: the CLI intentionally exits 1
    when a replayed invariant fails.  A valid fresh bundle therefore wins over
    the process status when deciding whether the expected failure reproduced.
    """
    if fresh is None:
        return ReplayOutcome(
            "replay_execution_failure", None, fresh_path,
            replay_command=tuple(source.replay_command), error=error,
        )
    expected_failure = source.result.get("passed") is False
    reproduced_failure = fresh.result.get("passed") is False
    source_expression, source_values = _failure_identity(source)
    fresh_expression, fresh_values = _failure_identity(fresh)
    same_failure = (
        source_expression is not None
        and source_expression == fresh_expression
        and (not source_values or source_values == fresh_values)
    )
    if expected_failure and reproduced_failure and same_failure:
        status: ReplayStatus = "reproduced_expected_failure"
    elif expected_failure and not reproduced_failure:
        status = "passed_expected_failure"
    elif expected_failure and reproduced_failure:
        status = "unexpected_failure"
    elif reproduced_failure:
        status = "unexpected_failure"
    else:
        status = "passed"
    invariant, observed = _failure_details(fresh) if reproduced_failure else (None, {})
    return ReplayOutcome(
        status, fresh, fresh_path, invariant, redact_recursive(observed),
        tuple(fresh.replay_command or source.replay_command), error=error,
    )


class ConsoleCommandModule:
    """The only execution dependency used by the terminal console.

    ``replay_allow_mutations`` is supplied by the CLI entrypoint.  The TUI
    never derives, escalates, or overrides it.
    """

    def __init__(self, api: CommandAPI, *, replay_allow_mutations: bool = False) -> None:
        self.api = api
        self.replay_allow_mutations = replay_allow_mutations

    def validate_scenario(self, scenario: dict[str, Any] | Any, *, discovered_tools: list[dict[str, Any]] | None = None) -> RunBundle:
        return self.api.validate(scenario=scenario, tool_inventory=discovered_tools)

    def validate(self, scenario_path: Path, **kwargs: Any) -> RunBundle:
        return self.api.validate(scenario_path, **kwargs)

    async def run(self, scenario_path: Path, **kwargs: Any) -> RunBundle:
        # Console execution remains read-only; the CLI/AgentPolicy are the
        # only authority boundaries that may opt into mutations.
        if kwargs.pop("allow_mutations", False):
            raise ValueError("console command module cannot authorize mutations")
        return await self.api.run(scenario_path, allow_mutations=False, **kwargs)

    def save(self, bundle: RunBundle, output: Path | None = None) -> Path:
        return self.api.save(bundle, output)

    async def replay(self, bundle_path: Path, *, headless: bool = True) -> ReplayOutcome:
        source_payload = redact_recursive(json.loads(bundle_path.read_text()))
        source = RunBundle.model_validate(source_payload)
        command = [sys.executable, "-m", "webmcp_resilience.cli", "replay", str(bundle_path)]
        command.append("--ci" if headless else "--headed")
        command.append("--json")
        if self.replay_allow_mutations:
            command.append("--allow-mutations")
        try:
            completed = subprocess.run(command, cwd=Path.cwd(), text=True, capture_output=True, check=False)
        except OSError as error:
            return replay_outcome(source, None, process_returncode=-1, error=str(redact_recursive(str(error))))
        payload = _json_result(completed.stdout)
        if payload is None or not payload.get("output"):
            details = (completed.stdout + completed.stderr).strip() or "CLI emitted no JSON replay result"
            return replay_outcome(source, None, process_returncode=completed.returncode,
                                  error=str(redact_recursive(details)))
        try:
            fresh_path = Path(str(payload["output"])).resolve()
            persisted = RunBundle.model_validate(redact_recursive(json.loads(fresh_path.read_text())))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            return replay_outcome(source, None, fresh_path=Path(str(payload["output"])),
                                  process_returncode=completed.returncode,
                                  error=str(redact_recursive(str(error))))
        return replay_outcome(source, persisted, fresh_path=fresh_path,
                              process_returncode=completed.returncode)

    def export_handoff(self, bundle_path: Path, output: Path | None = None) -> dict[str, Path]:
        return self.api.export_handoff(bundle_path, output)


# Compatibility name for callers that imported the old client.  It now points
# at the real CommandAPI-backed module rather than a second execution path.
ConsoleCommandClient = ConsoleCommandModule
