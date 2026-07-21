import unittest
from typing import Any

from danish_rag.answer_pipeline import (
    AnswerService,
    AnswerValidationError,
    answer_schema,
    validate_answer,
)
from danish_rag.provider_setup import ProviderConfiguration


def provider_configuration() -> ProviderConfiguration:
    return ProviderConfiguration(
        provider_id="openai_compatible",
        endpoint="http://127.0.0.1:1234",
        model="fixture-model",
        provider_version="fixture-provider",
        model_identity={"id": "fixture-model"},
        capabilities=["generation"],
        validated_at_utc="2026-07-06T12:00:00+00:00",
    )


def evidence_fixture(
    citation_id: str,
    *,
    content: str = (
        "Official fixture content. One approved source describes a Danish-test "
        "requirement. The stale source describes Prøve i Dansk 3."
    ),
    source_health: str = "healthy",
    review_state: str = "approved-current",
    approval_state: str = "approved",
    agreement_state: str = "supports",
    topic_tags: list[str] | None = None,
) -> dict[str, object]:
    return {
        "citation_id": citation_id,
        "document_id": citation_id,
        "source_id": f"source-{citation_id}",
        "title": f"Fixture source {citation_id}",
        "publisher": "SIRI",
        "official_url": f"https://www.nyidanmark.dk/{citation_id}",
        "checked_at_utc": "2026-06-15T09:00:00Z",
        "knowledge_release_id": "kr-fixture",
        "corpus_identity": "kr-fixture",
        "review_state": review_state,
        "source_health": source_health,
        "approval_state": approval_state,
        "agreement_state": agreement_state,
        "topic_tags": topic_tags or ["permanent-residence", "language-requirement"],
        "content": content,
    }


class FixtureRetriever:
    manifest = {"corpus_id": "kr-fixture"}

    def __init__(self, evidence: list[dict[str, object]]) -> None:
        self.evidence = evidence
        self.calls: list[str] = []

    def retrieve(self, question: str) -> list[dict[str, object]]:
        self.calls.append(question)
        return self.evidence


class FixtureGenerator:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "question": question,
                "evidence_ids": [item["citation_id"] for item in evidence],
                "schema": schema,
            }
        )
        return self.payload


class Issue13EvidenceSafetyTests(unittest.TestCase):
    def test_personal_eligibility_question_keeps_supported_fact_and_scoped_refusal(self):
        evidence = [
            evidence_fixture(
                "language-source",
                content=(
                    "Permanent opholdstilladelse kan kræve dokumentation for "
                    "bestået Prøve i Dansk 2."
                ),
            )
        ]
        generator = FixtureGenerator(
            {
                "summary": "The source supports a language-requirement fact only.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": (
                            "Permanent residence can require documentation for "
                            "bestået Prøve i Dansk 2."
                        ),
                        "citation_ids": ["language-source"],
                    }
                ],
            }
        )

        result = AnswerService(
            retriever=FixtureRetriever(evidence),
            generator=generator,
        ).answer(
            "I passed PD2, have lived in Denmark for 7 years, and have a job. "
            "Do I qualify for permanent residence?",
            provider_configuration(),
        )

        self.assertEqual(
            generator.calls[0]["schema"],
            answer_schema(["language-source"]),
        )
        self.assertIn("bestået Prøve i Dansk 2", result.answer["sections"][0]["text"])
        self.assertIn("language-source", result.answer["sections"][0]["citation_ids"])
        refusal_sections = [
            section
            for section in result.answer["sections"]
            if section["kind"] == "refusal"
        ]
        self.assertEqual(len(refusal_sections), 1)
        self.assertIn("personal eligibility", refusal_sections[0]["text"])
        answer_text = " ".join(
            [result.answer["summary"], *[section["text"] for section in result.answer["sections"]]]
        ).casefold()
        self.assertNotIn("you qualify", answer_text)
        self.assertNotIn("you are eligible", answer_text)

    def test_personal_eligibility_conclusion_from_generator_is_rejected(self):
        evidence = [evidence_fixture("language-source")]
        generator = FixtureGenerator(
            {
                "summary": "You qualify for permanent residence.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": "You qualify because PD2 is enough.",
                        "citation_ids": ["language-source"],
                    }
                ],
            }
        )

        with self.assertRaisesRegex(AnswerValidationError, "prohibited"):
            AnswerService(
                retriever=FixtureRetriever(evidence),
                generator=generator,
            ).answer(
                "I passed PD2, have lived in Denmark for 7 years, and have a job. "
                "Do I qualify for permanent residence?",
                provider_configuration(),
            )

    def test_safety_sensitive_summary_is_replaced_before_validation(self):
        evidence = [
            evidence_fixture(
                "equivalence-source",
                content="One approved source describes a Danish-test requirement.",
            )
        ]
        generator = FixtureGenerator(
            {
                "summary": "The page does not decide whether SIRI will accept it.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": "One approved source describes a Danish-test requirement.",
                        "citation_ids": ["equivalence-source"],
                    },
                    {
                        "kind": "refusal",
                        "text": "I cannot decide whether SIRI will accept it.",
                        "citation_ids": [],
                    },
                ],
            }
        )

        result = AnswerService(
            retriever=FixtureRetriever(evidence),
            generator=generator,
        ).answer(
            "I have an old Danish certificate. Can you tell me whether SIRI will "
            "accept it for permanent residence?",
            provider_configuration(),
        )

        self.assertEqual(
            result.answer["summary"],
            "This answer separates supported official facts from the personal or "
            "legal decision I cannot make.",
        )
        self.assertNotIn("will accept", result.answer["summary"].casefold())

    def test_legal_advice_request_is_refused_without_generation(self):
        boundary_evidence = [
            evidence_fixture(
                "boundary-source",
                content="Assistenten skal afvise personlig juridisk rådgivning.",
                topic_tags=["safety-boundary", "evidence-boundary"],
            )
        ]
        generator = FixtureGenerator({"summary": "should not run", "sections": []})

        result = AnswerService(
            retriever=FixtureRetriever(boundary_evidence),
            generator=generator,
        ).answer(
            "Give me legal advice on how to argue that my Danish test should count.",
            provider_configuration(),
        )

        self.assertEqual(generator.calls, [])
        self.assertEqual(result.answer["response_kind"], "refusal")
        self.assertEqual(result.answer["sections"][0]["kind"], "refusal")
        self.assertEqual(result.answer["sections"][0]["citation_ids"], ["boundary-source"])
        self.assertIn("legal advice", result.answer["sections"][0]["text"])

    def test_retrieval_miss_returns_refusal_without_generator_substitute_fact(self):
        generator = FixtureGenerator(
            {
                "summary": "PD2 is enough without sources.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": "PD2 is enough.",
                        "citation_ids": [],
                    }
                ],
            }
        )

        result = AnswerService(
            retriever=FixtureRetriever([]),
            generator=generator,
        ).answer(
            "I heard PD2 is enough. Just confirm it quickly without sources.",
            provider_configuration(),
        )

        self.assertEqual(generator.calls, [])
        self.assertEqual(result.answer["response_kind"], "refusal")
        self.assertEqual(result.answer["citations"], [])
        self.assertIn("No approved official evidence", result.answer["summary"])
        self.assertNotIn("PD2 is enough", result.answer["summary"])

    def test_conflicting_approved_sources_are_both_cited_with_warning(self):
        evidence = [
            evidence_fixture("source-a"),
            evidence_fixture(
                "source-b",
                content="Another approved page appears to say a different Danish test.",
                agreement_state="conflicts",
            ),
        ]
        generator = FixtureGenerator(
            {
                "summary": "The answer is limited because sources conflict.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": "One approved source describes a Danish-test requirement.",
                        "citation_ids": ["source-a"],
                    }
                ],
            }
        )

        result = AnswerService(
            retriever=FixtureRetriever(evidence),
            generator=generator,
        ).answer(
            "One official page says PD2 and another appears to say a different Danish test. "
            "Which one is right?",
            provider_configuration(),
        )

        self.assertEqual(result.answer["trust"]["evidence_confidence"], "Low")
        warning_sections = [
            section
            for section in result.answer["sections"]
            if section["kind"] == "source_warning"
        ]
        self.assertEqual(len(warning_sections), 1)
        self.assertEqual(warning_sections[0]["citation_ids"], ["source-a", "source-b"])
        material_ids = {citation["citation_id"] for citation in result.answer["citations"]}
        self.assertEqual(material_ids, {"source-a", "source-b"})

    def test_overdue_policy_usable_source_gets_visible_warning_and_medium_freshness(self):
        evidence = [evidence_fixture("stale-source", source_health="overdue-policy-usable")]
        generator = FixtureGenerator(
            {
                "summary": "A policy-usable stale source supports only a limited answer.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": "The stale source describes Prøve i Dansk 3.",
                        "citation_ids": ["stale-source"],
                    }
                ],
            }
        )

        result = AnswerService(
            retriever=FixtureRetriever(evidence),
            generator=generator,
        ).answer(
            "Can an overdue but still policy-usable official source support a Danish exam answer?",
            provider_configuration(),
        )

        self.assertEqual(result.answer["trust"]["fresh_tomato_score"], "Medium")
        self.assertTrue(
            any(
                section["kind"] == "source_warning"
                and "overdue" in section["text"].casefold()
                and section["citation_ids"] == ["stale-source"]
                for section in result.answer["sections"]
            )
        )

    def test_blocked_source_states_cannot_support_generated_claims(self):
        payload = {
            "summary": "Blocked source claim.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": "A changed source says the requirement is different.",
                    "citation_ids": ["blocked-source"],
                }
            ],
        }

        for review_state, source_health, approval_state in [
            ("approved-current", "changed-unreviewed", "approved"),
            ("approved-current", "broken", "approved"),
            ("approved-current", "extraction-failed", "approved"),
            ("approved-current", "healthy", "unapproved"),
            ("changed-unreviewed", "healthy", "approved"),
        ]:
            with self.subTest(
                review_state=review_state,
                source_health=source_health,
                approval_state=approval_state,
            ):
                with self.assertRaisesRegex(AnswerValidationError, "not eligible"):
                    validate_answer(
                        payload,
                        evidence=[
                            evidence_fixture(
                                "blocked-source",
                                review_state=review_state,
                                source_health=source_health,
                                approval_state=approval_state,
                            )
                        ],
                    )


if __name__ == "__main__":
    unittest.main()
