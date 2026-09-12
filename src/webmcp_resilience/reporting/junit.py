from pathlib import Path
from xml.etree.ElementTree import Element, ElementTree, SubElement

from ..models.result import RunResult


def write_junit(result: RunResult, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    suite = Element("testsuite", name="webmcp-resilience", tests=str(len(result.scenarios)),
                    failures=str(sum(not item.passed for item in result.scenarios)))
    for item in result.scenarios:
        case = SubElement(suite, "testcase", classname="webmcp_resilience", name=item.scenario,
                          time=f"{item.duration_ms / 1000:.3f}")
        if not item.passed:
            failure = SubElement(case, "failure", message=item.error or "scenario failed")
            failure.text = item.trace.model_dump_json(indent=2)
    ElementTree(suite).write(destination, encoding="utf-8", xml_declaration=True)
