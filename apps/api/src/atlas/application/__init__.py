"""Application layer: use cases and orchestration (spine §5).

Depends only on the domain layer's ports and entities; owns transaction
boundaries via the Unit-of-Work port. First use cases land at M03/M04.
The import-linter layer contract enforces the dependency direction.
"""
