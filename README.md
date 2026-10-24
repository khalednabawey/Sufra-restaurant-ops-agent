# Arabic Restaurant Ops Agent

An Arabic and English operations assistant for **Sufra Café & Bakery (سُفرة)**, a fictional four-branch chain built on public data. Owners, area managers and branch managers ask about sales, stock, waste and staff in their own language. Each person sees only the data their role allows, and nothing changes until a human approves it.

- **Access control lives in the tools, not the prompt.** Every MCP tool verifies the caller's signed token and filters SQL rows by their role and branches, so a prompt injection can't widen access.
- **No invented numbers.** Every tool result carries a `query_summary`, and the agent's verify step (week 2) checks each number in an answer against the tool results.
- **Evaluated on every push** (week 3). An eval suite fails the build if accuracy drops or data leaks across roles.

> **Status: week 1 done** (data, database, MCP server with 11 read tools, knowledge base, tests). Next: the LangGraph agent, write tools with human approval, and the chat UI.

## Run it locally

Needs [uv](https://docs.astral.sh/uv/), Docker and, for the inspector, Node.js.

```bash
cp .env.example .env
make setup      # Python 3.12 + dependencies into .venv
make db-up      # PostgreSQL 17 + pgvector on localhost:5433
make data       # download weather and reviews, build every table (seed 42)
make seed       # load PostgreSQL, embed the knowledge base (first run downloads BGE-M3, ~2.3 GB)
make test
```

The embedding model loads in a few minutes on CPU the first time a process uses it; searches then take well under a second. Once it's downloaded, `HF_HUB_OFFLINE=1` skips the Hugging Face update check.

No `make` (e.g. Windows without Git Bash tools)? Each target is a one-line `uv run ...` command in the [Makefile](Makefile).

## The MCP server

```bash
make tokens     # print a signed token for each demo user
make inspect    # open the MCP inspector on the server
```

In the inspector, call a tool and paste a token into `caller_token`. The demo users are an owner, the Riyadh area manager (B1 and B4), one branch manager per branch, and an Olaya barista.

| Tool | What it returns | Roles |
|---|---|---|
| `branch_sales` | Revenue, orders, average ticket by hour, day, week, month or category | managers |
| `top_items` | Items ranked by quantity, revenue or margin | managers |
| `item_margin` | Price, recipe cost, margin, and cost changes over time | managers |
| `low_stock_items` | Items below N days of cover | managers |
| `waste_report` | Waste cost and share of stock used, by ingredient, reason or week | managers |
| `labor_cost_ratio` | Labour cost ÷ sales, hours, overtime | managers |
| `staff_schedule` | Shifts and coverage gaps (staff see only their own shifts) | everyone |
| `compare_branches` | Branches ranked on one metric, partial periods flagged | managers |
| `reviews_summary` | Review count, positive share, sample quotes | managers |
| `search_sop` | SOPs, FDA extracts and the menu, filtered by role before ranking | everyone |
| `pending_actions_list` | Actions waiting for the caller's approval, and their own requests | managers |

"Managers" means owner, area manager and branch manager, each limited to their own branches. A refusal comes back as an error that starts with `not_permitted:` and never says whether the data exists.

## Repository

```text
data/         sources, mappings, build.py, seed.py, schema.sql, checks.sql, SOPs  → data/README.md
mcp_server/   server.py, scope.py (tokens + role checks), tools/, kb.py (knowledge-base ingest)
tests/        scope and token rules, per-tool scope filters, the MCP server end to end, planted facts
agent/ api/ ui/ evals/ deploy/   weeks 2–4
```

## Stack

Python 3.12 · PostgreSQL 17 + pgvector 0.8.7 · MCP Python SDK 2.3 · sentence-transformers 6.2 (BGE-M3) · FastAPI · LangGraph · Groq with OpenRouter fallback · Langfuse · Chainlit · Docker · GitHub Actions

## Data

Sales are adapted from a public-domain dataset; weather, holidays, ingredients and reviews come from open sources. Inventory, waste and staffing are derived by documented rules, and five facts are planted for the evals. See [data/README.md](data/README.md) for every source, licence and rule.

```text
Data sources: Sales transactions adapted from Maven Analytics "Coffee Shop Sales" (Public Domain).
Ingredient names and units from USDA FoodData Central (CC0). Weather data by Open-Meteo.com (CC BY 4.0).
Arabic reviews from ElSahar & El-Beltagy (2015), "Building Large Arabic Multi-domain Resources for Sentiment
Analysis", loaded from Hugging Face. Food-safety extracts from the FDA Food Code 2022. Inventory, waste and
staffing are derived from the sales data using the rules in data/README.md.
```
