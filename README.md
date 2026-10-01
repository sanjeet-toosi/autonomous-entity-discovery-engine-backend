# business-graph-backend

Control plane + execution engine for the **Autonomous Business Graph Agent**: give it a
natural-language objective ("Find all restaurants in the US") and it continuously discovers
business websites, understands them, classifies them into a growing taxonomy, writes a
knowledge graph, and learns how to discover more efficiently.

```
Control Plane (FastAPI)  ──  Redis (control flags · rate limits · event bus)  ──  Workers
        │                                                                         │
   PostgreSQL  ◄── system of record: missions, runs, frontier, graph, brain, events, metrics
        │
     Neo4j     ◄── graph projection (BELONGS_TO / SUBCATEGORY_OF / LINKS_TO) for traversal
```

## Architecture (six systems, strictly separated)

| System | Modules | Responsibility |
|---|---|---|
| Control plane | `missions/`, `runs/`, `api/` | mission interpretation, run state machine (pause/resume/stop with checkpoints), REST + WebSocket |
| Discovery frontier | `frontier/` | PostgreSQL-authoritative candidate store; `UNIQUE(mission, domain)`; lease-based claiming with `FOR UPDATE SKIP LOCKED`; retry/backoff; priority = weighted relevance/probability/source quality/novelty/gain |
| Execution plane | `discovery/`, `crawler/`, `intelligence/`, `workers/` | search bootstrapping, governed HTTP crawling (robots, per-domain + global rate limits, SSRF guard, size/redirect limits), page parsing, business detection + extraction + classification, entity resolution, recursive link discovery |
| Knowledge graph | `graph/` | the only writer of business/category/edge/evidence rows; every mutation emits a `change_event`; Neo4j mirror |
| Brain | `brain/`, `evaluation/` | semantic + procedural memory, episodic experiences, source intelligence, failure memory, versioned strategies, reflection → proposed lessons → DRAFT strategy candidates → offline evaluation. **Promotion is explicit, never automatic.** |
| Observability | `events/`, `metrics/`, `stats/` | structured activity events (PostgreSQL + Redis pub/sub → WebSocket), hourly pre-aggregated metrics, worker heartbeats |

The **model layer** (`llm/`) exposes tasks (`plan_mission`, `extract_business`, `reflect`) and
routes them to Anthropic, OpenAI or Ollama with a deterministic heuristic fallback, rate limits,
a daily spend cap, and per-call cost tracking (`model_invocations`).

## Run it locally

Prerequisites: Python 3.12+, `uv`, PostgreSQL, Redis, optionally Neo4j and Ollama.

```bash
cp .env.example .env            # set DATABASE_URL / NEO4J_PASSWORD / model keys
uv sync
uv run alembic upgrade head

# terminal 1 – control plane
uv run uvicorn app.main:app --port 8010 --reload

# terminal 2 – workers (one of each type for development)
uv run python -m app.workers.run all --count 1 --concurrency 4
# production shape: separate processes
uv run python -m app.workers.run crawler --count 4 --concurrency 8
uv run python -m app.workers.run discovery
uv run python -m app.workers.run reflection
```

Create and start a mission:

```bash
curl -X POST localhost:8010/api/v1/missions -H 'content-type: application/json' \
  -d '{"prompt":"Find all restaurants in the US","start":true}'
```

Then open the frontend (`business-graph-frontend`) or watch `ws://localhost:8010/ws/global`.

## Model provider selection

`LLM_PROVIDER=auto` picks Anthropic → OpenAI → Ollama depending on which credentials exist.
Every task falls back to the heuristic provider on error, refusal or spend cap, so a run never
stalls on a model. Post-model invariants (`intelligence/sanitize.py`) keep the taxonomy well-formed
even with small local models.

## API surface

`/api/v1/missions`, `/runs` (`pause|resume|stop|continue`), `/stats/overview?range=24h`,
`/graph/root|categories/{id}/children|businesses`, `/businesses/{id}/{evidence,history,relationships,pages,activity,discoveries}`,
`/categories`, `/brain/{overview,lessons,strategies,sources,failures,experiments,memories,reflect}`,
`/activity`, `/system/{health,workers,queues,errors,config}`, WebSocket `/ws/runs/{id}` and `/ws/global`.
Interactive docs at `/docs`.

## Run lifecycle

```
DRAFT → STARTING → RUNNING ⇄ PAUSING → PAUSED → RESUMING → RUNNING
                     └────→ STOPPING → STOPPED   (COMPLETED / FAILED terminal)
```
Workers stop *claiming* the instant a run leaves `RUNNING` (Redis control flag); the orchestrator
waits for in-flight leases to drain, writes a checkpoint (frontier counts, search cursor, strategy
version), then completes the transition. `POST /runs/{id}/continue` creates a new run from a
stopped run's checkpoint.

## Safety

Every fetch resolves DNS first and refuses loopback / private / link-local / metadata addresses;
only `http(s)` is allowed; a domain blocklist skips social networks, builders and aggregators;
robots.txt is honoured; response size, redirect count and timeouts are bounded.

## Tests

```bash
uv run pytest
```
