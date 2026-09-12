"""Delta-debugging reduction over user-declared actions."""
from collections.abc import Awaitable, Callable

from ..models.scenario import Scenario


async def reduce_failure(scenario: Scenario, reproduces: Callable[[Scenario], Awaitable[bool]]) -> Scenario:
    """Remove actions one at a time while the caller confirms the failure remains.

    The callback owns page reset and execution, keeping this reducer browser and
    application neutral.
    """
    current = scenario.model_copy(deep=True)
    changed = True
    while changed:
        changed = False
        for actor, actions in list(current.actors.items()):
            for index in range(len(actions)):
                candidate = current.model_copy(deep=True)
                candidate.actors[actor].pop(index)
                if await reproduces(candidate):
                    current = candidate
                    changed = True
                    break
            if changed:
                break
    return current
