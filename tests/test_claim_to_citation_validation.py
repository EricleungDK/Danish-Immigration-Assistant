import unittest

from danish_rag.answer_pipeline import AnswerValidationError, validate_answer


def evidence(content: str) -> dict[str, object]:
    return {
        "citation_id": "official-source",
        "document_id": "official-source",
        "source_id": "official-source",
        "title": "Permanent residence language requirements",
        "publisher": "SIRI",
        "official_url": "https://www.nyidanmark.dk/permanent-residence",
        "checked_at_utc": "2026-07-13T00:00:00Z",
        "knowledge_release_id": "kr-test",
        "corpus_identity": "kr-test",
        "review_state": "approved-current",
        "source_health": "healthy",
        "content": content,
    }


def payload(claim: str) -> dict[str, object]:
    return {
        "summary": "An evidence-bounded answer.",
        "sections": [
            {
                "kind": "official_fact",
                "text": claim,
                "citation_ids": ["official-source"],
            }
        ],
    }


class ClaimToCitationValidationTests(unittest.TestCase):
    def test_supported_paraphrase_passes(self) -> None:
        result = validate_answer(
            payload(
                "Prøve i Dansk 2, or a Danish exam at an equivalent or higher "
                "level, can meet the basic language requirement for permanent residence."
            ),
            evidence=[
                evidence(
                    "For permanent residence, the applicant must pass Danish language "
                    "test 2 (Prøve i Dansk 2), or a Danish exam of an equivalent or "
                    "higher level, to satisfy the basic Danish-language requirement."
                )
            ],
        )

        self.assertEqual(result["trust"]["evidence_confidence"], "High")

    def test_irrelevant_citation_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            AnswerValidationError,
            "not supported by its cited evidence",
        ):
            validate_answer(
                payload("Prøve i Dansk 2 costs exactly 10,000 DKK in 2026."),
                evidence=[
                    evidence(
                        "Prøve i Dansk 2 consists of a written part and an oral part."
                    )
                ],
            )

    def test_number_must_appear_in_cited_evidence(self) -> None:
        with self.assertRaisesRegex(
            AnswerValidationError,
            "not supported by its cited evidence",
        ):
            validate_answer(
                payload("The registration deadline is 30 August 2026."),
                evidence=[
                    evidence("The registration deadline is 31 August 2026.")
                ],
            )


if __name__ == "__main__":
    unittest.main()
