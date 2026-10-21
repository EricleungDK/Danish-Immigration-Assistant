"""Evaluator, machine evidence and retrieval benchmark share the trust vocabulary (#72)."""

import ast
import json
import unittest
from pathlib import Path

from danish_rag import final_answer_evaluation as fae
from danish_rag import retrieval_benchmark as rb
from danish_rag import trust_indicators as ti
from tests.evaluator_vocabulary_snapshot import GOLDEN_PATH, compute_snapshot

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "danish_rag"
MODULES = (
    "final_answer_evaluation.py",
    "evaluation_machine_evidence.py",
    "retrieval_benchmark.py",
)
HEALTH_STRINGS = {member.value for member in ti.SourceHealth}
# The evaluator must stay an independent check: it never runs the production derivation.
PRODUCTION_DERIVATIONS = {
    "assess_trust_indicators",
    "assess_source_freshness",
    "fresh_tomato_for_source",
    "is_answer_eligible",
    "is_overdue_policy_usable",
    "is_conflicting_evidence",
    "no_material_source_trust_indicators",
}


def _tree(name: str) -> ast.Module:
    return ast.parse((PACKAGE / name).read_text(encoding="utf-8"))


def _is_review_state_use(node: ast.Constant, parents: dict[ast.AST, ast.AST]) -> bool:
    ancestor = parents.get(node)
    while ancestor is not None:
        if isinstance(ancestor, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_ORACLE_ELIGIBLE_REVIEW_STATES"
            for target in ancestor.targets
        ):
            return True
        ancestor = parents.get(ancestor)
    parent = parents.get(node)
    if isinstance(parent, ast.keyword) and parent.arg == "review_state":
        return True
    if isinstance(parent, ast.Compare) and isinstance(parent.left, ast.Call):
        args = parent.left.args
        return bool(args) and isinstance(args[0], ast.Constant) and args[0].value == "review_state"
    return False


def hand_matched_health_strings(name: str) -> list[tuple[int, str]]:
    tree = _tree(name)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in HEALTH_STRINGS
        and id(node) not in docstrings
        and not _is_review_state_use(node, parents)
    ]


class NoHandMatchedHealthStringsTests(unittest.TestCase):
    def test_no_health_strings_in_evaluator_modules(self):
        for name in MODULES:
            self.assertEqual(hand_matched_health_strings(name), [], name)

    def test_no_copied_conflict_state_literals(self):
        for name in MODULES:
            strings = {
                node.value
                for node in ast.walk(_tree(name))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            self.assertFalse("contradicts" in strings, name)


class OracleIndependenceTests(unittest.TestCase):
    def test_evaluator_imports_vocabulary_not_production_derivation(self):
        imported = set()
        for node in ast.walk(_tree("final_answer_evaluation.py")):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("trust_indicators"):
                imported |= {alias.name for alias in node.names}
        self.assertTrue(
            {"SourceHealth", "TrustLevel", "lowest_trust_level",
             "CONFLICTING_AGREEMENT_STATES"} <= imported,
            imported,
        )
        self.assertEqual(imported & PRODUCTION_DERIVATIONS, set())

    def test_evaluator_does_not_reference_production_derivation(self):
        for name in ("final_answer_evaluation.py",):
            names = {
                node.id for node in ast.walk(_tree(name)) if isinstance(node, ast.Name)
            } | {
                node.attr for node in ast.walk(_tree(name)) if isinstance(node, ast.Attribute)
            }
            self.assertEqual(names & PRODUCTION_DERIVATIONS, set(), name)

    def test_oracle_uses_shared_definitions(self):
        self.assertIs(fae.CONFLICTING_AGREEMENT_STATES, ti.CONFLICTING_AGREEMENT_STATES)
        self.assertIs(fae.lowest_trust_level, ti.lowest_trust_level)
        self.assertIs(fae.SourceHealth, ti.SourceHealth)
        self.assertIs(fae.TrustLevel, ti.TrustLevel)

    def test_oracle_owns_eligibility_policy(self):
        for name in ("ELIGIBLE_REVIEW_STATES", "ELIGIBLE_SOURCE_HEALTH"):
            self.assertFalse(hasattr(fae, name), name)
        imported = set()
        for node in ast.walk(_tree("final_answer_evaluation.py")):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("trust_indicators"):
                imported |= {alias.name for alias in node.names}
        self.assertEqual(
            imported,
            {"CONFLICTING_AGREEMENT_STATES", "SourceHealth", "TrustLevel", "lowest_trust_level"},
        )

    def test_drift_alarm_oracle_eligibility_matches_production_today(self):
        # Not a dependency: if production policy changes, update the oracle deliberately.
        self.assertEqual(fae._ORACLE_ELIGIBLE_REVIEW_STATES, ti.ELIGIBLE_REVIEW_STATES)
        self.assertEqual(fae._ORACLE_ELIGIBLE_HEALTH, ti.ELIGIBLE_SOURCE_HEALTH)


class BenchmarkVocabularyTests(unittest.TestCase):
    def test_states_derive_from_the_enum(self):
        self.assertEqual(rb.SOURCE_HEALTH_STATES, {m.value for m in ti.SourceHealth})
        self.assertEqual(
            set(rb.BLOCKED_SOURCE_HEALTH),
            {"changed-unreviewed", "broken", "extraction-failed"},
        )
        self.assertEqual(rb.POLICY_USABLE_OVERDUE_HEALTH, "overdue-policy-usable")


class OutcomesUnchangedTests(unittest.TestCase):
    def test_oracle_outcomes_match_golden_computed_before_the_refactor(self):
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        actual = compute_snapshot()
        for section in golden:
            self.assertEqual(actual[section], golden[section], section)
        self.assertEqual(set(actual), set(golden))


if __name__ == "__main__":
    unittest.main()
