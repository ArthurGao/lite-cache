@AGENTS.md

# Claude Code notes

- At the start of a session: read the status line and §11 of `docs/lite-cache-design.md`, then
  `kb/README.md`.
- Stage close-out is AGENTS.md Workflow step 4: `/simplify` through the Skill tool, then the Codex
  review command there (foreground, it waits). Do not start the next stage before it is done.
- Relevant user-level skills: `llm-app-architecture` (roles, provenance, no fallback chain),
  `rag-design` (the lite-rag integration, S4), `llm-model-eval`, `testing-and-ci`, `kb-maintain`.
