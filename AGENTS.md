# Working on mineru-like

This is a local, single-user document parser built with Python, FastAPI and uv.
It serves the uploads/jobs protocol used by llm-for-zotero, using managed
llama.cpp or an external inference service. Quality and source fidelity take
priority over latency; deployment targets a 6 GB GPU with useful headroom.

## Principles and requirements

- Preserve compatibility with the unchanged Zotero plugin. Broader MinerU API
  compatibility is a long-term goal, not a claim that every upstream route exists.
- Verify relevant upstream code and model contracts rather than infer behavior
  from names, summaries or successful smoke tests.
- Separate actual implementation, observed test results, estimates and unverified
  behavior. Make uncertainty visible rather than manufacture confidence.
- Discuss new runtime dependencies and dependency replacements before adopting
  them. Routine development tooling may proceed within the agreed task scope.
- Any history rewrite requires explicit permission, including amendments to
  commits created during the current task. Version changes, tag creation or
  movement, and pushes also require explicit instruction.
- Keep local reference assets, credentials and generated document data out of
  commits. Preserve unrelated working-tree changes.

## Working defaults

Use judgment within these principles. Execute clear fixes and routine choices
without another approval round. For substantial behavior or architecture choices,
present the alternatives, consequences and a recommendation with enough context
for the user to decide before implementing that direction.

Fix directly related issues needed for the requested result. Avoid opportunistic
refactors; record concrete unrelated problems separately. This project is young:
clean breaks in its own configuration, CLI and internal interfaces are acceptable
when justified and documented. That latitude does not relax external API
compatibility requirements.

Work on the current branch unless instructed otherwise. Comparison worktrees are
intended once the user considers the implementation stable; do not choose that
transition on their behalf. Make coherent, reviewable Conventional Commits as
work progresses: `type(scope): imperative summary`, using a scope when useful.
Keep behavior and its tests together; explain non-obvious rationale and
validation in the commit body when helpful.

Keep essential instructions accurate during the work. Consolidate documentation,
Unreleased changelog entries and TODO updates at milestones of a few related
commits rather than tying updates only to version numbers.

Report meaningful findings and changes of direction during work. Final reports
should explain the outcome, consequential decisions and rationale, relevant
changes, validation evidence, limitations and follow-up work. Provide an
engineering report proportionate to the change, not a transcript of tool calls.

## Repository map

| Area | Purpose |
| --- | --- |
| [src/AGENTS.md](src/AGENTS.md) | Python style, model contracts and correctness rules |
| [tests/AGENTS.md](tests/AGENTS.md) | Test design and evidence standards |
| [docs/AGENTS.md](docs/AGENTS.md) | Documentation structure and maintenance |
| `src/mineru_like/cli.py`, `api.py` | CLI, HTTP contract and lifecycle entry points |
| `src/mineru_like/worker.py`, `store.py`, `runtime.py` | Durable jobs, state and inference ownership |
| `src/mineru_like/pipeline.py`, `verification.py`, `tables.py`, `native.py` | Extraction, recovery and document adapters |
| `src/mineru_like/processor.py`, `result.py`, `svg_assets.py`, `compression.py`, `fidelity.py` | Delivery, assets and fidelity checks |
| [pyproject.toml](pyproject.toml), [config.example.toml](config.example.toml) | Tool settings, dependencies and configuration examples |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Dependency roles, advisory typing and contributor workflow |
| [docs/TESTING.md](docs/TESTING.md) | Test prerequisites, integration commands and limits |
| [CHANGELOG.md](CHANGELOG.md), [TODO.md](TODO.md) | Recorded changes and explicitly distinguished remaining work |

## Common commands

Run from the repository root. Select checks according to the change's risk;
these commands are references, not a requirement to run every check per commit.

```sh
uv sync --locked --group dev
uv run --locked ruff check .
uv run --locked python scripts/run_tests.py
uv build
```

Use Ruff to format touched Python files, following `pyproject.toml`. See the
testing guide for integrations and the full-suite runner. Keep changing runtime
defaults and validation results in their authoritative files, not in this guide.

Owned Python source, tests and scripts have comprehensive annotations. Preserve
useful contracts when editing; the separately recommended `uv run --locked ty check`
is advisory and does not require a wider typing cleanup on every focused task.
