from pathlib import Path
from xml.etree.ElementTree import parse

from webmcp_resilience.models.result import RunResult, ScenarioResult
from webmcp_resilience.models.trace import TraceRun
from webmcp_resilience.reporting.junit import write_junit


def test_writes_machine_readable_failure(tmp_path: Path) -> None:
    output = tmp_path / "result.xml"
    write_junit(RunResult(scenarios=[ScenarioResult(scenario="observed-app", passed=False, duration_ms=5,
        error="observed invariant failed", trace=TraceRun(scenario="observed-app"))]), output)
    root = parse(output).getroot()
    assert root.attrib["failures"] == "1"
    assert root.find("testcase/failure") is not None
