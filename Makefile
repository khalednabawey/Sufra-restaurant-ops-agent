.PHONY: setup raw data seed test lint db-up db-down db-reset db-shell mcp inspect tokens api ui-setup ui eval eval-smoke

# legacy-peer-deps works around an npm 11.2 install bug ("Cannot read properties of null (reading 'edgesOut')")
INSPECTOR = npm_config_legacy_peer_deps=true npx -y @modelcontextprotocol/inspector@2.10.1 \
	--config mcp-inspector.json --server sufra

setup:      ## install Python dependencies into .venv (and the UI's own .venv)
	uv sync
	cd ui && uv sync

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
	uv run ruff check . ui/app.py

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

api:        ## run the agent API on :8000 (Postgres checkpointer; CHECKPOINTER=memory to skip it)
	uv run python -m api --port 8000

ui-setup:   ## install the chat UI's separate environment (Chainlit pins mcp<2)
	cd ui && uv sync

ui:         ## run the Chainlit chat UI on :8501 (needs `make api` running)
	cd ui && uv run chainlit run app.py --port 8501

PROVIDER ?= groq
eval:       ## full eval suite on one provider: make eval PROVIDER=groq|openrouter|router
	uv run python -m evals.run_evals --provider $(PROVIDER) --delay 2

eval-smoke: ## the 20-case smoke set CI runs on every push
	uv run python -m evals.run_evals --provider $(PROVIDER) --smoke --delay 2
