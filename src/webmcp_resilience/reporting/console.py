from rich.console import Console

from ..models.result import ScenarioResult


def render_result(console: Console, result: ScenarioResult) -> None:
    status = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
    console.print(f"{status} {result.scenario} ({result.duration_ms}ms)")
    if result.error:
        console.print(f"  {result.error}")
    if result.failure_path:
        console.print(f"  Reproduction: {result.failure_path}")
