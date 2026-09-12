from .base import FaultEffect


def timeout_for(effect: FaultEffect, tool: str) -> int | None:
    if effect.type == "timeout" and effect.applies_to(tool):
        return effect.duration_ms
    return None
