"""Delta-debugging reduction over user-declared actions.

Reduction is deliberately browser/application neutral.  The callback owns a
fresh run and returns the observed failure identity, so removing an action can
never be accepted merely because some unrelated exception happened.
"""
from dataclasses import dataclass, field
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..models.scenario import Scenario


@dataclass(frozen=True)
class FailureSignature:
    """Stable identity for the defect a reducer is allowed to preserve."""

    kind: str
    invariant: str | None = None
    tool: str | None = None
    error_type: str | None = None
    message: str | None = None

    def matches(self, other: "FailureSignature | None") -> bool:
        return bool(other) and self == other

    def as_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind, "invariant": self.invariant, "tool": self.tool,
            "error_type": self.error_type, "message": self.message,
        }


@dataclass(frozen=True)
class ReductionBudget:
    max_attempts: int = 100
    time_budget_ms: int | None = 30_000


@dataclass
class ReductionReport:
    scenario: Scenario
    signature: FailureSignature | None
    attempts: int = 0
    exhausted: bool = False
    rejected_signatures: list[FailureSignature] = field(default_factory=list)


ReductionObservation = FailureSignature | bool | None
Reproduces = Callable[[Scenario], Awaitable[ReductionObservation]]


def signature_from_failure(error: BaseException, *, trace: Any | None = None) -> FailureSignature | None:
    """Build a deterministic signature and exclude harness/policy failures."""
    error_type = type(error).__name__
    if error_type in {"PermissionError", "CommandError", "WebMCPUnavailable", "TimeoutError"}:
        return None
    for event in reversed(getattr(trace, "events", ())):
        if event.type == "invariant.fail":
            return FailureSignature("invariant", invariant=event.data.get("expression"))
        if event.type == "result_invariant.fail":
            return FailureSignature("result_invariant", invariant=event.data.get("expression"))
        if event.type == "tool_contract.assertion.fail":
            return FailureSignature("tool_contract", invariant=event.data.get("assertion"), tool=event.name)
        if event.type == "tool.error":
            return FailureSignature("tool_error", tool=event.name, error_type=error_type,
                                    message=_stable_message(event.data.get("error")))
    return FailureSignature("exception", error_type=error_type, message=_stable_message(str(error)))


def _stable_message(message: Any) -> str | None:
    if message is None:
        return None
    text = str(message)
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "<id>", text, flags=re.I)
    text = re.sub(r"\b\d+(?:\.\d+)?ms\b", "<duration>", text)
    return text[:500]


async def reduce_failure_report(
    scenario: Scenario,
    reproduces: Reproduces,
    *,
    target_signature: FailureSignature | None = None,
    budget: ReductionBudget | None = None,
) -> ReductionReport:
    """Reduce actions while requiring the same application failure signature."""
    budget = budget or ReductionBudget()
    if budget.max_attempts < 1:
        raise ValueError("reduction max_attempts must be at least 1")
    if budget.time_budget_ms is not None and budget.time_budget_ms < 1:
        raise ValueError("reduction time budget must be positive")
    current = scenario.model_copy(deep=True)
    report = ReductionReport(current, target_signature)
    started = time.monotonic()

    def exhausted() -> bool:
        return report.attempts >= budget.max_attempts or (
            budget.time_budget_ms is not None
            and (time.monotonic() - started) * 1000 >= budget.time_budget_ms
        )

    changed = True
    while changed and not exhausted():
        changed = False
        for actor, actions in list(current.actors.items()):
            for index in range(len(actions)):
                if exhausted():
                    report.exhausted = True
                    break
                candidate = current.model_copy(deep=True)
                candidate.actors[actor].pop(index)
                report.attempts += 1
                observation = await reproduces(candidate)
                if isinstance(observation, FailureSignature):
                    if target_signature is None:
                        target_signature = observation
                        report.signature = observation
                    if target_signature.matches(observation):
                        current = candidate
                        report.scenario = current
                        changed = True
                        break
                    report.rejected_signatures.append(observation)
                elif observation and target_signature is None:
                    # Backward-compatible bool callbacks remain useful for
                    # callers that do not have structured failure evidence.
                    current = candidate
                    report.scenario = current
                    changed = True
                    break
            if report.exhausted or changed:
                break
    report.scenario = current
    report.signature = target_signature
    report.exhausted = report.exhausted or exhausted()
    return report


async def reduce_failure(
    scenario: Scenario,
    reproduces: Reproduces,
    *,
    target_signature: FailureSignature | None = None,
    budget: ReductionBudget | None = None,
) -> Scenario:
    """Compatibility wrapper returning only the reduced scenario."""
    return (await reduce_failure_report(
        scenario, reproduces, target_signature=target_signature, budget=budget
    )).scenario
