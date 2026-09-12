from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    timestamp_ms: int
    actor: str
    type: str
    name: str | None = None
    invocation_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    state_snapshot: dict[str, Any] = Field(default_factory=dict)


class TraceRun(BaseModel):
    schema_version: str = "2.0"
    compatibility: dict[str, Any] = Field(default_factory=lambda: {"schema": "webmcp-resilience/trace-2", "requires": {"scenario": "1", "fault_model": "1", "invariant_model": "1", "trace_model": "1", "browser_webmcp_adapter": "1"}})
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    scenario: str
    events: list[TraceEvent] = Field(default_factory=list)
