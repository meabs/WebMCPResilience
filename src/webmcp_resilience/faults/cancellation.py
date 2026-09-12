from .base import FaultEffect


def cancels(effect: FaultEffect, tool: str) -> bool:
    return effect.type == "cancellation" and effect.applies_to(tool)
