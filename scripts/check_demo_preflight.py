"""Compare the demo fixture's stable preflight shape with committed evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_demo_preflight.py PRELIGHT_JSON EXPECTED_SNAPSHOT")
        return 2

    preflight = json.loads(Path(sys.argv[1]).read_text())
    expected = json.loads(Path(sys.argv[2]).read_text())
    api = preflight["preflight"]["webmcp"]["api"]
    inventory = preflight["tool_inventory"]
    actual = {
        "entry_point": api["location"],
        "tool_count": len(inventory),
        "tool_names": sorted(tool["name"] for tool in inventory),
    }
    if actual == expected:
        print("demo preflight snapshot matches")
        return 0

    print("demo preflight snapshot differs")
    print("expected:", json.dumps(expected, sort_keys=True))
    print("actual:", json.dumps(actual, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
