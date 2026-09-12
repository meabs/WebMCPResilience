from .base import FaultEffect


def navigation_url(effect: FaultEffect) -> str | None:
    return effect.url


def navigates(effect: FaultEffect) -> bool:
    return effect.type == "navigation"
