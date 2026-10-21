.PHONY: db migrate seed embed serve test lint
db:
	docker compose up -d db
migrate:
	uv run alembic upgrade head
seed:
	uv run mro-rag ingest --source "$${MRO_SOURCE_DIR:-data/source/airbus-mcp-data-v1}"
embed:
	uv run --extra embedding mro-rag embed --release "$(RELEASE)"
serve:
	uv run --extra embedding mro-rag serve
test:
	uv run --extra dev pytest
lint:
	uv run --extra dev ruff check src tests scripts
