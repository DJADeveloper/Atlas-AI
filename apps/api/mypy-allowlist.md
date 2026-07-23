# mypy strict-mode allowlist

M01 acceptance requires `mypy --strict` to pass with **zero `# type: ignore`
comments outside this documented allowlist** (`docs/60-milestones.md`, M01).

## Inline `# type: ignore` comments

None. The codebase currently contains zero `# type: ignore` comments.

## Configured relaxations (pyproject `[tool.mypy]` overrides)

| Module | Relaxation | Reason | Upstream |
|---|---|---|---|
| `testcontainers.*` | `ignore_missing_imports` | The `testcontainers` distribution ships no `py.typed` marker, so strict mode rejects the import (`import-untyped`). Used only by `tests/integration/`; `src/atlas` never imports it. | testcontainers/testcontainers-python — py.typed marker not yet published; re-check on each dependency bump. |
| `atlas.infrastructure.parsing.pdf_parser` | `disallow_untyped_calls = false` | PyMuPDF ships partial annotations; calling `pymupdf.open`/page methods trips `no-untyped-call` at our call sites. Scoped to the single adapter touching pymupdf. | pymupdf — typing coverage incomplete; re-check on each dependency bump. |
| `tests.unit.test_parsers` | `disallow_untyped_calls = false`; `disable_error_code = attr-defined, no-any-return` | The PDF fixture helper uses `Document.tobytes` and `PDF_ENCRYPT_AES_256`, both missing from pymupdf's stubs despite existing at runtime. Test-fixture module only. | pymupdf — stubs incomplete for tobytes/encryption constants. |

Any new entry requires: module name, the exact relaxation, why it is
unavoidable, and the upstream issue or condition under which it gets removed.
