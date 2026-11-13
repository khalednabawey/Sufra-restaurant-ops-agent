# Arabic Restaurant Ops Agent

An Arabic and English operations assistant for **Sufra Café & Bakery (سُفرة)**, a fictional four-branch chain built on public data. Owners, area managers and branch managers ask about sales, stock, waste and staff in their own language. Each person sees only the data their role allows, and nothing changes until a human approves it.

- **Access control lives in the tools, not the prompt.** Every MCP tool verifies the caller's signed token and filters SQL rows by their role and branches, so a prompt injection can't widen access.
- **No invented numbers.** Every tool result carries a `query_summary`, and the agent's verify step checks each number in an answer against the tool results. A draft with an unsourced figure is rewritten once; if it still has one, the answer says so.
- **Nothing changes without people.** Reorders and price changes only create pending requests: the requester confirms first, then the approver the SOPs name (area manager up to SAR 5,000, otherwise the owner) approves or rejects. The model is never given the approval tool.
- **Evaluated on every push** (week 3). An eval suite fails the build if accuracy drops or data leaks across roles.

> **Status: week 2 done.** Data, database, MCP server (11 read tools, 2 write tools, approvals), the LangGraph agent with Groq → OpenRouter fallback, the API and the Chainlit UI. Next (week 3): the eval suite, Langfuse tracing and CI.

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

Then put your `GROQ_API_KEY` and `OPENROUTER_API_KEY` in `.env` and start the agent:

```bash
make api        # FastAPI on http://localhost:8000 (docs at /docs)
make ui         # Chainlit on http://localhost:8501 — pick a user from the menu at the top
```

The UI has its own environment (`ui/pyproject.toml`) because Chainlit pins `mcp<2` and the server uses the MCP 2.x SDK; it talks to the API over HTTP like any other client. On Windows, start the API with `make api` (or `uv run python -m api`), not plain `uvicorn`: psycopg's async driver needs a selector event loop there.

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
| `request_reorder` | Creates a pending reorder with its amount and approver (nothing is ordered) | area and branch managers |
| `propose_price_change` | Creates a pending price change; needs a written reason | owner, area manager |
| `decide_action` | Approve or reject; called by the API on an approver's click, never offered to the model | owner, area manager |

"Managers" means owner, area manager and branch manager, each limited to their own branches. A refusal comes back as an error that starts with `not_permitted:` and never says whether the data exists.

## The agent

```text
START → guard ─(injection)→ refuse
          └→ agent ⇄ tools        model picks MCP tools; write tools pause for the user's Confirm
               └→ verify → END    every number in the draft must appear in a tool result
```

- **Scope travels outside the model.** The API puts the user's signed token in the run config; the tool adapter adds it to each MCP call and strips `caller_token` from the schemas the model sees. The model is shown only the tools its role may call, and the server checks again.
- **Model router.** Every model call goes through Groq (`openai/gpt-oss-120b`) with OpenRouter (`nvidia/nemotron-3.5-super-vl:free`) as the fallback. Groq doesn't retry: on its free tier a rate limit asks the client to wait 10–30 s, and answering from the fallback is faster. Each answer reports which provider answered.
- **Memory.** A Postgres checkpointer keeps each conversation; threads are named `<user_id>:<id>` and a user can only open their own. The model sees the last 30 messages.
- **Two human steps for writes.** `request_reorder` and `propose_price_change` trigger a LangGraph interrupt; the UI shows Confirm / Cancel. A confirmed request is a `pending_actions` row routed per the SOPs. Only `POST /actions/{id}/decision` from the routed approver changes its status, and nobody approves their own reorder.

| Endpoint | What it does |
|---|---|
| `POST /auth/demo-login` | Signed token for a demo user |
| `POST /chat` | Ask a question (`thread_id` optional); returns the answer, provider, tools used and any pending requests, or `needs_confirmation` |
| `POST /chat/confirm` | Confirm or cancel the request the agent proposed |
| `GET /actions` | Requests waiting for you, and your own |
| `POST /actions/{id}/decision` | Approve or reject (approvers only) |

## Repository

```text
data/         sources, mappings, build.py, seed.py, schema.sql, checks.sql, SOPs  → data/README.md
mcp_server/   server.py, scope.py (tokens + role checks), tools/, kb.py (knowledge-base ingest)
agent/        graph.py (LangGraph), llm.py (Groq → OpenRouter), tools.py (MCP adapter), verify.py, prompts.py
api/          main.py (FastAPI), __main__.py (launcher)
ui/           app.py (Chainlit), its own pyproject.toml
tests/        scope and token rules, per-tool scope filters, write tools and approvals, the agent graph
              with a scripted model, fallback and number-check units, planted facts
evals/ deploy/   weeks 3–4
```

## Stack

Python 3.12 · PostgreSQL 17 + pgvector 0.8.7 · MCP Python SDK 2.3 · sentence-transformers 6.2 (BGE-M3) · LangGraph 1.2 + langgraph-checkpoint-postgres 3.2 · langchain-groq 1.1 / langchain-openai 1.7 · FastAPI 0.162 · Chainlit 2.12 · Docker · Langfuse and GitHub Actions in week 3

## Data

Sales are adapted from a public-domain dataset; weather, holidays, ingredients and reviews come from open sources. Inventory, waste and staffing are derived by documented rules, and five facts are planted for the evals. See [data/README.md](data/README.md) for every source, licence and rule.

```text
Data sources: Sales transactions adapted from Maven Analytics "Coffee Shop Sales" (Public Domain).
Ingredient names and units from USDA FoodData Central (CC0). Weather data by Open-Meteo.com (CC BY 4.0).
Arabic reviews from ElSahar & El-Beltagy (2015), "Building Large Arabic Multi-domain Resources for Sentiment
Analysis", loaded from Hugging Face. Food-safety extracts from the FDA Food Code 2022. Inventory, waste and
staffing are derived from the sales data using the rules in data/README.md.
```
