"""Run the documented inspect -> replay loop through local MCP control.

Start ``webmcp demo`` in another terminal before running this file. The
control server is deliberately launched with the same explicit mutation
authority required by the vulnerable fixture.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SCENARIO = ".webmcp/scenarios/vulnerable-human-tool-race.yaml"


def main() -> int:
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "webmcp_resilience.cli",
            "agent-server",
            "--project-root",
            ".",
            "--allow-mutations",
        ],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    counter = 0

    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal counter
        counter += 1
        message = {"jsonrpc": "2.0", "id": counter, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        assert server.stdin and server.stdout
        server.stdin.write(json.dumps(message) + "\n")
        server.stdin.flush()
        line = server.stdout.readline()
        if not line:
            raise RuntimeError("agent-server exited before returning a response")
        response = json.loads(line)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]["structuredContent"]

    try:
        steps = [
            ("inspect", "preflight", {"path": "/", "run_id": "agent-inspect"}),
            ("validate", "validate_scenario", {"scenario_id": SCENARIO, "run_id": "agent-validated"}),
            ("run", "run_scenario", {"scenario_id": SCENARIO, "adversarial": True, "seed": 7, "run_id": "agent-failure"}),
        ]
        for label, tool, arguments in steps:
            result = call(tool, arguments)
            print(json.dumps({"step": label, "run_id": result.get("run_id"), "result": result.get("result", {})}, sort_keys=True))

        evidence = call("get_failure_or_repro", {"run_id": "agent-failure", "artifact": "failure"})
        print(json.dumps({"step": "failure evidence", "artifact": evidence.get("artifact"), "contents": evidence.get("contents")}, sort_keys=True))

        replayed = call("replay_run", {"source_run_id": "agent-failure", "run_id": "agent-replay"})
        print(json.dumps({"step": "replay", "run_id": replayed.get("run_id"), "result": replayed.get("result", {})}, sort_keys=True))
    finally:
        server.terminate()
        server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
