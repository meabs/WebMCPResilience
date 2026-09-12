from pathlib import Path

from pydantic import BaseModel, Field

from .trace import TraceRun


class ScenarioResult(BaseModel):
    scenario: str
    passed: bool
    duration_ms: int
    error: str | None = None
    trace: TraceRun
    trace_path: Path | None = None
    failure_path: Path | None = None


class RunResult(BaseModel):
    schema_version: str = "0.1"
    scenarios: list[ScenarioResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.scenarios)
