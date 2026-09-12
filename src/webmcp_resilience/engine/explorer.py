"""Bounded, deterministic interleaving exploration for declared actions."""
from itertools import permutations
import random

from ..models.scenario import Scenario, TimedAction

ScheduledAction = tuple[int, str, TimedAction]


def schedules(scenario: Scenario, limit: int = 12, seed: int = 0, explore_yields: bool = True) -> list[list[ScheduledAction]]:
    """Return the declared schedule plus permutations of only simultaneous actions.

    This explores real declared interactions, never generated application data.
    """
    grouped: dict[int, list[ScheduledAction]] = {}
    for actor, actions in scenario.actors.items():
        for action in actions:
            grouped.setdefault(action.offset_ms, []).append((action.offset_ms, actor, action))
    variants: list[list[ScheduledAction]] = [[]]
    for offset in sorted(grouped):
        group = grouped[offset]
        orders = list(permutations(group)) if len(group) > 1 else [tuple(group)]
        variants = [prior + list(order) for prior in variants for order in orders][:limit]
    # Bounded seeded exploration also varies declared timing by one scheduler turn.
    # It never invents arguments or actions, making every schedule replayable.
    if explore_yields and len(variants) < limit:
        rng = random.Random(seed)
        declared = [item for group in grouped.values() for item in group]
        while len(variants) < limit and declared:
            candidate = []
            for offset, actor, action in declared:
                adjusted = offset + rng.choice((0, 0, 1))
                candidate.append((adjusted, actor, action))
            candidate.sort(key=lambda item: item[0])
            if candidate not in variants:
                variants.append(candidate)
            else:
                break
    return variants
