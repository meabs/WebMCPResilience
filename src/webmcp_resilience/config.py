from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Config(BaseModel):
    base_url: str = "http://localhost:3000"
    browser: str = "chromium"
    browser_channel: str | None = None
    state_script: str | None = None
    browser_args: list[str] = Field(default_factory=list)
    from_origins: list[str] = Field(default_factory=list)


def load_config(project: Path = Path(".webmcp")) -> Config:
    path = project / "config.yaml"
    return Config.model_validate(yaml.safe_load(path.read_text()) or {})
