# Test guidance

This file supplements the [root guidance](../AGENTS.md) for `tests/`.
Setup, prerequisites and copyable commands belong in
[the testing guide](../docs/TESTING.md), not duplicated here.

Preserve useful test annotations, using shared protocols where useful and local
escapes for malformed fixtures or dynamic doubles. A focused task does not require
wider annotation cleanup. The separate advisory type check and its scope are
described in [development](../docs/DEVELOPMENT.md).

## Design meaningful checks

- Choose verification according to the risk and reach of the change. Focused tests,
  broader suites, lint and builds serve different purposes; a full suite is not
  required for every commit.
- Test observable behavior and failure modes rather than mirror implementation
  details. Prefer clear fixtures and assertions over layers of test abstractions.
- Add regressions for actual defects. Preserve existing coverage when contracts
  change; do not delete scenarios or weaken assertions simply to make tests pass.
- Prefer generated fixtures where practical. Clearly distinguish tests of real
  adapters from tests that mock extraction or inject a controlled parser.
- Exercise lifecycle, cancellation, ordering and isolation when changing async
  work. Use synchronization to establish overlap or ordering instead of relying
  on arbitrary sleeps for the central assertion.
- Keep destructive or capability experiments isolated and reversible. Verify the
  requested result before cleanup and verify cleanup afterward.

## Report what the evidence establishes

A mocked plugin lifecycle proves protocol handling, not model quality. A live CPU
smoke does not prove CUDA behavior or GPU memory headroom. Generated documents do
not establish fidelity across every real file or format.

- Distinguish automated regression tests, actual backend inference, host-tool
  integrations and representative deployment/corpus checks in reports.
- Record skipped and unavailable checks with reasons. Never present estimates,
  successful loading or a narrow smoke as measured general correctness.
- When a check must run elsewhere, provide its exact command, prerequisites,
  expected result and evidence to inspect. Do not block otherwise complete
  implementation solely on unavailable deployment access.
- Explicitly requested full-suite runs must report missing prerequisites rather
  than silently succeed with skipped integrations. Use the existing runner's
  strict mode described in the testing guide.
- Keep private documents, model weights and generated evidence out of tracked
  fixtures. Do not claim general model capability from a controlled response.
