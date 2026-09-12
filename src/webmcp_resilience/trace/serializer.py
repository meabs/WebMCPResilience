from pathlib import Path

from ..models.trace import TraceRun


def write_trace(trace: TraceRun, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(trace.model_dump_json(indent=2))
