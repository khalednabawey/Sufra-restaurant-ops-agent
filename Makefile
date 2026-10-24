.PHONY: setup raw data seed test lint db-up db-down db-reset db-shell mcp inspect tokens

# legacy-peer-deps works around an npm 11.2 install bug ("Cannot read properties of null (reading 'edgesOut')")
INSPECTOR = npm_config_legacy_peer_deps=true npx -y @modelcontextprotocol/inspector@2.10.1 \
	--config mcp-inspector.json --server sufra

setup:      ## install Python dependencies into .venv
	uv sync

raw:        ## re-create the committed extracts in data/raw/ (not needed for a normal build)
	uv run python data/fetch_sales.py
	uv run python data/fetch_usda.py

data:       ## download weather and reviews, then build every table (seed 42)
	uv run python data/download.py
	uv run python data/build.py --seed 42

seed:       ## load the build into PostgreSQL and embed the knowledge base
	uv run python data/seed.py
	uv run python -m mcp_server.kb ingest

test:
	uv run pytest

lint:
	uv run ruff check .

db-up:      ## start PostgreSQL + pgvector and wait until healthy
	docker compose up -d --wait db

db-down:
	docker compose down

db-reset:   ## drop all data and start an empty database
	docker compose down -v
	docker compose up -d --wait db

db-shell:
	docker compose exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

mcp:        ## run the MCP server over stdio
	uv run python -m mcp_server.server

inspect:    ## open the MCP inspector on the server (paste a token from `make tokens`)
	$(INSPECTOR)

tokens:     ## print a signed token for each demo user
	uv run python -m mcp_server.demo_users
