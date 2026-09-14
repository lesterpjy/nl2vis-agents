# Data Agents

Two agents that answer a question about data and draw the answer.

The **Analysis Agent** answers one natural-language question about one registered SQLite database with
read-only SQL, returning a typed result: an answer, one question back, or an abstention. The
**Visualization Agent** turns that result into a *Chart Spec*, and a deterministic renderer builds Vega-Lite
from it in a fixed house style. No model writes plotting code, and no generated code runs.

It ships as a FastAPI service with a JSON API and a server-rendered GUI. Slides land in [`docs/`](docs/).

## Install

Needs [uv](https://docs.astral.sh/uv/) and Python >= 3.12.

```bash
uv sync
uv run python scripts/fetch_databases.py     # the three demo databases, once
cp .env.example .env                         # then add your OPENAI_API_KEY
uv run uvicorn data_agents.web.service:app   # http://127.0.0.1:8000
```

Sign in with `alice-demo-token`. Run every command from the repository root: registry URLs are relative.

`registry/data/` is not in the repository, so the fetch step comes before the tests as well as the app; it
checks every download against a pinned SHA-256. Seeded users: `alice` (sakila, chinook), `bob`
(northwind_small), `admin` (everything, by role).

## How it works

**A Turn** is one question, one answer, one row in the store. `orchestrator.run_turn` is plain code
(authorize, route, analyse, gate, chart, persist), and every step is an event on one stream the GUI, the API
and the admin drawer all read.

**Authorization** is `grants ∩ live Registry`, recomputed per request, so unregistering a database revokes it
everywhere with no restart. Users, grants, the Registry, Sessions, Turns and an audit trail live in one
SQLite file seeded from `registry/*.yaml`.

**The SQL path is three layers**, each able to refuse alone.

1. **The guard** parses with sqlglot and admits one `SELECT`, with a `LIMIT` injected if absent. A parse
   failure rejects: it fails closed.
2. **The handle** is a `mode=ro` connection with `query_only` set, extension loading off, a row cap and a
   timeout. The Analysis Agent holds one and never names a database.
3. **Eight checks on the final SQL**, each a one-edit rewrite of the parse tree (the *twin*) run through the
   same handle and compared to the original: an outer join made inner, `COUNT` swapped for `COUNT(DISTINCT)`,
   a bare column counted per group, a fan-out measured, an integer division cast to real, a day bound moved
   on, a `!=` filter made to keep NULLs, a text sort cast to a number. Agreement costs one execution and says
   nothing; disagreement goes back to the model as two numbers in plain words. No model judges any of it.
   [`tests/fixtures/sql-checks.md`](tests/fixtures/sql-checks.md) demonstrates all eight and is their test
   data.

**The chart is typed, not generated.** The Visualization Agent picks columns, form and title, never colours
or sizes. `form.py` decides what a data type or a cardinality decides (mark, orientation, sort, the room bars
need), `presentation.py` what the numbers say (unit, magnitude, ticks, label fit), `renderer.py` composes
both into schema-validated Vega-Lite. One invariant holds throughout: a chart has a label channel and a
measure channel, the label channel carries at least two distinct values, and no column plays two channels. A
chart that still cannot be read is not shipped; the Turn keeps the table and says why.

A Turn stores its **Chart Spec, not an image**, so reopening a Session is a read. A renderer change stops its
fingerprint matching and the chart is built again from the spec, never re-derived by a model.

## Usage

The GUI exchanges your token for a Sign-in held in an HttpOnly cookie, so the credential never reaches page
code. Pick a database, ask, follow up; the page streams the Turn and draws the chart with vega-embed. Admins
get a drawer for the Registry, grants and the audit trail. Browser libraries are served from `/static`, so
the GUI needs no network.

The same operations over HTTP, token as a bearer header, Turn events as server-sent events:

```bash
TOKEN=alice-demo-token; API=http://127.0.0.1:8000
curl -s $API/databases -H "Authorization: Bearer $TOKEN"      # grants ∩ live Registry

SID=$(curl -s -X POST $API/sessions -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      -d '{"database": "sakila"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

curl -N -X POST $API/sessions/$SID/turns -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question": "How much revenue does each film category bring in?", "chart": true}'

# chart data you supply, with no Analysis Agent call: your own SQL through the same guard, or a CSV
curl -N -X POST $API/charts -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"database": "sakila", "intent": "distribution",
       "sql": "SELECT rating, COUNT(*) AS films FROM film GROUP BY rating"}'
```

Also `GET /me`, `GET /sessions`, `DELETE /sessions/{id}` and `/admin/*`. A follow-up that only asks to see
the same table differently ("show it as a pie") is routed to the chart side and costs no analysis call.
Optional `hints` (`chart_type`, `title`, `x`, `y`, `sort`) outrank the question's own words.

## Adding a database

Drop a SQLite file into `registry/data/` and the drawer registers it in one click, or:

```bash
ADMIN=admin-demo-token
curl -s -X POST $API/admin/databases -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"name": "orders", "url": "sqlite:///registry/data/orders.db", "description": "What it holds."}'
```

With no `document`, the database is introspected and a **Schema Document** drafted by one model call; given
one, it is used as it is. Either way a structural round-trip check runs first: every table, column, value and
example in the document must exist in the database and vice versa, or the mismatch is named and nothing is
written.

## Configuration

Read from the environment; `.env` is the only place a secret belongs. Full list in `.env.example`.

| variable | | default |
|---|---|---|
| `OPENAI_API_KEY` | the provider key | **required** |
| `DATA_AGENTS_TOKEN_<NAME>` | one per user, name upper-cased | none; no token, no sign-in |
| `ANALYSIS_MODEL` | | `openai:gpt-4.1` |
| `VISUALIZATION_MODEL`, `ROUTER_MODEL`, `SUGGESTION_MODEL` | | `openai:gpt-4.1-mini` |
| `SCHEMA_MODEL` | drafting a Schema Document, once per database | `openai:gpt-5.5` |
| `SCHEMA_VARIANT` | `lean` drops views and empty tables: equal on the eval set at 29% fewer input tokens | `lean` |
| `MODEL_CALLS_PER_MINUTE` / `TURNS_IN_FLIGHT` | pacing per user / per process; past either, 429 | `20` / `4` |
| `LOGFIRE_TOKEN` | absent means no traces are sent, and nothing fails | none |
| `LOGFIRE_SEND_TO_LOGFIRE` | `false` stops spans leaving the machine whatever credentials exist | unset |

## Tests

```bash
uv run pytest                          # no API key: model responses replay from tests/evals/recordings/
uv run pytest --live                   # calls the model and re-records the tapes
uv run python -m tests.evals.runner    # the eval table, replayed
```

727 tests, 711 of which run from a plain clone; the rest need the benchmark downloads under `.bench/`. They
are deterministic rules table-driven with no model, the GUI and service driven by `FunctionModel`s, and
twelve end-to-end cases replayed from tapes. `fetch_databases.py` must have run first.

## Benchmark

Our own tests show a regression and nothing else. `tests/bench/` scores the system against
[VisEval](https://arxiv.org/abs/2407.00981) (IEEE VIS 2024): 1,150 questions over 146 unseen databases, each
with a ground-truth chart and a gold query that runs on 984 of them. It sits behind an adapter seam, so
another dataset is three functions (`cases`, `register`, `score`) over `tests/bench/case.py`.

Split once by committed case id (`tests/bench/dev.txt`, 250 cases): a **dev** slice fixes are found on, and a
held-out remainder run rarely and never tuned against.

```bash
uv run python -m tests.bench.runner --dataset viseval --split dev --dry          # free: no model
uv run python -m tests.bench.runner --dataset viseval --split dev                # live, about a cent a case
uv run python -m tests.bench.runner --dataset viseval --split test-250 --final   # the committed held-out draw
uv run python -m tests.bench.rescore .bench/results.json --dataset viseval       # re-score, no model call
```

A Turn ending in a Clarification is an unanswered question, and the **first pass** scores it as one. A
simulated user then answers, given the question, the analyst's question back and what the database is about,
never the gold; that answer is scored beside the first, never in its place.

The rules are published ones: `ex` is BIRD's evaluator verbatim, `correct` the same after projecting the
agent's columns onto the gold's, and `chart`, `data` and `order` are VisEval's own checks ported verbatim
(MIT), fed from the Vega-Lite we build. `reads` is ours.

### Held out: the committed 250-case draw of the held-out 900

| condition | `ex` | `correct` | `chart` | `data` | `order` | `reads` | asked |
|---|---|---|---|---|---|---|---|
| first pass | 57% | 77% | 83% | 73% | 75% | 88% | 26/250 |
| after the reply | 61% | 81% | 90% | 80% | 82% | 98% | |

Both live held-out runs were spent on this one committed draw, so 650 of the 900 have never been run live.
`ex` and `correct` are over the 209 cases whose gold executes, `order` over the 115 that ask for one. An
earlier run on the identical 250, before the eight checks existed and asking nothing, scored 62 / 82 / 92 /
82 / 85 / 99; paired case by case the two differ on 13 of 209 for `correct`. The checks buy a question the
user can answer, at the price of asking on 10% of cases.

Caveats that belong beside those numbers:

* VisEval's CSVs carry no keys, so `tables.infer_keys` recovers 85% of the gold's join conditions from the
  data. Paired over 100 cases they moved `correct` by nothing.
* `chart` is VisEval's own name match, so the pies the House Style refuses above three parts count as misses.
* 148 cases bin their x axis, which is not SQL, so they ship no runnable gold and are scored on `data` alone.
* A `--dry` sweep charts the ground truth's own table with its own channels (91 / 99 / 99 over all 1,150).
  Those are ceilings on the renderer, not scores for the system, and the sweep has seen every held-out table,
  so a dry run over a test split is not a held-out number either. `--split all` is a superset of dev.
* No number of the paper's is quoted or compared against: different populations, different databases, and
  *invalid* means something else for a system that hands over a typed spec.

Over the held-out 250 a Turn takes a median 4.4 s and 3 model round trips, and costs under a cent. Provider
round trips are 90% of that; the guard and its eight twins together cost 6 ms.

## HTTPS

So the browser treats the page as secure and the cookie carries `Secure`:

```bash
brew install mkcert && mkcert -install     # once; trusts a local CA
mkdir -p certs
mkcert -cert-file certs/cert.pem -key-file certs/key.pem localhost 127.0.0.1 ::1
uv run uvicorn data_agents.web.service:app --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem
```

`certs/` is git-ignored. Behind a TLS-terminating proxy, add `--proxy-headers`.

## Before pointing this at real data

* **Row contents reach the model as text**: the Schema Document carries stored spellings and example values,
  and results go to the Visualization Agent as sample rows. That is what makes value linking and chart choice
  work, and it means the data is not private from the provider.
* **A Logfire token sends traces off the machine**, SQL and rows included. With no token nothing is sent;
  `LOGFIRE_SEND_TO_LOGFIRE=false` turns it off outright.

## Licence

MIT, see [LICENSE](LICENSE). The vendored browser libraries keep their own, recorded in
[`VENDORED.md`](data_agents/web/static/VENDORED.md); `tests/bench/viseval_checks.py` is Microsoft's VisEval
code, MIT, licensed in the file.
