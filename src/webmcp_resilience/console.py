import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .models.bundle import RunBundle, redact_recursive
from .console_client import bundle_comparison, bundle_trace, timeline_rows, visible_events


def show_trace(path: Path, console: Console, compare: Path | None = None) -> None:
    payload = json.loads(path.read_text())
    bundle = None
    try:
        bundle = RunBundle.model_validate(payload)
    except Exception:
        trace = redact_recursive(payload)
        baseline = None
    else:
        trace = bundle_trace(bundle)
        baseline = None
        if compare is not None:
            try:
                baseline = RunBundle.model_validate_json(compare.read_text())
            except Exception:
                baseline = None
    baseline_payload = None
    if compare is not None:
        try:
            baseline_payload = bundle_trace(baseline) if baseline is not None else redact_recursive(json.loads(compare.read_text()))
        except Exception:
            baseline_payload = None
    discovered = sum(event["type"] == "tool.discovered" for event in trace.get("events", []))
    lifecycle = sum(event["type"] == "tool.change" for event in trace.get("events", []))
    safe_artifacts = 0
    metadata_only = 0
    if baseline_payload is not None:
        safe_artifacts = sum(item.redacted and item.sensitivity == "redacted" for item in bundle.artifacts) if bundle else 0
        metadata_only = sum(not (item.redacted and item.sensitivity == "redacted") for item in bundle.artifacts) if bundle else 0
        if bundle is not None and baseline is not None:
            diff = bundle_comparison(bundle, baseline)
            console.print(f"[cyan]bundle comparison[/cyan] · result changed={diff['result_changed']} · "
                          f"browser changed={diff['browser_changed']} · events +{len(diff['events_only_left'])}/-{len(diff['events_only_right'])}")
        else:
            current_events = {(event.get("actor"), event.get("type"), event.get("name")) for event in trace.get("events", [])}
            baseline_events = {(event.get("actor"), event.get("type"), event.get("name")) for event in baseline_payload.get("events", [])}
            console.print(f"[cyan]trace comparison[/cyan] · events +{len(current_events - baseline_events)}/-{len(baseline_events - current_events)}")
    console.print(f"[cyan]{discovered} tools discovered[/cyan] · [dim]{lifecycle} lifecycle events collapsed[/dim] · "
                  f"[dim]visible events={len(visible_events(trace))}[/dim]")
    if bundle is not None:
        approvals = bundle.approvals
        authority = approvals[-1].authority if approvals else "read_only"
        console.print(f"[dim]run={bundle.run_id} · result={'PASS' if bundle.result.get('passed') else 'FAIL'} · "
                      f"seed={bundle.execution.get('seed')} · capability={bundle.compatibility.capability_fingerprint or 'unknown'} · "
                      f"authority={authority} · sensitive artifacts=metadata-only[/dim]")
    if baseline is not None:
        console.print(f"[dim]safe redacted artifacts={safe_artifacts} · metadata-only artifacts={metadata_only}[/dim]")
    table = Table(title=f"WebMCP Console · {trace['scenario']}")
    table.add_column("Time", style="cyan", no_wrap=True)
    table.add_column("Actor")
    table.add_column("Event")
    table.add_column("Tool / result")
    for event in trace.get("events", []):
        if event["type"] in {"tool.discovered", "tool.change"}:
            continue
        detail = event.get("name") or ""
        result = event.get("data", {}).get("result")
        if isinstance(result, dict) and result.get("code"):
            detail = f"{detail} · {result['code']}"
        table.add_row(f"+{event['timestamp_ms']}ms", event['actor'], event['type'], detail)
    console.print(table)
    failures = [event for event in timeline_rows(trace) if event["failure"]]
    if failures:
        console.print("[red]Failure links[/red]")
        for row in failures:
            console.print(f"  {row['event_type']} · {row['event'].get('data', {}).get('expression', 'unknown')} · "
                          f"state={json.dumps(row['event'].get('state_snapshot', {}), sort_keys=True)}")
