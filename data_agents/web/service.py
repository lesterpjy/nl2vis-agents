"""FastAPI service: bearer token to User, the CLI's operations over HTTP, Turn events as server-sent events. A thin client of orchestrator.py."""

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from data_agents.contracts import ChartHints, ChartRequest, GrantRequest, RegisterRequest, SessionRequest, SessionSummary, TurnRequest
from data_agents.data import registry, supplied
from data_agents.orchestrator import chart_result, run_turn, visualize_turn
from data_agents.system import auth, tracing
from data_agents.web import drawer, gui
from data_agents.data.registry import DatabaseEntry
from data_agents.system.auth import AuditEntry, User
from data_agents.system.sessions import Session, SessionStore
from data_agents.web.serving import admit, delete_session, event_stream, own_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Tracing starts with the server, not with the import: the test suite builds this app hundreds of times and must not
    send a span for any of it. Without this the GUI and the API were untraced even with a token set."""
    tracing.configure("data-agents-service")
    yield


app = FastAPI(title="Data Agents", description="Natural-language analysis over registered databases, charted in the House Style.",
              lifespan=lifespan)
app.include_router(gui.router)  # the GUI is one more client of the operations below
app.include_router(drawer.router)
app.mount("/static", StaticFiles(directory=gui.HERE / "static"), name="static")


@app.middleware("http")
async def browser_headers(request: Request, call_next) -> Response:
    """Two headers every response carries: no MIME sniffing, and over TLS, stay on TLS for a year."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def get_store() -> SessionStore:
    return SessionStore()  # one connection per request: any replica serves any Turn


def current_user(authorization: Annotated[str | None, Header()] = None) -> User:
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    try:
        return auth.authenticate(token)
    except auth.NotAuthenticated:
        raise HTTPException(401, "invalid or missing bearer token")


def current_admin(user: Annotated[User, Depends(current_user)]) -> User:
    try:
        auth.require_admin(user)
    except auth.NotAuthorized as e:
        raise HTTPException(403, str(e))
    return user


Me = Annotated[User, Depends(current_user)]
Admin = Annotated[User, Depends(current_admin)]
Store = Annotated[SessionStore, Depends(get_store)]


@app.get("/me")
def me(user: Me) -> User:
    return user


@app.get("/databases")
def databases(user: Me) -> list[DatabaseEntry]:
    entries = registry.load()
    return [entries[name] for name in auth.effective_databases(user, entries)]


@app.post("/sessions", status_code=201)
def create_session(body: SessionRequest, user: Me, store: Store) -> Session:
    try:
        auth.authorize(user, body.database, registry.load())
    except auth.NotAuthorized as e:
        raise HTTPException(403, str(e))
    return store.create(user.name, body.database)


@app.get("/sessions")
def list_sessions(user: Me, store: Store) -> list[SessionSummary]:
    return store.list_sessions(user.name)


@app.get("/sessions/{session_id}")
def get_session(session_id: str, user: Me, store: Store) -> Session:
    return own_session(store, user, session_id)


@app.delete("/sessions/{session_id}", status_code=204)
def remove_session(session_id: str, user: Me, store: Store) -> None:
    delete_session(store, own_session(store, user, session_id))


@app.post("/sessions/{session_id}/turns")
def take_turn(session_id: str, body: TurnRequest, user: Me, store: Store) -> StreamingResponse:
    session = own_session(store, user, session_id)
    admit(user)
    return event_stream(lambda emit: run_turn(store, session, body.question, emit, chart=body.chart, hints=body.hints), detailed=user.role == "admin")


@app.post("/sessions/{session_id}/chart")
def chart_latest(session_id: str, user: Me, store: Store, hints: ChartHints | None = None) -> StreamingResponse:
    session = own_session(store, user, session_id)
    admit(user)
    return event_stream(lambda emit: visualize_turn(store, session, emit, hints), detailed=user.role == "admin")


@app.post("/charts")
def chart_supplied(body: ChartRequest, user: Me) -> StreamingResponse:
    """The CLI's `visualize --sql` and `--csv` over HTTP: no Session, no Analysis Agent, the same guard and authorization."""
    if (body.sql is None) == (body.csv is None):
        raise HTTPException(422, "give exactly one of sql or csv")
    admit(user)
    try:
        if body.csv is not None:
            result, source = supplied.from_csv_text(body.csv, body.name, body.intent), body.name
        else:
            if body.database is None:
                raise HTTPException(422, "sql needs a database")
            auth.authorize(user, body.database, registry.load())
            db = registry.open_database(registry.load()[body.database])
            try:
                result = supplied.from_sql(db, body.sql, body.intent)
            finally:
                db.close()
            source = body.database
    except auth.NotAuthorized as e:
        raise HTTPException(403, str(e))
    except ValueError as e:  # a rejected statement, a failed query, or an unusable CSV
        raise HTTPException(400, str(e))
    return event_stream(lambda emit: chart_result(source, result, emit, body.hints), detailed=user.role == "admin")


@app.post("/admin/databases", status_code=201)
def register_database(body: RegisterRequest, admin: Admin) -> DatabaseEntry:
    if body.document is None:
        admit(admin)  # drafting the Schema Document is a model call
    try:
        return registry.register(body.name, body.url, body.description, body.document, by=admin.name)
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))


@app.delete("/admin/databases/{name}", status_code=204)
def unregister_database(name: str, admin: Admin) -> None:
    try:
        registry.unregister(name, by=admin.name)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.post("/admin/grants", status_code=204)
def grant(body: GrantRequest, admin: Admin) -> None:
    if body.database not in registry.load():
        raise HTTPException(404, f"{body.database} is not registered")
    try:
        auth.grant(body.user, body.database, by=admin.name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/admin/grants/{user}/{database}", status_code=204)
def revoke(user: str, database: str, admin: Admin) -> None:
    try:
        auth.revoke(user, database, by=admin.name)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.get("/admin/users")
def list_users(admin: Admin, q: str = "", page: int = 1) -> list[User]:
    return auth.list_users(q, page)[0]


@app.post("/admin/users", status_code=201)
def create_user(body: User, admin: Admin) -> User:
    """A new principal with its grants; signing in still needs DATA_AGENTS_TOKEN_<NAME> in the environment."""
    if missing := [g for g in body.grants if g not in registry.load()]:
        raise HTTPException(404, f"not registered: {', '.join(missing)}")
    try:
        auth.create_user(body.name, body.role, by=admin.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    for database in body.grants:
        auth.grant(body.name, database, by=admin.name)
    return auth.user(body.name)


@app.delete("/admin/users/{name}", status_code=204)
def remove_user(name: str, admin: Admin) -> None:
    try:
        auth.remove_user(name, by=admin.name)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/admin/audit")
def audit(admin: Admin, limit: int = 20) -> list[AuditEntry]:
    return auth.audit(limit)
