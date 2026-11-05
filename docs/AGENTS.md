# Documentation guidance

This file supplements the [root guidance](../AGENTS.md) for `docs/`.

## Structure and style

- Use layered documentation. Keep README focused on purpose, features and quick
  start; link to detailed configuration, parsing policy, architecture, testing and
  troubleshooting material as those references are developed.
- Add task-based guides when they help a reader accomplish something. Link to
  shared references rather than repeat configuration definitions and commands
  across guides; duplicated instructions become stale easily.
- Lead with practical behavior and the information a reader needs to act. Use
  plain language, concrete examples and headings/tables where they aid navigation.
- Keep examples runnable and aligned with actual CLI/configuration behavior.
  Identify placeholders and prerequisites; do not imply assets or tools are
  bundled when they must be supplied separately.
- Keep machine-specific paths, private reference locations, credentials and
  document contents out of published examples.

## Maintenance and evidence

Consolidate documentation, Unreleased changelog and TODO updates every few
related commits at a meaningful milestone. This cadence is independent of version
numbers. Correct misleading operational instructions immediately rather than
leave readers with a known broken command until the next milestone.

- Document the final behavior and important tradeoffs, not the conversation or
  every abandoned implementation. Include history only when it explains a current
  constraint or migration.
- Keep one authoritative description for changing values. Link to the settings,
  example configuration or testing guide instead of scattering defaults and test
  counts through instruction files.
- Separate implemented capabilities, concrete missing features, remaining
  validation and explicitly deferred ideas. Do not describe an optional idea as
  promised work or use incomplete testing as proof of missing implementation.
- State the scope of verification. Preserve the distinction between mocked,
  generated-fixture, actual inference and deployment results.
- When CLI/configuration changes break this project's own interfaces, update the
  affected examples and explain what users must change. External API compatibility
  remains a separate requirement.
