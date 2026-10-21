"""Contract guards for the shared fake (#77): replace, don't layer.

Every test, monitor and fixture reaches a model only through the Local model transport, and
the only fake is `FakeTransport` (served by `ControlledModel` when a deterministic model is
needed). These guards fail if an ad-hoc generator, a subclass of a production adapter, a
private monitor fixture, a stand-in urllib opener, or the old generator seam comes back.
"""

import ast
import inspect
import re
import unittest
from pathlib import Path

from danish_rag import answer_pipeline
from danish_rag.local_app import create_app

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("danish_rag", "tests", "recording")
PRODUCTION_ADAPTERS = {
    "TransportAnswerGenerator", "OllamaTransport", "OpenAICompatibleTransport",
    "OllamaEmbeddingProvider", "TransportSocialReplies",
}
#: Seam tests of the answer module's collaborator contract (a generator that cannot
#: repair, a duck-typed generator). They take no part in how fixtures reach a model.
CONTRACT_SEAM_TESTS = {"tests/test_evidence_bounded_answer.py"}
#: Tests may keep this deterministic embedding provider fixture (it is not a model fake).
EMBEDDING_FIXTURE = "tests/embedding_provider_fixture.py"


def sources():
    for directory in SCANNED:
        for path in sorted((ROOT / directory).rglob("*.py")):
            yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")


def classes():
    for relative, text in sources():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ClassDef):
                yield relative, node


def method_names(node: ast.ClassDef) -> set[str]:
    return {
        item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class SharedFakeContractTests(unittest.TestCase):
    def test_the_old_generator_protocol_is_deleted(self):
        self.assertFalse(hasattr(answer_pipeline, "AnswerGenerator"))

    def test_the_application_factory_accepts_only_transport_wiring(self):
        parameters = inspect.signature(create_app).parameters
        self.assertIn("transport", parameters)
        self.assertNotIn("answer_generator", parameters)
        with self.assertRaises(TypeError):
            create_app(answer_generator=object())

    def test_no_code_hands_the_application_an_answer_generator(self):
        offenders = [
            relative for relative, text in sources()
            if relative != "tests/test_shared_fake_contract.py"
            and re.search(r"answer_generator\s*=", text)
        ]
        self.assertEqual(offenders, [])

    def test_no_class_subclasses_a_production_adapter_or_the_fake(self):
        offenders = [
            f"{relative}:{node.name}" for relative, node in classes()
            if any(
                isinstance(base, (ast.Name, ast.Attribute))
                and getattr(base, "id", getattr(base, "attr", None))
                in PRODUCTION_ADAPTERS | {"FakeTransport"}
                for base in node.bases
            )
        ]
        self.assertEqual(offenders, [])

    def test_no_ad_hoc_answer_generator_or_social_replier_exists(self):
        """Only the production adapters write answers; fakes live at the transport."""

        offenders = []
        for relative, node in classes():
            if node.name in PRODUCTION_ADAPTERS:
                continue
            names = method_names(node)
            if "generate" in names and any(
                    isinstance(item, ast.FunctionDef) and item.name == "generate"
                    and "normalized_question" in {a.arg for a in item.args.kwonlyargs}
                    for item in node.body):
                offenders.append(f"{relative}:{node.name}.generate")
            if "converse" in names and node.name != "SocialReplies":
                offenders.append(f"{relative}:{node.name}.converse")
            if "repair_answer" in names and relative not in CONTRACT_SEAM_TESTS:
                offenders.append(f"{relative}:{node.name}.repair_answer")
        self.assertEqual(offenders, [])

    def test_no_ad_hoc_embedding_provider_outside_the_shared_fixture(self):
        offenders = [
            f"{relative}:{node.name}" for relative, node in classes()
            if {"inspect_model", "embed"} <= method_names(node)
            and relative not in {EMBEDDING_FIXTURE, "danish_rag/embedding_provider.py"}
        ]
        self.assertEqual(offenders, [])

    def test_monitors_and_evaluation_use_the_shared_fake_not_private_fixtures(self):
        private = {
            "_FixtureAnswerGenerator", "_FixtureEmbeddingProvider",
            "_UnavailableEvaluationGenerator", "_DeterministicEvaluationEmbeddingProvider",
            "ControlledAnswerGenerator",
        }
        text = {relative: body for relative, body in sources()}
        for relative in (
            "danish_rag/release_monitors.py", "danish_rag/supported_environment_monitor.py",
            "danish_rag/evaluation_machine_evidence.py", "danish_rag/final_answer_evaluation.py",
        ):
            for name in private:
                self.assertNotIn(name, text[relative], f"{relative} keeps {name}")
        self.assertIn("FakeTransport", text["danish_rag/release_monitors.py"])

    def test_no_test_stands_in_for_local_model_calls_with_a_urllib_opener(self):
        offenders = []
        for relative, body in sources():
            if relative == "tests/test_shared_fake_contract.py":
                continue
            if re.search(r"install_opener|build_opener", body) and relative.startswith("tests/"):
                offenders.append(relative)
            for call in re.findall(r"patch[\w.]*\((?:[^()]|\([^()]*\))*urlopen[^)]*\)", body):
                if re.search(r"return_value|new=|new_callable", call):
                    offenders.append(f"{relative}: {call}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
