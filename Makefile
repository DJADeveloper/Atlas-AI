# Atlas developer entry points. Every gate here is exactly what CI runs —
# both delegate to scripts/ci/*.sh (docs/53-cicd-strategy.md, pipeline parity).

.PHONY: install dev lint typecheck test test-integration compose-validate ci-local up down

install: ## Install Python and JS toolchains
	cd apps/api && uv sync
	pnpm install --frozen-lockfile

dev: ## Run the API locally with reload (expects compose core services up)
	cd apps/api && uv run uvicorn --factory atlas.presentation.app:create_app --reload

lint:
	scripts/ci/lint.sh

typecheck:
	scripts/ci/typecheck.sh

test:
	scripts/ci/test.sh

test-integration:
	scripts/ci/test-integration.sh

compose-validate:
	scripts/ci/compose-validate.sh

ci-local: lint typecheck test compose-validate ## The full fast gate, as CI runs it

up: ## Start the dev stack
	docker compose -f infra/compose/docker-compose.yml --profile core up -d

down: ## Stop the dev stack
	docker compose -f infra/compose/docker-compose.yml --profile core down

db-upgrade: ## Apply migrations to head
	cd apps/api && uv run alembic upgrade head

db-downgrade: ## Revert the most recent migration
	cd apps/api && uv run alembic downgrade -1

db-reset: ## Return to empty and re-migrate from scratch
	cd apps/api && uv run alembic downgrade base && uv run alembic upgrade head

db-revision: ## Autogenerate a migration (hand-review required; docs/11 §5). Usage: make db-revision m="add xyz"
	cd apps/api && uv run alembic revision --autogenerate -m "$(m)"
