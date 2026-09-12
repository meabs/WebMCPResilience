from pathlib import Path

import yaml

from ..models.scenario import Scenario


def load_reproduction(path: Path) -> Scenario:
    """Load a versioned failure record without exposing trace internals to callers."""
    saved = yaml.safe_load(path.read_text())
    if not isinstance(saved, dict) or "scenario" not in saved:
        raise ValueError(f"{path} is not a WebMCP Resilience reproduction file")
    return Scenario.model_validate(saved["scenario"])
