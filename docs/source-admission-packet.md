# Source-Admission Packet

This maintainer command prepares the current five `discovered` sources for a
real curator decision. It does not fetch source pages, alter the source
registry, record a human decision, or make any source production eligible.

Write the private packet outside the repository:

```bash
.venv/bin/python -B -m danish_rag.source_admission \
  --repo-root . \
  --registry data/source_registry/sr-2026-07-06.1.json \
  --output /tmp/danish-rag-source-admission-packet.json \
  --generated-at-utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

The command validates the registry against its active fixture release, binds
the registry's exact SHA-256 digest and source identity fields, and writes the
packet with mode `0600`. It fails if the output resolves inside the repository,
the registry drifts from the release, or any source has already left
`discovered` state. With no `--observations` argument, the packet contains an
explicitly empty `machine_observed_discrepancies` array.

## Optional Machine Observations

Machine observations are review prompts, not provenance, monitoring, curation,
or human-review evidence. When useful, pass a JSON array through
`--observations /path/to/observations.json`. Each item must have exactly these
fields:

```json
[
  {
    "source_id": "nyidanmark-permanent-residence-language-requirements",
    "code": "publisher-attribution-differs",
    "summary": "A machine observation for the curator to verify.",
    "registry_value": "SIRI",
    "observed_value": "Udlændingestyrelsen",
    "reference_url": "https://www.nyidanmark.dk/da/Du-vil-ans%C3%B8ge/Permanent-ophold/Permanent-ophold"
  }
]
```

The reference URL must use HTTPS on the source's official domain. Every
included observation is marked `unverified-machine-observation`, requires
curator verification, and explicitly does not count as curation evidence.

The generated packet leaves curator IDs, decision, admission timestamp, scope
rationale, and monitoring-owner IDs blank. A human maintainer must review and
complete those fields through the governed source-registry workflow; editing
the packet alone does not approve a source.
