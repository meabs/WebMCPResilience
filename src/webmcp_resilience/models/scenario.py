from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator


SUPPORTED_FAULT_TIMINGS: dict[str, set[str]] = {
    "latency": {"before_invoke", "after_invoke"},
    "duplicate_invocation": {"before_invoke", "after_invoke"},
    "cancellation": {"before_invoke"},
    "navigation": {"before_invoke", "after_invoke"},
    "timeout": {"before_invoke"},
    "http_error": {"before_invoke"},
}


class TimedAction(BaseModel):
    at: str | int = "0ms"
    invoke: str | None = None
    retry: str | None = None
    cancel: str | None = None
    action: Literal["click", "fill", "select", "navigate", "wait"] | None = None
    selector: str | None = None
    value: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_one_operation(self) -> "TimedAction":
        if sum(value is not None for value in (self.invoke, self.retry, self.cancel, self.action)) != 1:
            raise ValueError("each action needs exactly one of invoke, retry, cancel, or action")
        return self

    @property
    def offset_ms(self) -> int:
        if isinstance(self.at, int):
            return self.at
        value = self.at.strip().lower()
        if not value.endswith("ms") or not value[:-2].isdigit():
            raise ValueError("action timing must be an integer number of milliseconds")
        return int(value[:-2])


class Fault(BaseModel):
    type: Literal["latency", "timeout", "http_error", "duplicate_invocation", "cancellation", "navigation"]
    tool: str | None = None
    duration_ms: int = 0
    url: str | None = None
    status: int = 503
    at: str | int = "before_invoke"
    yield_point: str | None = None

    @model_validator(mode="before")
    @classmethod
    def shorthand(cls, value: Any) -> Any:
        return {"type": value} if isinstance(value, str) else value

    @model_validator(mode="after")
    def supported_timing(self) -> "Fault":
        if self.at not in SUPPORTED_FAULT_TIMINGS[self.type]:
            raise ValueError(f"fault timing {self.at!r} is unsupported for {self.type}; supported: {sorted(SUPPORTED_FAULT_TIMINGS[self.type])}")
        if self.yield_point is not None:
            raise ValueError("fault yield_point is not supported; use the explicit fault at timing")
        return self


class StateProvider(BaseModel):
    tool: str
    path: str | None = None


class Scenario(BaseModel):
    name: str = Field(validation_alias=AliasChoices("name", "scenario"))
    url: str = "/"
    actors: dict[str, list[TimedAction]]
    faults: list[Fault] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    state: StateProvider | None = None
    result_invariants: list[str] = Field(default_factory=list)
    compatibility: dict[str, Any] = Field(default_factory=lambda: {"schema": "webmcp-resilience/scenario-1", "requires": {"scenario": "1", "fault_model": "1", "invariant_model": "1", "trace_model": "1", "browser_webmcp_adapter": "1"}})
    metadata: dict[str, Any] = Field(default_factory=dict)
