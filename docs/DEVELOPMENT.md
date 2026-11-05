# Development

Install the locked development dependencies and run the usual local checks from the repository root:

```sh
uv sync --locked --group dev
uv run --locked ruff check .
uv run --locked pytest -q
uv build
```

The [testing guide](TESTING.md) covers integration prerequisites, focused runs, the complete no-skips runner and what each check establishes. A normal pytest run can skip unavailable integrations.

Run the separately recommended advisory type check with:

```sh
uv run --locked ty check
```

The development dependencies include ty 0.0.97 in the lockfile. Maintain comprehensive annotations across `src`, `tests` and `scripts`, sharing protocols for cross-module contracts and using local `Any` escapes where appropriate. The check is advisory; there is no strict gate or diagnostic quota. The package includes `py.typed` for consumers.

Model and third-party contracts require source verification; see [source guidance](../src/AGENTS.md), [test guidance](../tests/AGENTS.md), and [third-party notices](../THIRD_PARTY_NOTICES.md). Keep credentials, model assets and generated documents out of commits.
