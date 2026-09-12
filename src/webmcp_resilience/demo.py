"""Serve the independent WebMCP Resilience Lab test application."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path


class _QuietHandler(SimpleHTTPRequestHandler):
    """Keep local demo access logs out of machine-readable CLI output."""

    def log_message(self, *_args: object) -> None:
        return


def create_server(host: str = "127.0.0.1", port: int = 4173) -> ThreadingHTTPServer:
    site = Path(files("webmcp_resilience").joinpath("lab")).resolve()
    if not site.is_dir():  # Editable installs serve the source example directly.
        site = Path(__file__).resolve().parents[2] / "examples" / "lab"
    if not site.is_dir():
        raise RuntimeError("Resilience Lab assets are unavailable in this installation")
    handler = partial(_QuietHandler, directory=str(site))
    return ThreadingHTTPServer((host, port), handler)


def forge_scenario(name: str = "vulnerable-human-tool-race") -> Path:
    """Return an included local Resilience Forge scenario."""
    packaged = Path(files("webmcp_resilience").joinpath("resilience_forge", ".webmcp", "scenarios", f"{name}.yaml"))
    if packaged.is_file():
        return packaged
    source = Path(__file__).resolve().parents[2] / "examples" / "resilience-forge" / ".webmcp" / "scenarios" / f"{name}.yaml"
    if not source.is_file():
        raise RuntimeError(f"included Resilience Forge scenario is unavailable: {source}")
    return source


def serve(host: str = "127.0.0.1", port: int = 4173) -> None:
    server = create_server(host, port)
    print(f"WebMCP Resilience Lab: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
