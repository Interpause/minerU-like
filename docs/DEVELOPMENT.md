# Development

See [content rendering contracts](CONTENT_RENDERING.md) for semantic annotations, running furniture, table projections and image-link lifecycle.

Install the locked development dependencies and run the usual local checks from the repository root:

```sh
uv sync --locked --group dev
uv run --locked ruff check .
uv run --locked python scripts/run_tests.py
uv build
```

The [testing guide](TESTING.md) covers integration prerequisites, focused runs, the complete no-skips runner and what each check establishes. A normal pytest run can skip unavailable integrations.

Run the separately recommended advisory type check with:

```sh
uv run --locked ty check
```

The development dependencies include ty 0.0.97 in the lockfile. Maintain comprehensive annotations across `src`, `tests` and `scripts`, sharing protocols for cross-module contracts and using local `Any` escapes where appropriate. The check is advisory; there is no strict gate or diagnostic quota. The package includes `py.typed` for consumers.

Model and third-party contracts require source verification; see [source guidance](../src/AGENTS.md), [test guidance](../tests/AGENTS.md), and [third-party notices](../THIRD_PARTY_NOTICES.md). Keep credentials, model assets and generated documents out of commits.

## Dependencies and fixtures

Runtime dependencies provide the API/server (FastAPI and uvicorn), HTTP (httpx2), settings validation (Pydantic), document conversion (DocVortex), imaging (Pillow and NumPy) and PDF/SVG handling. [Architecture](ARCHITECTURE.md#pdf-and-svg-dependencies) explains the distinct PDF and SVG roles. The lockfile records exact resolved dependencies; discuss runtime dependency additions or replacements before adopting them.

The dev group provides pytest and pytest-asyncio, Ruff, ty, and fixture generators. `python-docx` supplies the `docx` import, `pypptx-with-oxml` supplies the `pptx` namespace for generated slide fixtures, `openpyxl` supplies spreadsheet fixtures, and ReportLab supplies PDF fixtures (it is also a DocVortex runtime dependency). Do not install a separate `python-pptx` into the same namespace as a routine fixture fix. Node packages and host tools for selected integrations are separate prerequisites described in [testing](TESTING.md).

## Typing and dynamic boundaries

`[tool.ty.environment]` selects Python 3.12 and `[tool.ty.src]` includes `src`, `tests` and `scripts`. The command above reports diagnostics independently of the test suite. Shared protocols in [contracts.py](../src/mineru_like/contracts.py) describe parser, inference, engine and result-writer interactions. Use a precise type or a shared protocol when the interface is known; use a local `Any`, cast, or narrowly scoped suppression when external model records, dynamic SQLite projections, library APIs or test doubles cannot be described usefully. Keep the escape close to that boundary and do not make passing ty or a particular diagnostic count a release gate.

## Build and review

Choose checks for the affected behavior. Documentation-only edits usually need diff and link review; a small isolated code fix usually needs focused tests and lint for the touched Python. Broaden testing for changes across components, persistence or lifecycle behavior. Distribution builds are unnecessary for ordinary documentation, test-only and isolated implementation edits unless they affect packaging. Run them for dependency/build metadata, package layout/data, entry-point changes or release preparation. During a larger task, normally defer the build and artifact inspection to the final integrated pass; repeat earlier only to investigate a packaging issue, or later if build inputs change.

`uv build` creates a source distribution and wheel under `dist/`; unlike `uv run --locked`, the build command may resolve its isolated build requirements. After building, inspect the specific resulting wheel (replace the placeholder with its filename):

```sh
python -m zipfile -l dist/mineru_like-VERSION-py3-none-any.whl
```

Check for `mineru_like/py.typed`, the bundled task prompt text files, `mineru_like/licenses/`, and `mineru_like/THIRD_PARTY_NOTICES.md` as well as the package modules. A listing checks package contents, not installation or runtime behavior. Before committing, review the diff and select tests according to the affected behavior; keep behavior and its tests together and preserve unrelated work. The [testing guide](TESTING.md) describes stronger integration checks and their limits.

The migration leaves three targeted suppression sites: two `svgelements` imports whose runtime exports are missing from its published type surface, and DocVortex's suffix argument after the adapter validates its accepted format. PDF geometry, heterogeneous model metadata, partial SQLite projections and deliberately malformed test inputs use local casts or dynamic aliases. No owned directory is excluded and no diagnostic rule is disabled globally. See the [ty configuration reference](https://docs.astral.sh/ty/reference/configuration/) for configuration and [suppression guidance](https://docs.astral.sh/ty/suppression/) before adding an escape.
