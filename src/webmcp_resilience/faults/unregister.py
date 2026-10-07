from .base import FaultEffect


def unregisters_during_invoke(effect: FaultEffect, tool: str) -> bool:
    """Return whether this effect unregisters the selected in-flight tool."""
    return effect.type == "unregister_during_invoke" and effect.applies_to(tool)
