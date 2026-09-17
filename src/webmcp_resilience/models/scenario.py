from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


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
    # Optional selectors for duplicate names returned from different origins
    # or frames.  They are evidence-qualified, not browser CSS selectors.
    tool_origin: str | None = None
    tool_frame: str | None = None
    timeout_ms: int | None = Field(default=None, gt=0)
    args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_one_operation(self) -> "TimedAction":
        if sum(value is not None for value in (self.invoke, self.retry, self.cancel, self.action)) != 1:
            raise ValueError("each action needs exactly one of invoke, retry, cancel, or action")
        if self.timeout_ms is not None and not (self.invoke or self.retry):
            raise ValueError("timeout_ms is only supported for invoke or retry actions")
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


class ToolContractExpectation(BaseModel):
    """Optional structural and observable expectations for one named tool."""

    model_config = ConfigDict(extra="forbid")
    fingerprint: str | None = None
    required_inputs: list[str] = Field(default_factory=list)
    read_only: bool | None = None
    semantic_version: str | None = None
    result_invariants: list[str] = Field(default_factory=list)
    expected_result_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_declarations(self) -> "ToolContractExpectation":
        if len(self.required_inputs) != len(set(self.required_inputs)):
            raise ValueError("required_inputs must not contain duplicates")
        if len(self.expected_result_codes) != len(set(self.expected_result_codes)):
            raise ValueError("expected_result_codes must not contain duplicates")
        return self


class Scenario(BaseModel):
    name: str = Field(validation_alias=AliasChoices("name", "scenario"))
    url: str = "/"
    actors: dict[str, list[TimedAction]]
    faults: list[Fault] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    final_invariants: list[str] = Field(default_factory=list)
    state: StateProvider | None = None
    result_invariants: list[str] = Field(default_factory=list)
    tool_contracts: dict[str, ToolContractExpectation] = Field(default_factory=dict)
    compatibility: dict[str, Any] = Field(default_factory=lambda: {"schema": "webmcp-resilience/scenario-1", "requires": {"scenario": "1", "fault_model": "1", "invariant_model": "1", "trace_model": "1", "browser_webmcp_adapter": "1"}})
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Navigation-capable declarative tools may intentionally replace the page.
    # Opting in lets the runner wait for and bind the destination document.
    allow_navigation: bool = False
    # Same-actor order is preserved by exploration by default.  Opting into
    # permutations is an explicit scenario declaration because it changes the
    # meaning of a user-authored schedule.
    allow_reordering: bool = False
