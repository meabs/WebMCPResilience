"""Bounded, deterministic interleaving exploration for declared actions."""
from dataclasses import dataclass
from collections.abc import Iterator
import random
import time

from ..models.scenario import Scenario, TimedAction

ScheduledAction = tuple[int, str, TimedAction]


@dataclass(frozen=True)
class ExplorationBudget:
    """Hard limits for schedule generation.

    ``max_schedules`` is the portable bound.  ``time_budget_ms`` is an
    additional guard for large scenarios whose individual candidates are
    cheap but whose permutation space is enormous.
    """

    max_schedules: int = 12
    time_budget_ms: int | None = None


class ScheduleExploration(list[list[ScheduledAction]]):
    """A list-compatible result carrying truthful generation evidence."""

    def __init__(self, schedules: list[list[ScheduledAction]], *, examined: int,
                 exhausted: bool, budget: ExplorationBudget) -> None:
        super().__init__(schedules)
        self.examined = examined
        self.exhausted = exhausted
        self.budget = budget


def explore_schedules(
    scenario: Scenario,
    *,
    limit: int = 12,
    seed: int = 0,
    explore_yields: bool = True,
    time_budget_ms: int | None = None,
) -> ScheduleExploration:
    """Return the declared schedule plus permutations of only simultaneous actions.

    This explores real declared interactions, never generated application data.
    """
    if limit < 1:
        raise ValueError("schedule limit must be at least 1")
    if time_budget_ms is not None and time_budget_ms < 1:
        raise ValueError("schedule time budget must be positive")
    budget = ExplorationBudget(limit, time_budget_ms)
    started = time.monotonic()
    grouped: dict[int, list[ScheduledAction]] = {}
    for actor, actions in scenario.actors.items():
        for action in actions:
            grouped.setdefault(action.offset_ms, []).append((action.offset_ms, actor, action))
    declared_schedule = [item for offset in sorted(grouped) for item in grouped[offset]]
    variants: list[list[ScheduledAction]] = [[]]
    examined = 0
    stopped_by_budget = False

    def within_budget() -> bool:
        return time_budget_ms is None or (time.monotonic() - started) * 1000 < time_budget_ms

    for offset in sorted(grouped):
        group = grouped[offset]
        # Never materialize permutations: a limit of twelve must not spend
        # time constructing all 20! candidates for a large simultaneous group.
        # Keep each actor's declared order stable unless the scenario explicitly
        # opts into reordering it.
        orders = _bounded_orders(group, allow_reordering=bool(getattr(scenario, "allow_reordering", False)))
        next_variants: list[list[ScheduledAction]] = []
        for order in orders:
            examined += 1
            if not within_budget():
                stopped_by_budget = True
                break
            next_variants.extend(prior + list(order) for prior in variants)
            if len(next_variants) >= limit:
                break
        if next_variants:
            variants = next_variants[:limit]
        if stopped_by_budget:
            # A time budget may expire before a complete interleaving can be
            # emitted. Keep one complete declared schedule runnable; a partial
            # schedule would produce a misleading pass.
            variants = [declared_schedule]
            break
    # Bounded seeded exploration also varies declared timing by one scheduler turn.
    # It never invents arguments or actions, making every schedule replayable.
    if explore_yields and len(variants) < limit and not stopped_by_budget:
        rng = random.Random(seed)
        declared = [item for group in grouped.values() for item in group]
        while len(variants) < limit and declared and within_budget():
            candidate = []
            for offset, actor, action in declared:
                adjusted = offset + rng.choice((0, 0, 1))
                candidate.append((adjusted, actor, action))
            candidate.sort(key=lambda item: item[0])
            if candidate not in variants:
                variants.append(candidate)
            else:
                break
    # Reaching the configured candidate cap is itself a budget exhaustion
    # signal.  A caller must not mistake a truncated search for a complete
    # exploration merely because it received ``limit`` results.
    exhausted = stopped_by_budget or len(variants) >= limit
    return ScheduleExploration(variants, examined=examined, exhausted=exhausted, budget=budget)


def _bounded_orders(group: list[ScheduledAction], *, allow_reordering: bool) -> Iterator[tuple[ScheduledAction, ...]]:
    """Yield interleavings lazily while preserving each actor's own order."""
    if allow_reordering:
        yield from _permutations(group)
        return
    actors: list[str] = []
    by_actor: dict[str, list[ScheduledAction]] = {}
    for item in group:
        if item[1] not in by_actor:
            actors.append(item[1])
            by_actor[item[1]] = []
        by_actor[item[1]].append(item)

    def interleave(prefix: tuple[ScheduledAction, ...], positions: dict[str, int]) -> Iterator[tuple[ScheduledAction, ...]]:
        if len(prefix) == len(group):
            yield prefix
            return
        for actor in actors:
            index = positions[actor]
            if index >= len(by_actor[actor]):
                continue
            next_positions = positions.copy()
            next_positions[actor] += 1
            yield from interleave(prefix + (by_actor[actor][index],), next_positions)

    yield from interleave((), {actor: 0 for actor in actors})


def _permutations(values: list[ScheduledAction]) -> Iterator[tuple[ScheduledAction, ...]]:
    """Small local lazy permutation generator (avoids list(permutations(...)))."""
    if not values:
        yield ()
        return
    for index, value in enumerate(values):
        remaining = values[:index] + values[index + 1:]
        for tail in _permutations(remaining):
            yield (value, *tail)


def schedules(
    scenario: Scenario,
    limit: int = 12,
    seed: int = 0,
    explore_yields: bool = True,
    *,
    time_budget_ms: int | None = None,
) -> ScheduleExploration:
    """Compatibility wrapper returning a list-compatible bounded result."""
    return explore_schedules(
        scenario, limit=limit, seed=seed, explore_yields=explore_yields,
        time_budget_ms=time_budget_ms,
    )
