import time
from typing import Any

from ..models.trace import TraceEvent, TraceRun


class Recorder:
    def __init__(self, scenario: str, requirements: dict[str, str] | None = None) -> None:
        self.started = time.monotonic()
        self.run = TraceRun(scenario=scenario)
        if requirements:
            self.run.compatibility["requires"] = dict(requirements)

    def add(self, actor: str, event_type: str, *, name: str | None = None, invocation_id: str | None = None,
            data: dict[str, Any] | None = None, state: dict[str, Any] | None = None) -> None:
        self.run.events.append(TraceEvent(timestamp_ms=round((time.monotonic() - self.started) * 1000), actor=actor,
            type=event_type, name=name, invocation_id=invocation_id, data=data or {}, state_snapshot=state or {}))
