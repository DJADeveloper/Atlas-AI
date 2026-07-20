# mypy strict-mode allowlist

M01 acceptance requires `mypy --strict` to pass with **zero `# type: ignore`
comments outside this documented allowlist** (`docs/60-milestones.md`, M01).

## Inline `# type: ignore` comments

None. The codebase currently contains zero `# type: ignore` comments.

## Configured relaxations (pyproject `[tool.mypy]` overrides)

None. If a third-party dependency without type information ever forces an
override (e.g. `ignore_missing_imports` for a specific module), it must be
listed here with the module name, the reason, and the upstream issue tracking
its typing support.
