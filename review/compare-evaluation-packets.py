"""Compare private final-answer review packets without printing their contents."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def executions(path: str) -> dict[str, tuple[str, str]]:
    packet: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        str(case["case_id"]): (
            str(case["execution_sha256"]),
            str(case["execution"].get("error_type") or ""),
        )
        for case in packet["cases"]
    }


if len(sys.argv) != 3:
    raise SystemExit(
        "usage: compare-evaluation-packets.py PACKET_A.json PACKET_B.json"
    )

first = executions(sys.argv[1])
second = executions(sys.argv[2])
changed = sorted(
    case_id
    for case_id in first.keys() | second.keys()
    if first.get(case_id) != second.get(case_id)
)
errors = {
    case_id: error_type
    for case_id, (_execution_hash, error_type) in second.items()
    if error_type
}

if changed:
    print("MISMATCH - execution results changed:")
    print("\n".join(changed))
else:
    print("MATCH - fresh executions are stable")

if errors:
    print("EXECUTION ERRORS:")
    for case_id, error_type in sorted(errors.items()):
        print(f"{case_id}: {error_type}")
else:
    print("NO EXECUTION ERRORS")

raise SystemExit(bool(changed or errors))
