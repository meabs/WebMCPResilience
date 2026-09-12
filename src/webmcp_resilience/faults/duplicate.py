from .base import FaultEffect


def duplicates(effect: FaultEffect, tool: str) -> bool:
    return effect.type == "duplicate_invocation" and effect.applies_to(tool)
