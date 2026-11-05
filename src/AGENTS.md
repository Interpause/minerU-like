# Source guidance

This file supplements the [root guidance](../AGENTS.md) for `src/`.

## Python and interfaces

- Favor cohesive modules, descriptive names, explicit contracts and focused
  helpers. Extract code for clarity or reuse, not to build a speculative framework.
- Maintain comprehensive annotations in source, tests and scripts. Use shared
  protocols for cross-module contracts and local `Any` escapes where necessary;
  the advisory type checker is not a strict gate or a diagnostic quota. Follow the Python constraints and Ruff settings in
  [pyproject.toml](../pyproject.toml).
- Prefer minimal comments. Explain surprising constraints when names and structure
  cannot carry the reasoning; avoid narrating obvious code or duplicating types.
- Use distinct error categories at boundaries. Keep malformed model output,
  transcription uncertainty, invalid input and infrastructure failure separate.
  Catch exceptions where a real recovery or translation is possible; do not hide
  backend errors behind successful-looking fallback output.
- Keep blocking rendering and substantial CPU work off the event loop. Preserve
  task ownership, cancellation propagation and cleanup; one canceled request must
  not terminate a healthy shared inference service or another job's work.

## Model contracts and evidence

The bundled task prompts, model examples, both demo wrappers and technical report
must be compared when investigating model behavior. Shared task contracts are the
baseline; wrapper choices need their own justification. Explain contradictions
and omissions rather than treating one wrapper as universal authority.

- Preserve documented task formats. Do not demand an invented universal JSON
  response or assume capabilities the examples/report do not establish.
- Keep upstream task templates unchanged unless a prompt change is explicitly
  justified, reviewed against source evidence and tested. Preserve attribution
  and [third-party notices](../THIRD_PARTY_NOTICES.md).
- Retain raw output, finish reason and recovery provenance before transformations.
  Cleanup cannot turn truncation into completeness. Parse valid structured output
  before attempting repair; keep recovered content explicitly distinguished.
- Submit unrelated page/crop tasks independently. Multiple images within a task
  need support from its documented contract, such as related-page reconstruction.
- Check executable/API capability instead of rejecting an arbitrary runtime build
  number. Keep current defaults and configuration semantics in the settings code
  and [example configuration](../config.example.toml), not duplicated here.

## Document correctness

- PDF text is supporting evidence, not authoritative reading order or semantics.
  Read PDF equations visually even for digital-born files; never trust a scanned
  document's existing OCR layer as transcription evidence.
- Preserve native equation structure only where the source format and adapter
  support it. Object counts and successful conversion do not establish fidelity.
- Prefer localized recovery supported by better evidence. Preserve useful content,
  source images and labeled uncertain transcriptions when quality checks remain
  unresolved; do not discard a document for a minor discrepancy.
- Preserve page order, source provenance, non-table content and original fragments
  through table reconstruction. Unsupported or uncertain merges must not destroy
  independent results.
- Preserve source evidence through asset transformations. Measure compression
  savings and fidelity; do not assume re-encoding will improve or worsen an asset.
  Update Markdown, HTML and structured references together.
- Let the inference backend own request queuing and slot capacity. CPU resource
  controls are a separate concern; do not silently add a job-count admission cap
  or inference serialization as a performance fix.

Consult [parsing guide](../docs/PARSING.md) for current parsing policy and
[TODO.md](../TODO.md) for known limitations. Keep guidance outside
`src/mineru_like/` so agent instructions are not shipped in the Python package.
