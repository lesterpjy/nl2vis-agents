# Data Agents

Two cooperating agents that answer a question about data and draw the answer.

The **Analysis Agent** takes one natural-language question about one registered SQLite database and answers it
with read-only SQL: it reads a Schema Document, writes a SELECT, runs it through a guard, and returns a typed
result — an answer, a single question back, or an abstention when the database holds nothing the question needs.
The **Visualization Agent** takes that result and returns a *Chart Spec*: which columns play which channel, what
form the chart takes, what it is called. A deterministic renderer turns the spec into Vega-Lite in a fixed house
style. No model writes plotting code, and no generated code is executed.

It runs as a FastAPI service with a JSON API and a server-rendered browser GUI, and it is scored against two
open benchmarks neither agent has seen.

The slides that go with it land in [`docs/`](docs/).

---

## Install

Prerequisites: [uv](https://docs.astral.sh/uv/) and Python ≥ 3.12.

```bash
uv sync
uv run python scripts/fetch_databases.py     # the three demo databases; needs the network, once
cp .env.example .env                         # then put your OPENAI_API_KEY in it
uv run uvicorn data_agents.web.service:app   # http://127.0.0.1:8000
```

Sign in with `alice-demo-token`, pick a database, ask a question.

`registry/data/` is not in the repository, so **`fetch_databases.py` comes before the tests as well as before the
app** — the suite queries the same three files. It verifies every download against a pinned SHA-256 and refuses
to install a file that does not match; a second run is a no-op.

**Run every command from the repository root.** The registry URLs and the System Store are relative paths.

The three seeded users are `alice` (sakila, chinook), `bob` (northwind_small) and `admin` (every registered
database, by role). A token is how a user signs in, and tokens live in `.env` only — the store holds users and
grants but never a secret.

## How it works

**A Turn** is the unit of work: one question, one answer, one row in the store. `orchestrator.run_turn` is plain
code — authorize, route, analyse, gate, chart, persist, emit — and every step it takes is an event on one stream
that the GUI, the API and the admin panel all read.

**The Registry and grants.** A Database is registered by name with a URL and a Schema Document. A User holds
grants. What a user may actually use is *grants ∩ live Registry*, computed on every request, so unregistering a
database revokes it everywhere without a restart. An admin holds every registered database by role. Users,
grants, the Registry, Sessions, Turns and an audit trail live in one SQLite file, `registry/system.db`, seeded
from `registry/databases.yaml` and `registry/users.yaml`: edit a seed file and the store follows on the next
request; edit the store and the file stays what it was.

**The read-only SQL path**, in layers, each of which can refuse on its own:

1. **The guard** (`data/sql_guard.py`) parses the statement with sqlglot and admits exactly one `SELECT` or set
   operation, with a `LIMIT` injected when none is given. A parse failure rejects — it fails closed.
2. **The handle** (`data/database.py`) is a connection opened `mode=ro` with `query_only` set and extension
   loading off, wrapped in a row cap and a statement timeout. The Analysis Agent is handed one handle by
   dependency injection and never names a database; nothing downstream can widen what it holds.
3. **Eight mechanical checks on the final SQL** (`agents/sql_checks.py`), each a one-edit rewrite of the parse
   tree — the *twin* — executed through the same handle and compared to the original. An outer join made inner,
   a `COUNT` swapped with `COUNT(DISTINCT)`, a bare column's distinct values counted per group, a fan-out
   measured, an integer division cast to real, a day bound moved to the next day, a `!=` filter made to keep
   NULLs, a text sort cast to a number. Agreement says nothing and costs one execution; disagreement goes back
   to the model once or twice, as two numbers in plain words, and the model fixes the query, keeps it and says
   why, or asks the user. No model judges any of it. `tests/fixtures/sql-checks.md` demonstrates all eight
   against the three databases, and the test suite asserts that every query in it fires or stays silent as it
   claims, that the twin printed is the twin the code runs, and that the retry printed is the retry the model
   receives.

**The chart is not model-generated code.** The Visualization Agent returns a typed `ChartSpec` and never sees
colours, fonts or sizes. `charts/form.py` decides what a data type or a cardinality decides — the mark, the
orientation, the sort, the room the bars need — and `charts/presentation.py` decides what the numbers say: the
unit a column name implies, the magnitude to draw at, the tick format, whether labels fit. `charts/renderer.py`
composes both into Vega-Lite validated against the schema by Altair. One invariant is enforced in code
throughout (`charts/chartable.py`): *a chart has a label channel and a measure channel; the label channel
carries at least two distinct values; one column never plays two channels*. A chart that still cannot be read
is not shipped — the Turn keeps the table and says, in one fixed sentence, why there is no picture.

**A Turn keeps its Chart Spec, not an image.** Reopening a Session is a read: every client draws the same
Vega-Lite the store kept. When the renderer changes, its fingerprint no longer matches and the chart is built
again from the spec — history restyled deliberately, never re-derived by a model.

## Usage

### The browser

`/` signs in — the token is exchanged for a Sign-in whose id lives in an HttpOnly cookie for eight hours, so the
credential never reaches page code. `/workspace` lists the databases you hold and your Sessions; opening one
starts a Session, and switching database starts a new one, because history built on one schema is the wrong
context for another. The session page says what the database can answer about before you ask, suggests a
question to start with and a follow-up after each answer, streams the Turn's progress as it runs, and draws the
chart in the browser with vega-embed, so it has hover and tooltips. An admin also gets a drawer: register a
database found on disk in one click, unregister, grant and revoke per user, and read the audit trail.

The browser libraries are served from `/static`, so the GUI works with no network and no CDN.

### The HTTP API

Every route takes the token as a bearer header. Turn events arrive as server-sent events.

```bash
TOKEN=alice-demo-token
API=http://127.0.0.1:8000

curl -s $API/me         -H "Authorization: Bearer $TOKEN"   # who you are and what you hold
curl -s $API/databases  -H "Authorization: Bearer $TOKEN"   # grants ∩ live Registry

# start a Session, then ask
SID=$(curl -s -X POST $API/sessions -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      -d '{"database": "sakila"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

curl -N -X POST $API/sessions/$SID/turns -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question": "How much revenue does each film category bring in?", "chart": true}'

# follow up in the same Session; a message that only asks to see the same table differently is
# routed to the chart side and costs no analysis call
curl -N -X POST $API/sessions/$SID/turns -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question": "show it as a pie", "chart": true}'

curl -s $API/sessions -H "Authorization: Bearer $TOKEN"                       # your Sessions
curl -s -X DELETE $API/sessions/$SID -H "Authorization: Bearer $TOKEN"        # and everything it wrote
```

`POST /charts` charts data you supply, with no Analysis Agent call — your own SQL through the same guard, or a
CSV:

```bash
curl -N -X POST $API/charts -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"database": "sakila", "intent": "distribution",
       "sql": "SELECT rating, COUNT(*) AS films FROM film GROUP BY rating"}'

curl -N -X POST $API/charts -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"csv": "month,rain\n2024-01,80\n2024-02,61\n", "name": "rain.csv", "intent": "trend"}'
```

`hints` on either route (`chart_type`, `title`, `x`, `y`, `sort`) are the user's explicit choices and outrank the
question's words. The same preferences work in the question itself — "…as a scatter", "…in alphabetical order" —
because the Visualization Agent is given the question alongside the result.

The admin routes need the admin role:

```bash
ADMIN=admin-demo-token
curl -s -X POST $API/admin/grants -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"user": "bob", "database": "sakila"}'
curl -s -X DELETE $API/admin/grants/bob/sakila -H "Authorization: Bearer $ADMIN"
curl -s -X POST $API/admin/users  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"name": "carol", "role": "analyst", "grants": ["chinook"]}'
curl -s "$API/admin/audit?limit=20" -H "Authorization: Bearer $ADMIN"
```

A new user still needs `DATA_AGENTS_TOKEN_CAROL` in `.env` before they can sign in.

## Adding a database

Drop a SQLite file into `registry/data/` and the admin drawer offers it; one click registers it. Or:

```bash
curl -s -X POST $API/admin/databases -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"name": "orders", "url": "sqlite:///registry/data/orders.db", "description": "What it holds."}'
```

Given no `document`, the database is introspected — tables, columns, keys, row counts, the stored spellings of
small text columns, three example values per column — and a **Schema Document** is drafted by one model call,
held to the template in `data_agents/data/schema_style.md`. Given one, that document is used as it is and no
model is called.

Either way the **structural round-trip check** runs before anything is written: every table and column in the
document exists in the database and vice versa, and every value and example it lists is stored exactly as
written. A mismatch is refused with the mismatch named, and nothing is written. Registering under a name that
could walk out of the registry directory is refused before the name touches the filesystem.

## Configuration

Everything is read from the environment, and `.env` is the only place a secret belongs.

| variable | what it does | default |
|---|---|---|
| `OPENAI_API_KEY` | the provider key pydantic-ai uses | **required** |
| `DATA_AGENTS_TOKEN_<NAME>` | one token per user, the name upper-cased | none; a user without one cannot sign in |
| `ANALYSIS_MODEL` | the Analysis Agent | `openai:gpt-4.1` |
| `VISUALIZATION_MODEL` | the Visualization Agent | `openai:gpt-4.1-mini` |
| `ROUTER_MODEL` | the follow-up router | `openai:gpt-4.1-mini` |
| `SUGGESTION_MODEL` | the question in the box | `openai:gpt-4.1-mini` |
| `SCHEMA_MODEL` | drafting a Schema Document: once per database, offline | `openai:gpt-5.5` |
| `SIMULATED_USER_MODEL` | the benchmark's simulated user | `openai:gpt-4.1-mini` |
| `SCHEMA_VARIANT` | `full`, `no_examples` or `lean`; `lean` drops views and empty tables and measured equal on the eval set at 29% fewer input tokens | `lean` |
| `MODEL_CALLS_PER_MINUTE` | pacing, per user | `20` |
| `TURNS_IN_FLIGHT` | pacing, per process | `4` |
| `REGISTRY_DIR` | the databases, Schema Documents and System Store | `registry` |
| `BENCH_DIR` | the benchmark's downloads, registry and run files | `.bench` |
| `LOGFIRE_TOKEN` | sends traces to Logfire. Absent means no traces are sent, and nothing fails | none |
| `LOGFIRE_SEND_TO_LOGFIRE` | `false` stops spans leaving the machine whatever credentials exist; unset means Logfire's own `if-token-present` | unset |
| `VISUALIZATION_SEES_QUESTION` | `0` withholds the question from the Visualization Agent — the benchmark's `--blind` ablation | unset |

Past either pacing limit the service answers 429 with `Retry-After`. An analyst reads a plain sentence when a
Turn fails; an admin also reads the exception.

## Tests

```bash
uv run pytest                                # no API key needed: model responses replay from tests/evals/recordings/
uv run python -m tests.evals.runner          # the eval table, replayed
uv run pytest --live                         # calls the model and re-records the tapes
uv run python -m tests.evals.runner --live   # one live eval run, with fresh tokens, latency and cost
```

**727 tests, of which 711 run from a plain clone**; the other 16 need the benchmark datasets downloaded and skip
until they are. They are three tiers: deterministic rules table-driven with no model (the guard, the eight
checks, the chart invariant, the shape grid, the presentation rules, the store, authorization); the GUI and the
service driven by `FunctionModel`s; and twelve end-to-end cases replayed from recorded tapes, so a run needs no
key and cannot reach the network.

`scripts/fetch_databases.py` must have run first — the suite queries the three real databases.

## Benchmarks

The tests above are ours, so they show a regression and nothing else. `tests/bench/` scores the system against
open datasets neither agent has seen, behind one seam: a `Case` (question, database, gold SQL and its rows where
the dataset ships one, the dataset's own gold in `extra`), one adapter module per dataset, and one runner that
knows no dataset.

- **[VisEval](https://arxiv.org/abs/2407.00981)** (IEEE VIS 2024): 1,150 questions over 146 databases, each with
  the ground-truth chart type, channels, sort and values, and a gold query that runs on 984 of them.
- **[nvBench 2.0](https://arxiv.org/abs/2503.12880)** (NeurIPS 2025): 1,501 questions in the authors' dev and
  test splits, each over one table and written to be ambiguous, so its gold is a set of two to five valid charts
  and no query at all.

**Which set a number came from is part of the number.** Each dataset is split once, by committed case id
(`tests/bench/dev.txt`, 250 of VisEval; `tests/bench/nvbench2-dev.txt`, 100 of nvBench 2.0), into a **dev** slice
fixes are found on and a **test** remainder that is run rarely and never tuned against.

```bash
uv run python -m tests.bench.runner --dataset viseval --split dev --dry    # free: ground-truth tables through the renderer, no model
uv run python -m tests.bench.runner --dataset viseval --split all --dry    # the same sweep over every case; the instrument for renderer changes
uv run python -m tests.bench.runner --dataset viseval --split dev          # live, about a cent a case; downloads the 19MB dataset once into .bench/
uv run python -m tests.bench.runner --dataset viseval --split test-250 --final   # the committed stratified draw of the held-out set
uv run python -m tests.bench.runner --dataset nvbench2 --split dev --limit 50    # nvBench 2.0's tables are VisEval's CSVs; the JSON downloads once
uv run python -m tests.bench.checks  .bench/results.json                         # what each deterministic check did over that run
uv run python -m tests.bench.rescore .bench/results.json --dataset viseval       # score a stored run again under today's rules, no model call
uv run python -m tests.bench.paired  .bench/before.json .bench/after.json        # two runs, case by case
uv run python -m tests.bench.reply   .bench/results.json --dataset viseval --out .bench/replied.json
```

**Two conditions, never blended.** A Turn that ends in a Clarification is an unanswered question, and the
**first pass** scores it as one. Every Clarification is then answered by a **simulated user**, a mini model
playing the person who asked, given their question, the analyst's question back and what the database is about,
never the gold (`tests/bench/user.py`); the answer after that reply is scored as a second condition, `after`,
beside the first. The number a real user would see is the second, and the honest thing about it is that a person
does not know the convention a gold happens to follow.

**The rules are published ones, not ours.** `ex` is BIRD's evaluator verbatim: set equality of row tuples,
column order and count strict, no tolerance. `correct` is the same after projecting the agent's columns onto the
gold's in any order. VisEval's rows `chart`, `data` and `order` are its own `chart_check`, `data_check` and
`order_check`, ported verbatim (`tests/bench/viseval_checks.py`, MIT) and fed from the Vega-Lite we build
instead of a deconstructed SVG. nvBench 2.0's `precision@1`, `recall@1`, `f1@1` and `hit@1` are the paper's rule
re-expressed from its evaluation code (`tests/bench/nvbench2_checks.py`; that repository carries no licence, so
the rule is restated rather than copied). `reads` stays ours: their layout checks need a browser and their
readability scores are vision models.

### VisEval, held out

The committed 250-case draw of the held-out 900 (`tests/bench/test-250.txt`), leaving 650 cases never looked at.

| condition | `ex` | `correct` | `chart` | `data` | `order` | `reads` | asked |
|---|---|---|---|---|---|---|---|
| first pass | 119/209 (57%) | 160/209 (77%) | 207/250 (83%) | 182/250 (73%) | 86/115 (75%) | 221/250 (88%) | 26/250 |
| after the simulated user's reply | 128/209 (61%) | 170/209 (81%) | 225/250 (90%) | 199/250 (80%) | 94/115 (82%) | 245/250 (98%) | — |

An earlier run on the identical 250, before the eight checks existed and asking nothing, scored `ex` 62%,
`correct` 82%, `chart` 92%, `data` 82%, `order` 85%, `reads` 99% in one pass. Paired case by case, the two runs
differ on 13 of 209 for `correct` after the reply — the checks buy a question the user can answer, at the price
of asking on 10% of cases.

**The caveats belong beside the numbers.**

- The benchmark databases are built from VisEval's CSVs, which carry no keys, so `tables.infer_keys` recovers
  them from the data: 85% of the join conditions in VisEval's own gold SQL. Paired over 100 cases the recovered
  keys moved `correct` by nothing, so the join cliff is semantics and not missing structure.
- `chart` is VisEval's own name match, so the pies the House Style refuses above three parts are counted as the
  misses they are, rather than excused in the scorer.
- 148 of the 1,150 cases bin their x axis, which is not SQL, so they ship no query that runs and are scored on
  `data` alone. `ex` and `correct` are over the 209 held-out cases whose gold executes.
- A **dry** sweep charts the ground truth's own table with the ground truth's own channels: `chart` 91%, `data`
  99%, `order` 99% over all 1,150. Those are ceilings on the renderer, not scores for the system, and because
  the renderer work was measured on them a dry run over a test split is not a held-out number either.
  `--split all` is a superset of dev, not a held-out set.
- None of the paper's own numbers is quoted or compared against: different populations, different databases, and
  *invalid* does not mean the same thing for a system that hands over a typed spec.

### nvBench 2.0, held out

The authors' own `test.json`, 300 of its 750 (ordered by a content hash, so the prefix is an unbiased sample),
none of which lies in our dev 100.

| condition | R@1 | P@1 | F1@1 | hit@1 | charted | asked |
|---|---|---|---|---|---|---|
| first pass | 6.4 | 17.7 | 9.2 | 53/300 (18%) | 197/300 | 12/300 |
| after the reply | 6.4 | 17.7 | 9.2 | 53/300 (18%) | 202/300 | — |

- This system answers **one** chart per Turn against a gold set of two to five, so recall is bounded by 1/|gold|
  and @3 and @5 would repeat @1. Their systems were asked for up to K charts from a table schema; the comparison
  is ours against ourselves.
- `hit@1` is scored on the **Chart Spec**. Scored on the chart actually drawn, after the form layer has had its
  say, it is 30/300.
- Its gold carries no query and no rows, so `ex` and `correct` are absent, not zero.
- Some golds are box plots, which the House Style never draws; those are misses by construction.

### What a Turn costs

Over 650 live cases and 2,460 model calls: median wall clock ~4.3 s, p90 8–9 s, median 3 model round trips,
median cost under a cent a case. 90–100% of that wall clock is round trips to the provider — the guard, the
eight twins, the Schema Document and the renderer together are 24–200 ms at p50. **The eight twins are free in
every sense that matters:** no model call, and a latency cost below the noise of one round trip.

### Connecting another dataset

`--dataset NAME` is an `importlib` module name under `tests/bench/`. A dataset is three functions over the
`Case` model in `tests/bench/case.py`:

```python
def cases(split: str) -> list[Case]: ...                      # your splits, your names; a held-out one starts with "test"
def register(cases: list[Case]) -> Path: ...                  # build the SQLite files and Schema Documents the agents will read
def score(case, result, spec, vega_lite) -> dict: ...         # your paper's rows, under your paper's names
```

Nothing outside your module may read your `extra`, and nothing in the shared code names a dataset — both are
asserted by tests. Rows appear in the results table because your `score` returned them; the presentation reads
them and never chooses them. `.bench/` is created for you, and `BENCH_DIR` moves it.

## HTTPS

So that the browser shows the page as secure and the cookie carries the `Secure` flag, make a locally trusted
certificate once with [mkcert](https://github.com/FiloSottile/mkcert):

```bash
brew install mkcert && mkcert -install     # once; asks for your password to trust the local CA
mkdir -p certs
mkcert -cert-file certs/cert.pem -key-file certs/key.pem localhost 127.0.0.1 ::1
uv run uvicorn data_agents.web.service:app --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem
```

`certs/` is git-ignored. Behind a TLS-terminating proxy, add `--proxy-headers` so the app sees the client's
scheme.

## Two things to know before pointing this at real data

- **Row contents reach the model as text.** The Schema Document carries the stored spellings of small text
  columns and three example values per column, and query results are sent to the Visualization Agent as sample
  rows. Both are what make value linking and chart choice work, and both mean the data is not private from the
  provider.
- **A Logfire token sends traces off the machine**, and those traces carry the SQL and the rows in it. With no
  token nothing is sent and nothing fails; `LOGFIRE_SEND_TO_LOGFIRE=false` turns it off outright whatever
  credentials exist on the host.

## Licence

MIT — see [LICENSE](LICENSE).

The four browser libraries under `data_agents/web/static/` are vendored and keep their own licences, recorded
with their versions and sources in [`data_agents/web/static/VENDORED.md`](data_agents/web/static/VENDORED.md):
vega, vega-lite and vega-embed are BSD-3-Clause, htmx is BSD-2-Clause. `tests/bench/viseval_checks.py` is
Microsoft's VisEval code, MIT, with its licence at the top of the file.
