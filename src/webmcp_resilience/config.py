from pathlib import Path

import yaml
from typing import Literal

from pydantic import BaseModel, Field


class Config(BaseModel):
    base_url: str = "http://localhost:3000"
    browser: str = "chromium"
    browser_channel: str | None = None
    state_script: str | None = None
    browser_args: list[str] = Field(default_factory=list)
    from_origins: list[str] = Field(default_factory=list)
    # ``auto`` selects object arguments for native WebMCP and JSON strings for
    # the bundled compatibility host, based on its preflight identity. The explicit profiles are useful for
    # pinned contract fixtures and never retry a mutating invocation.
    webmcp_profile: Literal["auto", "native-object", "legacy-string"] = "auto"
    # These hooks are explicit application-state boundaries.  They are only
    # evaluated when configured and are included in bundle evidence.
    setup_script: str | None = None
    reset_script: str | None = None
    initial_state: dict[str, object] | None = None
    exploration_limit: int = Field(12, ge=1)
    exploration_budget_ms: int | None = Field(5_000, gt=0)
    reduction_max_attempts: int = Field(100, ge=1)
    reduction_budget_ms: int | None = Field(30_000, gt=0)
    # Descriptions are excluded from compatibility fingerprints by default.
    # Projects may explicitly opt in when prose is part of their contract.
    tool_contract_include_descriptions: bool = False


def load_config(project: Path = Path(".webmcp")) -> Config:
    path = project / "config.yaml"
    return Config.model_validate(yaml.safe_load(path.read_text()) or {})
