import html
import json
from pathlib import Path


def render_html(trace_path: Path, output: Path) -> Path:
    """Render a portable, offline event timeline from a versioned JSON trace."""
    trace = json.loads(trace_path.read_text())
    rows = "\n".join(
        f"<tr><td>{event['timestamp_ms']}ms</td><td>{html.escape(event['actor'])}</td><td>{html.escape(event['type'])}</td><td>{html.escape(event.get('name') or '')}</td></tr>"
        for event in trace.get("events", [])
    )
    page = f"""<!doctype html><meta charset=utf-8><title>WebMCP trace</title>
<style>body{{background:#101722;color:#eaf2f3;font:16px system-ui;margin:3rem auto;max-width:980px}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #385063;padding:.65rem;text-align:left}}th{{color:#3bd4d1}}td:first-child{{color:#ffcf8f}}</style>
<h1>Trace: {html.escape(trace.get('scenario', 'unknown'))}</h1><p>Schema {html.escape(trace.get('schema_version', 'unknown'))}</p><table><thead><tr><th>Time</th><th>Actor</th><th>Event</th><th>Tool/action</th></tr></thead><tbody>{rows}</tbody></table>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page)
    return output
