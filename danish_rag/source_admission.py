"""Prepare private, blank source-admission packets for human curators."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .source_registry import (
    SourceRegistryError,
    load_source_registry,
    validate_source_registry_against_release,
)


SOURCE_ADMISSION_PACKET_SCHEMA_VERSION = "source-admission-packet-v1"
DEFAULT_REGISTRY_PATH = Path("data/source_registry/sr-2026-07-06.1.json")
_OBSERVATION_CODE_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class SourceAdmissionPacketError(ValueError):
    """Raised when a safe source-admission packet cannot be produced."""


def write_source_admission_packet(
    *,
    repo_root: str | Path,
    registry_path: str | Path,
    output_path: str | Path,
    generated_at_utc: str,
    machine_observations: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Write a private packet containing evidence and blank curator fields only."""

    root = Path(repo_root).resolve()
    destination = Path(output_path).expanduser().resolve()
    try:
        destination.relative_to(root)
    except ValueError:
        pass
    else:
        raise SourceAdmissionPacketError(
            "source-admission packet must be written outside the repository"
        )
    source_path = Path(registry_path).resolve()
    try:
        relative_registry_path = source_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise SourceAdmissionPacketError(
            "source registry must be inside the repository"
        ) from exc

    _parse_utc(generated_at_utc)
    try:
        registry_bytes = source_path.read_bytes()
    except OSError as exc:
        raise SourceAdmissionPacketError(
            f"could not read source registry: {source_path}"
        ) from exc
    try:
        registry = load_source_registry(source_path)
    except SourceRegistryError as exc:
        raise SourceAdmissionPacketError("source registry is invalid") from exc
    sources = list(registry["sources"])
    non_discovered = [
        str(source["source_id"])
        for source in sources
        if source["registry_state"] != "discovered"
    ]
    if non_discovered:
        raise SourceAdmissionPacketError(
            "all sources must be discovered before source-admission packet generation; "
            f"non-discovered source(s): {', '.join(non_discovered)}"
        )
    release_dir = (
        root
        / "data"
        / "knowledge_releases"
        / str(registry["knowledge_release_id"])
    )
    try:
        validate_source_registry_against_release(registry, release_dir)
    except SourceRegistryError as exc:
        raise SourceAdmissionPacketError(
            "source registry does not match its active knowledge release"
        ) from exc
    source_by_id = {str(source["source_id"]): source for source in sources}
    observations = _validated_observations(machine_observations, source_by_id)

    packet_sources = []
    for source in sources:
        packet_sources.append(
            {
                "source_id": source["source_id"],
                "registry_record": {
                    "source_id": source["source_id"],
                    "official_url": source["official_url"],
                    "publisher": source["publisher"],
                    "topic": source["topic"],
                    "language": source["language"],
                    "registry_state": source["registry_state"],
                },
                "admission_status": "awaiting-human-curator",
                "production_release_eligible": False,
                "blank_curator_admission": {
                    "decision": None,
                    "curator_ids": [],
                    "admitted_at_utc": None,
                    "scope_rationale": None,
                    "monitoring_owner_ids": [],
                },
            }
        )

    packet = {
        "schema_version": SOURCE_ADMISSION_PACKET_SCHEMA_VERSION,
        "classification": "sensitive-local-only",
        "commit_policy": "do-not-commit",
        "contains_human_decisions": False,
        "contains_official_source_snapshots": False,
        "network_fetch_performed": False,
        "production_release_eligible": False,
        "qualification_status": "blocked-pending-human-source-admission",
        "generated_at_utc": generated_at_utc,
        "registry": {
            "artifact_scope": registry["artifact_scope"],
            "knowledge_release_id": registry["knowledge_release_id"],
            "path": relative_registry_path,
            "sha256": hashlib.sha256(registry_bytes).hexdigest(),
            "source_registry_version": registry["source_registry_version"],
        },
        "source_count": len(packet_sources),
        "sources": packet_sources,
        "machine_observed_discrepancies": observations,
    }
    _write_private_json(destination, packet)
    return packet


def _validated_observations(
    observations: Iterable[dict[str, Any]],
    source_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    required = {
        "source_id",
        "code",
        "summary",
        "registry_value",
        "observed_value",
        "reference_url",
    }
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict) or set(observation) != required:
            raise SourceAdmissionPacketError(
                f"machine observation {index} has an invalid shape"
            )
        source_id = str(observation["source_id"])
        source = source_by_id.get(source_id)
        if source is None:
            raise SourceAdmissionPacketError(
                f"machine observation {index} references an unknown source"
            )
        code = observation["code"]
        if not isinstance(code, str) or _OBSERVATION_CODE_PATTERN.fullmatch(code) is None:
            raise SourceAdmissionPacketError(
                f"machine observation {index} has an invalid code"
            )
        for field in ("summary", "registry_value", "observed_value", "reference_url"):
            if not isinstance(observation[field], str) or not observation[field].strip():
                raise SourceAdmissionPacketError(
                    f"machine observation {index} has an invalid {field}"
                )
        if not _same_official_domain(
            str(source["official_url"]), str(observation["reference_url"])
        ):
            raise SourceAdmissionPacketError(
                f"machine observation {index} reference URL is outside the official domain"
            )
        validated.append(
            {
                **observation,
                "classification": "unverified-machine-observation",
                "requires_curator_verification": True,
                "counts_as_curation_evidence": False,
            }
        )
    return validated


def _same_official_domain(expected_url: str, observed_url: str) -> bool:
    expected = urlparse(expected_url)
    observed = urlparse(observed_url)
    if observed.scheme != "https" or observed.username or observed.password:
        return False
    if observed.port not in {None, 443}:
        return False

    def normalized_host(value: str | None) -> str:
        host = (value or "").casefold().rstrip(".")
        return host.removeprefix("www.")

    return bool(normalized_host(expected.hostname)) and normalized_host(
        expected.hostname
    ) == normalized_host(observed.hostname)


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SourceAdmissionPacketError("generated_at_utc must be a UTC timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceAdmissionPacketError(
            "generated_at_utc must be a UTC timestamp"
        ) from exc


def _write_private_json(destination: Path, payload: dict[str, Any]) -> None:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    temporary_path: Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.chmod(0o600)
        temporary_path.replace(destination)
        destination.chmod(0o600)
    except OSError as exc:
        raise SourceAdmissionPacketError(
            f"could not write source-admission packet: {destination}"
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Write a private, blank human source-admission packet without "
            "fetching the network or changing source eligibility."
        )
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root used to bind and validate the current registry.",
    )
    parser.add_argument(
        "--registry",
        default=str(DEFAULT_REGISTRY_PATH),
        help=(
            "Current discovered-source registry path, absolute or relative to "
            "--repo-root."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output JSON path; it must resolve outside the repository.",
    )
    parser.add_argument(
        "--generated-at-utc",
        required=True,
        help="Actual packet-generation time as an ISO-8601 UTC timestamp ending in Z.",
    )
    parser.add_argument(
        "--observations",
        help=(
            "Optional JSON array of unverified machine observations. Omit it to "
            "write an explicitly empty discrepancy section. Supplied observations "
            "never count as curation evidence."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the source-admission packet CLI."""

    args = _argument_parser().parse_args(argv)
    root = Path(args.repo_root).resolve()
    registry_path = Path(args.registry)
    if not registry_path.is_absolute():
        registry_path = root / registry_path
    try:
        observations = _load_observations(args.observations)
        write_source_admission_packet(
            repo_root=root,
            registry_path=registry_path,
            output_path=args.output,
            generated_at_utc=args.generated_at_utc,
            machine_observations=observations,
        )
    except SourceAdmissionPacketError as exc:
        print(f"source-admission packet error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote blank source-admission packet to {Path(args.output).resolve()}")
    return 0


def _load_observations(path: str | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except OSError as exc:
        raise SourceAdmissionPacketError(
            f"could not read machine observations: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise SourceAdmissionPacketError(
            f"machine observations are not valid JSON: {path}"
        ) from exc
    if not isinstance(payload, list):
        raise SourceAdmissionPacketError("machine observations must be a JSON array")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
