# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, if it exists.
- **`CONTEXT-MAP.md`** at the repo root, if it exists. It points at one `CONTEXT.md` per context. Read each one relevant to the topic.
- **`docs/adr/`**, if it exists. Read ADRs that touch the area you're about to work in. In multi-context repos, also check `src/<context>/docs/adr/` for context-scoped decisions.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The producer skill (`/grill-with-docs`) creates them lazily when terms or decisions actually get resolved.

## File structure

Domain docs are optional for now. If the repo later adopts a single-context layout, use:

```text
/
|-- CONTEXT.md
|-- docs/adr/
|   |-- 0001-example-decision.md
|   `-- 0002-example-decision.md
`-- src/
```

If the repo later adopts a multi-context layout, use:

```text
/
|-- CONTEXT-MAP.md
|-- docs/adr/
`-- src/
    |-- ordering/
    |   |-- CONTEXT.md
    |   `-- docs/adr/
    `-- billing/
        |-- CONTEXT.md
        `-- docs/adr/
```

## Use the glossary's vocabulary

When your output names a domain concept in an issue title, a refactor proposal, a hypothesis, or a test name, use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use, or there's a real gap to note for `/grill-with-docs`.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> Contradicts ADR-0007 (event-sourced orders), but worth reopening because...
