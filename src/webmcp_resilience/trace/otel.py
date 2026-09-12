import json
from pathlib import Path


def export_otel(trace_path: Path, output: Path) -> Path:
    """Export the unified events as OpenTelemetry-compatible log records."""
    trace = json.loads(trace_path.read_text())
    records = [{"timeUnixNano": str(event["timestamp_ms"] * 1_000_000), "body": {"stringValue": event["type"]},
                "attributes": [{"key": "webmcp.actor", "value": {"stringValue": event["actor"]}},
                               {"key": "webmcp.scenario", "value": {"stringValue": trace["scenario"]}}]}
               for event in trace.get("events", [])]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"resourceLogs": [{"scopeLogs": [{"logRecords": records}]}]}, indent=2))
    return output
