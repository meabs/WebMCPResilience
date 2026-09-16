import asyncio
import json
from pathlib import Path

from webmcp_resilience.commands import CommandAPI, build_webmcp_compatibility_report
from webmcp_resilience.config import Config


def probe(*, available: bool = True) -> dict:
    return {
        "api": {
            "available": available,
            "location": "navigator.modelContext" if available else None,
            "mode": "native" if available else "unavailable",
            "native": available,
            "compatibilityHost": False,
            "getTools": available,
            "executeTool": available,
            "toolchange": available,
            "cancellation": available,
        },
        "document": {
            "origin": "https://app.example",
            "url": "https://app.example/",
            "secureContext": True,
            "crossOriginIsolated": True,
            "isTopLevel": True,
            "topOrigin": "https://app.example",
            "sameOriginWithTop": True,
            "iframeCount": 0,
            "frames": [],
        },
        "permissionsPolicy": {"available": True, "features": ["model-context"], "modelContextAllowed": True},
        "runtime": {"userAgent": "TestBrowser/1", "headless": True, "navigation": True},
        "lifecycle": {"toolchangeListenerSupported": available},
    }


def test_unsupported_browser_api_returns_structured_report_and_artifact(tmp_path: Path) -> None:
    report = build_webmcp_compatibility_report(
        probe(available=False), [], browser={"name": "chromium", "version": "1", "channel": "default", "headless": True}
    )
    assert report["report_kind"] == "webmcp_compatibility"
    assert report["non_mutating"] is True
    assert report["summary"]["status"] == "unsupported"
    assert report["findings"][0]["id"] == "webmcp-api-unavailable"
    assert report["findings"][0]["source_evidence"]["path"] == "webmcp.api.available"


def test_permission_origin_and_iframe_findings_are_deterministic() -> None:
    evidence = probe()
    evidence["document"].update({
        "secureContext": False,
        "crossOriginIsolated": False,
        "isTopLevel": False,
        "topOrigin": "https://parent.example",
        "sameOriginWithTop": False,
        "iframeCount": 1,
        "frames": [{"index": 0, "src": "https://third-party.example/", "sameOrigin": False, "sandbox": "", "allow": None}],
    })
    evidence["permissionsPolicy"].update({"available": True, "modelContextAllowed": False})
    first = build_webmcp_compatibility_report(evidence, [{"name": "read", "inputSchema": {}, "annotations": None}], browser={})
    second = build_webmcp_compatibility_report(evidence, [{"name": "read", "inputSchema": {}, "annotations": None}], browser={})
    assert first == second
    ids = {finding["id"] for finding in first["findings"]}
    assert {"insecure-context", "permissions-policy-denied", "iframe-topology", "cross-origin-iframe", "origin-not-isolated"} <= ids


def test_empty_inventory_is_reported_when_webmcp_is_available() -> None:
    report = build_webmcp_compatibility_report(probe(), [], browser={})
    finding = next(item for item in report["findings"] if item["id"] == "tool-inventory-empty")
    assert finding["severity"] == "error"
    assert report["summary"]["status"] == "unsupported"


def test_command_preflight_never_invokes_a_page_tool(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    class FakePage:
        url = "https://app.example/"

        async def goto(self, url: str) -> None:
            self.url = url

    class FakeBrowser:
        version = "123.0"

    class FakeClient:
        page = FakePage()
        browser = FakeBrowser()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    class FakeAdapter:
        def __init__(self, *_args, **_kwargs):
            pass

        async def install(self) -> None:
            calls.append("install")

        async def probe(self) -> dict:
            return probe()

        async def get_tools(self) -> list[dict]:
            calls.append("get_tools")
            return [{"name": "read", "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}}]

        async def invoke_tool(self, *_args, **_kwargs):
            calls.append("invoke_tool")
            raise AssertionError("preflight must not invoke a page tool")

    monkeypatch.setattr("webmcp_resilience.commands.BrowserClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr("webmcp_resilience.commands.WebMCPAdapter", FakeAdapter)
    bundle = asyncio.run(CommandAPI(Config(base_url="https://app.example")).preflight(run_id="preflight-check"))
    assert "invoke_tool" not in calls
    assert bundle.preflight["non_mutating"] is True
    assert bundle.browser_environment["version"] == "123.0"
    assert bundle.browser_environment["channel"] == "default"
    assert bundle.compatibility.tool_inventory_fingerprint
    assert bundle.inventory_contract["tools"][0]["fingerprint"]
    assert bundle.tool_contract_drift["status"] == "unchanged"
    assert bundle.result["tool_contract_drift"]["status"] == "unchanged"
    report_artifact = next(artifact for artifact in bundle.artifacts if artifact.kind == "report")
    report = json.loads(Path(report_artifact.path).read_text())
    assert report["report_kind"] == "webmcp_compatibility"


def test_command_preflight_rechecks_empty_inventory_after_registration_grace(tmp_path: Path, monkeypatch) -> None:
    """Preflight must wait for delayed registration before reporting no tools."""
    class FakePage:
        url = "https://app.example/"

        async def goto(self, url: str) -> None:
            self.url = url

    class FakeBrowser:
        version = "123.0"

    class FakeClient:
        page = FakePage()
        browser = FakeBrowser()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    class FakeAdapter:
        def __init__(self, *_args, **_kwargs):
            self.discovery_count = 0

        async def install(self) -> None:
            pass

        async def probe(self) -> dict:
            return probe()

        async def get_tools(self) -> list[dict]:
            self.discovery_count += 1
            if self.discovery_count == 1:
                return []
            return [{"name": "read", "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}}]

    monkeypatch.setattr("webmcp_resilience.commands.BrowserClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr("webmcp_resilience.commands.WebMCPAdapter", FakeAdapter)
    bundle = asyncio.run(CommandAPI(Config(base_url="https://app.example", tool_registration_grace_ms=1)).preflight())

    assert [tool["name"] for tool in bundle.tool_inventory] == ["read"]
    assert bundle.preflight["inventory_changes"]["added"] == ["read"]
