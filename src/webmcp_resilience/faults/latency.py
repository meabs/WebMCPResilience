from .base import FaultEffect


def delay_for(effect: FaultEffect, tool: str) -> int:
    return effect.duration_ms if effect.type == "latency" and effect.applies_to(tool) else 0
