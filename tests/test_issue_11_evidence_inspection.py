import unittest

from danish_rag.answer_pipeline import validate_answer


def evidence_fixture(
    citation_id: str,
    *,
    source_health: str = "healthy",
    review_state: str = "approved-current",
    agreement_state: str = "supports",
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
        "agreement_state": agreement_state,
        "content": (
            f"Official fixture content for {citation_id}. Prøve i Dansk 2 can support "
            "the language requirement. One official fact is supported by a current "
            "source. Another official fact is supported by an overdue source. A cited "
            "official fact and a second cited official fact are supported."
        ),
    }


class Issue11EvidenceInspectionTests(unittest.TestCase):
    def test_trust_indicators_keep_evidence_confidence_and_freshness_independent(self):
        payload = {
            "summary": "Supported answer.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": "Prøve i Dansk 2 can support the language requirement.",
                    "citation_ids": ["source-a"],
                }
            ],
        }

        current = validate_answer(
            payload,
            evidence=[evidence_fixture("source-a", source_health="healthy")],
        )
        overdue = validate_answer(
            payload,
            evidence=[evidence_fixture("source-a", source_health="overdue-policy-usable")],
        )

        self.assertEqual(current["trust"]["evidence_confidence"], "High")
        self.assertEqual(overdue["trust"]["evidence_confidence"], "High")
        self.assertEqual(current["trust"]["fresh_tomato_score"], "High")
        self.assertEqual(overdue["trust"]["fresh_tomato_score"], "Medium")
        self.assertIn("coverage", overdue["trust"]["evidence_confidence_reason"].casefold())
        self.assertIn("source freshness", overdue["trust"]["fresh_tomato_reason"].casefold())

    def test_conflicting_material_source_lowers_evidence_confidence(self):
        payload = {
            "summary": "Supported answer.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": "Prøve i Dansk 2 can support the language requirement.",
                    "citation_ids": ["source-a"],
                }
            ],
        }

        answer = validate_answer(
            payload,
            evidence=[evidence_fixture("source-a", agreement_state="conflicts")],
        )

        self.assertEqual(answer["trust"]["evidence_confidence"], "Low")
        reason = answer["trust"]["evidence_confidence_reason"].casefold()
        self.assertIn("agreement", reason)
        self.assertIn("conflict", reason)

    def test_answer_fresh_tomato_score_is_lowest_material_source_score(self):
        payload = {
            "summary": "Supported answer with two material sources.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": "One official fact is supported by a current source.",
                    "citation_ids": ["source-a"],
                },
                {
                    "kind": "official_fact",
                    "text": "Another official fact is supported by an overdue source.",
                    "citation_ids": ["source-b"],
                },
            ],
        }

        answer = validate_answer(
            payload,
            evidence=[
                evidence_fixture("source-a", source_health="healthy"),
                evidence_fixture("source-b", source_health="overdue-policy-usable"),
            ],
        )

        self.assertEqual(answer["trust"]["fresh_tomato_score"], "Medium")
        self.assertEqual(
            [source["fresh_tomato_score"] for source in answer["citations"]],
            ["High", "Medium"],
        )
        self.assertEqual(
            answer["citations"][1]["claim_support"][0]["text"],
            payload["sections"][1]["text"],
        )
        self.assertNotIn("review_state", answer["citations"][0])
        self.assertNotIn("source_health", answer["citations"][0])
        self.assertNotIn("source_excerpt", answer["citations"][0])

    def test_changing_evidence_coverage_does_not_increase_source_freshness(self):
        one_claim = {
            "summary": "Supported answer.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": "A cited official fact.",
                    "citation_ids": ["source-a"],
                }
            ],
        }
        two_claims = {
            "summary": "Supported answer.",
            "sections": [
                *one_claim["sections"],
                {
                    "kind": "official_fact",
                    "text": "A second cited official fact.",
                    "citation_ids": ["source-a"],
                },
            ],
        }
        evidence = [evidence_fixture("source-a", source_health="overdue-policy-usable")]

        first = validate_answer(one_claim, evidence=evidence)
        second = validate_answer(two_claims, evidence=evidence)

        self.assertEqual(first["trust"]["fresh_tomato_score"], "Medium")
        self.assertEqual(second["trust"]["fresh_tomato_score"], "Medium")


if __name__ == "__main__":
    unittest.main()
