"""Compatibility-host fixture for a documented declarative form tool race."""
from __future__ import annotations

import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml

from webmcp_resilience.commands import CommandAPI
from webmcp_resilience.config import Config


pytestmark = pytest.mark.e2e


class _QuietDirectoryHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return


def test_declarative_form_race_is_discovered_reduced_and_replayable(
    tmp_path: Path,
) -> None:
    fixture_directory = Path(__file__).parents[2] / "examples" / "declarative-form-race"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(_QuietDirectoryHandler, directory=str(fixture_directory)),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        api = CommandAPI(
            Config(
                base_url=f"http://127.0.0.1:{server.server_port}",
                state_script="window.__declarativeRace.getState()",
            ),
            output_dir=tmp_path / "runs",
        )
        preflight = asyncio.run(api.preflight(run_id="declarative-preflight"))
        tool = next(
            item
            for item in preflight.preflight["tool_inventory"]
            if item["name"] == "submit_note"
        )
        assert tool["discovery_mode"] == "declarative"

        vulnerable = asyncio.run(
            api.run(
                scenario=yaml.safe_load((fixture_directory / "vulnerable.yaml").read_text()),
                run_id="declarative-vulnerable",
                allow_mutations=True,
                adversarial=True,
                seed=7,
            )
        )
        assert vulnerable.result["passed"] is False
        assert vulnerable.result["error_code"] == "invariant_violation"
        assert any(item.kind == "scenario" for item in vulnerable.artifacts)
        replayed = asyncio.run(
            api.replay(
                api.save(vulnerable),
                run_id="declarative-vulnerable-replay",
                allow_mutations=True,
            )
        )
        assert replayed.result["passed"] is False
        assert replayed.result["error_code"] == "invariant_violation"

        safe = asyncio.run(
            api.run(
                scenario=yaml.safe_load((fixture_directory / "safe.yaml").read_text()),
                run_id="declarative-safe",
                allow_mutations=True,
                adversarial=True,
                seed=7,
            )
        )
        assert safe.result["passed"] is True
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
