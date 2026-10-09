"""Two-origin discovery fixture for explicit cross-origin exposure checks."""
from __future__ import annotations

import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from webmcp_resilience.commands import CommandAPI
from webmcp_resilience.config import Config


pytestmark = pytest.mark.e2e


class _QuietDirectoryHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return


def _top_page(partner_origin: str) -> str:
    return f"""<!doctype html><iframe id="partner" src="{partner_origin}/child.html"></iframe>
<script type="module">
const partnerOrigin = {partner_origin!r};
const frame = document.querySelector("#partner");
let childTool;
let childReady;
const childReadyPromise = new Promise((resolve) => {{ childReady = resolve; }});
window.addEventListener("message", (event) => {{
  if (event.origin !== partnerOrigin || event.data?.kind !== "partner-tool") return;
  childTool = {{
    ...event.data.tool,
    origin: event.origin,
    window: frame.contentWindow,
    execute: async () => ({{code: "PARTNER"}}),
  }};
  childReady();
}});
const topTool = {{
  name: "top_read",
  description: "A top-origin read-only tool.",
  inputSchema: {{type: "object", properties: {{}}}},
  annotations: {{readOnlyHint: true}},
  execute: async () => ({{code: "TOP"}}),
}};
const overexposed = new URLSearchParams(location.search).get("mode") === "overexposed";
document.modelContext = {{
  __webmcpResilienceCompatibilityHost: true,
  async getTools(options = {{}}) {{
    await childReadyPromise;
    const requested = options.fromOrigins || [];
    const exposePartner = overexposed || requested.includes(partnerOrigin);
    return exposePartner ? [topTool, childTool] : [topTool];
  }},
  async executeTool(tool, args) {{ return tool.execute(typeof args === "string" ? JSON.parse(args) : args); }},
}};
</script>"""


_CHILD_PAGE = """<!doctype html><script type="module">
parent.postMessage({
  kind: "partner-tool",
  tool: {
    name: "partner_read",
    description: "A partner-origin read-only tool.",
    inputSchema: {type: "object", properties: {}},
    annotations: {readOnlyHint: true},
  },
}, "*");
</script>"""


def _scenario(
    invariants: list[dict[str, str]], *, url: str = "/"
) -> dict[str, object]:
    return {
        "name": "cross-origin-exposure",
        "url": url,
        "actors": {"system": [{"action": "wait", "value": "0"}]},
        "cross_origin_invariants": invariants,
    }


def test_cross_origin_discovery_gating_and_replay(tmp_path: Path) -> None:
    partner_directory = tmp_path / "partner"
    partner_directory.mkdir()
    (partner_directory / "child.html").write_text(_CHILD_PAGE)
    partner_server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(_QuietDirectoryHandler, directory=str(partner_directory)),
    )
    partner_thread = Thread(target=partner_server.serve_forever, daemon=True)
    partner_thread.start()

    top_directory = tmp_path / "top"
    top_directory.mkdir()
    top_server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(_QuietDirectoryHandler, directory=str(top_directory)),
    )
    top_origin = f"http://127.0.0.1:{top_server.server_port}"
    partner_origin = f"http://127.0.0.1:{partner_server.server_port}"
    (top_directory / "index.html").write_text(_top_page(partner_origin))
    top_thread = Thread(target=top_server.serve_forever, daemon=True)
    top_thread.start()
    try:
        allowed_api = CommandAPI(
            Config(base_url=top_origin, from_origins=[partner_origin]),
            output_dir=tmp_path / "allowed-runs",
        )
        preflight = asyncio.run(allowed_api.preflight(run_id="cross-preflight"))
        partner_entry = next(
            item
            for item in preflight.preflight["tool_inventory"]
            if item["name"] == "partner_read"
        )
        assert partner_entry["registration"] == {
            "origin": partner_origin,
            "frame": "iframe:0",
        }
        allowed = asyncio.run(
            allowed_api.run(
                scenario=_scenario([{
                    "type": "listed_with_from_origin",
                    "tool": "partner_read",
                    "origin": partner_origin,
                }]),
                run_id="cross-allowed",
            )
        )
        assert allowed.result["passed"] is True

        default_api = CommandAPI(
            Config(base_url=top_origin),
            output_dir=tmp_path / "default-runs",
        )
        default = asyncio.run(
            default_api.run(
                scenario=_scenario([
                    {
                        "type": "not_listed_without_from_origin",
                        "tool": "partner_read",
                        "origin": partner_origin,
                    },
                    {
                        "type": "registration_refused_by_default",
                        "tool": "partner_read",
                        "origin": partner_origin,
                    },
                ]),
                run_id="cross-default",
            )
        )
        assert default.result["passed"] is True

        exposed_api = CommandAPI(
            Config(base_url=top_origin),
            output_dir=tmp_path / "exposed-runs",
        )
        exposed = asyncio.run(
            exposed_api.run(
                scenario=_scenario(
                    [{
                        "type": "not_listed_without_from_origin",
                        "tool": "partner_read",
                        "origin": partner_origin,
                    }],
                    url="/?mode=overexposed",
                ),
                run_id="cross-overexposed",
            )
        )
        assert exposed.result["passed"] is False
        assert exposed.result["error_code"] == "invariant_violation"
        source = exposed_api.save(exposed)
        replayed = asyncio.run(
            exposed_api.replay(source, run_id="cross-overexposed-replay")
        )
        assert replayed.result["passed"] is False
        assert replayed.result["error_code"] == "invariant_violation"
    finally:
        top_server.shutdown()
        top_thread.join()
        top_server.server_close()
        partner_server.shutdown()
        partner_thread.join()
        partner_server.server_close()
