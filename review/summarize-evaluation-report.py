"""Print only non-sensitive identity and status fields from an evaluation report."""

from __future__ import annotations

import json
import sys
from pathlib import Path


for value in sys.argv[1:]:
    path = Path(value)
    report = json.loads(path.read_text(encoding="utf-8"))
    print(path)
    print(f"identity={json.dumps(report.get('identity'), sort_keys=True)}")
    print(f"execution={json.dumps(report.get('execution'), sort_keys=True)}")
    print(
        "threshold_failures="
        f"{json.dumps(report.get('threshold_failures'), sort_keys=True)}"
    )
