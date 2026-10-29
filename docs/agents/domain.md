# Domain Docs

How the engineering skills should consume this repository's domain documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY.md`** at the repository root.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.

If either path doesn't exist, proceed silently. The `/domain-modeling` skill creates domain documentation lazily when terms or decisions are resolved.

## File structure

This is a single-context repository:

```text
/
├── GLOSSARY.md
├── docs/adr/
│   └── NNNN-short-decision-title.md
└── custom_components/keenetic_control/
```

## Use the glossary's vocabulary

When output names a domain concept—such as in an issue title, refactor proposal, hypothesis, or test name—use the term defined in `GLOSSARY.md`. Avoid synonyms that the glossary explicitly rejects.

If the needed concept isn't in the glossary, reconsider whether the term belongs to the project or note the gap for `/domain-modeling`.

## Flag ADR conflicts

If output contradicts an existing ADR, surface the conflict explicitly instead of silently overriding the decision.
