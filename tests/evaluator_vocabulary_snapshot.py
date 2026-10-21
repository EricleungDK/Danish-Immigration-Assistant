"""Outcome snapshot of the evaluator's trust vocabulary oracle (#72).

``compute_snapshot`` is the behavior pinned by
``tests/fixtures/evaluator_vocabulary_golden.json``, computed at the commit before #72
moved the evaluator onto the shared trust vocabulary.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

from danish_rag import final_answer_evaluation as fae
from danish_rag.evaluation_machine_evidence import run_source_policy_scenario
from danish_rag.evaluation_quality_bar import load_evaluation_cases
from tests.evaluation_time import fixed_clock

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "data/evaluation/evaluation-set-v0.1-candidate.json"
GOLDEN_PATH = ROOT / "tests/fixtures/evaluator_vocabulary_golden.json"

HEALTH_VALUES = [
    "healthy",
    "overdue-policy-usable",
    "changed-unreviewed",
    "broken",
    "extraction-failed",
    "unknown",
    None,
]
REVIEW_STATES = ["approved-current", "overdue-policy-usable", "changed-unreviewed", None]
APPROVAL_STATES = ["approved", "pending", None]
AGREEMENT_STATES = [
    None, "supports", "conflict", "conflicts", "conflicting", "contradicts", "CONFLICTS", "other",
]
SCENARIO_IDS = [
    "eval-010-conflicting-official-sources",
    "eval-011-overdue-policy-usable-source",
    "eval-012-changed-source-blocked",
    "eval-013-retrieval-miss-cannot-be-masked",
]


def _item(**fields: Any) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


def compute_snapshot() -> dict[str, Any]:
    eligibility = {}
    for health, review, approval in itertools.product(
        HEALTH_VALUES, REVIEW_STATES, APPROVAL_STATES
    ):
        item = _item(source_health=health, review_state=review, approval_state=approval)
        eligibility[json.dumps(item, sort_keys=True)] = fae._eligible_material_source(item)

    freshness = {}
    for size in (0, 1, 2):
        for combo in itertools.product(HEALTH_VALUES, repeat=size):
            items = [_item(source_health=health) for health in combo]
            freshness[json.dumps(items)] = fae._expected_fresh_tomato(items)

    confidence = {}
    for agreement, flag in itertools.product(AGREEMENT_STATES, [None, True, False]):
        items = [_item(agreement_state=agreement, conflicts_with_answer=flag)]
        for facts, cited in ((0, 0), (2, 2), (2, 1)):
            key = json.dumps([items, facts, cited])
            confidence[key] = fae._expected_evidence_confidence(
                official_fact_count=facts,
                cited_official_fact_count=cited,
                material_evidence=items,
            )

    mixed_items = [
        {},
        {"agreement_state": "supports"},
        {"agreement_state": "Conflicting"},
        {"agreement_state": "contradicts"},
        {"agreement_state": "supports", "conflicts_with_answer": True},
        {"agreement_state": "conflicts", "conflicts_with_answer": False},
        {"conflicts_with_answer": False},
    ]
    confidence_multi = {}
    for size in (2, 3):
        for combo in itertools.product(range(len(mixed_items)), repeat=size):
            items = [mixed_items[index] for index in combo]
            for facts, cited in ((0, 0), (2, 2), (2, 1)):
                key = json.dumps([items, facts, cited])
                confidence_multi[key] = fae._expected_evidence_confidence(
                    official_fact_count=facts,
                    cited_official_fact_count=cited,
                    material_evidence=items,
                )

    cases = {case["id"]: case for case in load_evaluation_cases(DATASET_PATH)["cases"]}
    scenarios = {
        case_id: run_source_policy_scenario(cases[case_id], clock=fixed_clock)
        for case_id in SCENARIO_IDS
    }
    return json.loads(
        json.dumps(
            {
                "eligibility": eligibility,
                "fresh_tomato": freshness,
                "evidence_confidence": confidence,
                "evidence_confidence_multi": confidence_multi,
                "scenarios": scenarios,
            }
        )
    )
