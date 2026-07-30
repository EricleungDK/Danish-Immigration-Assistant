"""Report structural drift between private packets without exposing their content."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_cases(path: str) -> dict[str, dict[str, Any]]:
    packet = json.loads(Path(path).read_text(encoding="utf-8"))
    return {str(case["case_id"]): case for case in packet["cases"]}


def evidence_without_scores(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in item.items() if key != "retrieval_score"}
        for item in items
    ]


if len(sys.argv) != 3:
    raise SystemExit(
        "usage: diagnose-evaluation-packet-drift.py PACKET_A.json PACKET_B.json"
    )

first = load_cases(sys.argv[1])
second = load_cases(sys.argv[2])

for case_id in sorted(first.keys() | second.keys()):
    left = first.get(case_id)
    right = second.get(case_id)
    if left is None or right is None:
        print(f"{case_id}: case-presence")
        continue
    if left.get("execution_sha256") == right.get("execution_sha256"):
        continue

    left_execution = left["execution"]
    right_execution = right["execution"]
    changed: list[str] = []

    if left_execution.get("error_type") != right_execution.get("error_type"):
        changed.append("error-type")

    left_evidence = left_execution.get("evidence") or []
    right_evidence = right_execution.get("evidence") or []
    left_ids = [str(item.get("citation_id")) for item in left_evidence]
    right_ids = [str(item.get("citation_id")) for item in right_evidence]
    if left_ids != right_ids:
        changed.append("evidence-ids-or-order")
    if digest(evidence_without_scores(left_evidence)) != digest(
        evidence_without_scores(right_evidence)
    ):
        changed.append("evidence-content-or-metadata")
    if [item.get("retrieval_score") for item in left_evidence] != [
        item.get("retrieval_score") for item in right_evidence
    ]:
        changed.append("retrieval-scores")

    left_result = left_execution.get("result")
    right_result = right_execution.get("result")
    if (left_result is None) != (right_result is None):
        changed.append("result-presence")
    elif isinstance(left_result, dict) and isinstance(right_result, dict):
        for key in sorted(left_result.keys() | right_result.keys()):
            if digest(left_result.get(key)) != digest(right_result.get(key)):
                changed.append(f"result.{key}")

        left_answer = left_result.get("answer") or {}
        right_answer = right_result.get("answer") or {}
        if isinstance(left_answer, dict) and isinstance(right_answer, dict):
            answer_changes = [
                key
                for key in sorted(left_answer.keys() | right_answer.keys())
                if digest(left_answer.get(key)) != digest(right_answer.get(key))
            ]
            if answer_changes:
                changed.append("answer-fields=" + ",".join(answer_changes))

            left_sections = left_answer.get("sections") or []
            right_sections = right_answer.get("sections") or []
            changed_sections = [
                str(index)
                for index in range(1, max(len(left_sections), len(right_sections)) + 1)
                if digest(
                    left_sections[index - 1] if index <= len(left_sections) else None
                )
                != digest(
                    right_sections[index - 1] if index <= len(right_sections) else None
                )
            ]
            if changed_sections:
                changed.append("answer-sections=" + ",".join(changed_sections))

    print(f"{case_id}: {'; '.join(changed) or 'unclassified'}")
