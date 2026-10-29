from dataclasses import dataclass

from ..models.scenario import Fault


@dataclass(frozen=True)
class FaultEffect:
    type: str
    tool: str | None = None
    duration_ms: int = 0
    delays_ms: tuple[int, ...] | None = None
    url: str | None = None
    status: int = 503
    at: str = "before_invoke"

    def applies_to(self, tool: str) -> bool:
        return self.tool is None or self.tool == tool


def build_effects(faults: list[Fault]) -> list[FaultEffect]:
    """Translate validated DSL faults into engine-neutral effects."""
    return [FaultEffect(type=fault.type, tool=fault.tool, duration_ms=fault.duration_ms, delays_ms=tuple(fault.delays_ms) if fault.delays_ms is not None else None, url=fault.url, status=fault.status, at=str(fault.at)) for fault in faults]
