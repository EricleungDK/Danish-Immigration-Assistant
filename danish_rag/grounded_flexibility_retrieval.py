"""Content-free retrieval evidence for the installed grounded-flexibility corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .evidence_integrity import sha256_file, utc_now_seconds
from .grounded_flexibility_evaluation import load_grounded_flexibility_dataset
from .knowledge_release import default_data_dir
from .retrieval import HybridRetriever
from .source_freshness import assess_source_freshness


DEFAULT_DATASET = "data/evaluation/grounded-flexibility-v0.1-candidate.json"


def evaluate_retrieval(dataset_path: str | Path, *, retriever: Any) -> dict[str, Any]:
    """Measure required source coverage at three without exporting query/results text.

    Dataset identities describe source documents; schema-2 results describe chunks.
    Only eligible chunks receive source-document credit. Exceptions are counted,
    never serialized, since provider exceptions may contain private input.
    """
    dataset = load_grounded_flexibility_dataset(dataset_path)
    timestamp = utc_now_seconds()
    intents: dict[str, dict[str, int]] = {}
    errors = blocked = 0
    for case in dataset["cases"]:
        try:
            results = retriever.retrieve(case["prompt"], limit=3)
        except Exception:
            errors += 1
            results = []
        eligible = set()
        for result in results:
            if assess_source_freshness(result, evaluated_at_utc=timestamp).answer_eligible:
                eligible.add(result.get("source_document_id", result["document_id"]))
            else:
                blocked += 1
        for intent in case["expected_intents"]:
            counts = intents.setdefault(intent["intent_id"], {"required": 0, "covered": 0})
            required = set(intent["required_document_ids"])
            counts["required"] += len(required)
            counts["covered"] += len(required & eligible)
    threshold = dataset["thresholds"]["per_intent_evidence_coverage_min"]
    by_intent = {
        key: {
            **counts,
            "observed": counts["covered"] / counts["required"],
            "threshold": threshold,
            "status": "passed" if counts["covered"] / counts["required"] >= threshold else "failed",
        }
        for key, counts in sorted(intents.items())
    }
    failures = [key for key, value in by_intent.items() if value["status"] != "passed"]
    if blocked:
        failures.append("blocked_source_violations")
    if errors:
        failures.append("retrieval_execution_errors")
    return {
        "schema_version": "grounded-flexibility-retrieval-v1",
        "generated_at_utc": timestamp,
        "dataset_sha256": sha256_file(dataset_path),
        "case_count": len(dataset["cases"]),
        "retrieval_limit": 3,
        "by_intent": by_intent,
        "blocked_source_violations": blocked,
        "execution_error_count": errors,
        "threshold_failures": failures,
        "strict_passed": bool(by_intent) and not failures,
        "semantic_qualification": "not_evaluated",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--data-dir", default=default_data_dir())
    parser.add_argument("--trust-root-path", required=True)
    parser.add_argument("--embedding-endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    retriever = HybridRetriever.from_data_dir(
        args.data_dir, trust_root_path=args.trust_root_path,
        embedding_endpoint=args.embedding_endpoint,
    )
    report = evaluate_retrieval(args.dataset, retriever=retriever)
    report["provenance"] = {
        "manifest_sha256": sha256_file(retriever.active_release["manifest_path"]),
        "dense_index_sha256": sha256_file(retriever.index_dir / "dense-index.json"),
        "index_metadata": retriever.dense_index["metadata"],
    }
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if report["strict_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
