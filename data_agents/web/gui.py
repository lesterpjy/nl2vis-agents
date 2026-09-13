"""The GUI: server-rendered HTML over the same orchestrator the CLI drives. Jinja fragments, HTMX, vega-embed in the browser.

A thin client, like the CLI: no analysis, no chart decision and no authorization rule lives here. The bearer token arrives once
on a form and is exchanged for a Sign-in, whose id is kept in an HttpOnly cookie: the credential never reaches the browser's
storage or page code, and every request maps the id to a User again — grants ∩ live Registry, as everywhere else. The admin
drawer is `drawer.py`.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from data_agents.agents import suggestion
from data_agents.charts import renderer
from data_agents.contracts import AnalysisResult, Clarification, TurnRequest
from data_agents.data import registry, schema_document
from data_agents.orchestrator import run_turn
from data_agents.system import auth
from data_agents.system.auth import User
from data_agents.system.sessions import Session, SessionStore, StoredChart, StoredTurn
from data_agents.web.serving import admit, delete_session, event_stream, own_session, same_origin

HERE = Path(__file__).parent
COOKIE = "da_sign_in"
router = APIRouter(tags=["gui"], dependencies=[Depends(same_origin)])
templates = Jinja2Templates(directory=HERE / "templates")


def get_store() -> SessionStore:
    return SessionStore()


def maybe_user(da_sign_in: Annotated[str | None, Cookie()] = None) -> User | None:
    try:
        return auth.signed_in_user(da_sign_in)
    except auth.NotAuthenticated:
        return None


def signed_in(user: Annotated[User | None, Depends(maybe_user)]) -> User:
    if user is None:
        raise HTTPException(303, "sign in first", headers={"Location": "/"})
    return user


Me = Annotated[User, Depends(signed_in)]
Store = Annotated[SessionStore, Depends(get_store)]


def page(request: Request, name: str, user: User | None = None, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"user": user, **context})


def charts_of(store: SessionStore, session: Session, turns: list[StoredTurn]) -> dict[int, dict]:
    """The Vega-Lite of every Turn that drew one: a read, unless the renderer has changed since. Then the Chart Spec, which is
    the record, is built again under today's renderer and kept — history restyled on purpose, and never re-derived by a model."""
    built = {}
    for turn in turns:
        if turn.chart is None:
            continue
        if turn.chart.renderer != renderer.FINGERPRINT and isinstance(turn.result, AnalysisResult):
            try:
                turn.chart = StoredChart(spec=turn.chart.spec, vega_lite=renderer.build(turn.chart.spec, turn.result, session.database), renderer=renderer.FINGERPRINT)
            except ValueError:
                continue  # the spec no longer fits the result under this renderer; the table still stands
            store.attach_chart(session.id, turn.n, turn.chart)
        built[turn.n] = turn.chart.vega_lite
    return built


_schemas: dict[str, tuple[str, list[tuple[str, str]]]] = {}
_suggested: dict[tuple[str, int], str] = {}  # one draft per Turn per process: reopening a Session must not call a model


def schema_of(database: str) -> list[tuple[str, str]]:
    """Table names and their columns, so the page can say what this database is able to answer.

    Introspecting sakila costs ~300 ms, which is the whole page. Held against the URL it was read from, so a database
    registered again somewhere else is read again rather than remembered wrongly.
    """
    entry = registry.load()[database]
    if (held := _schemas.get(database)) and held[0] == entry.url:
        return held[1]
    db = registry.open_database(entry)
    try:
        tables = [(t.name, ", ".join(c for c, _, _ in t.columns)) for t in schema_document.introspect(db) if t.kind == "table"]
    finally:
        db.close()
    _schemas[database] = (entry.url, tables)
    return tables


@router.get("/", response_class=HTMLResponse)
def sign_in_page(request: Request, user: Annotated[User | None, Depends(maybe_user)]) -> Response:
    return RedirectResponse("/workspace", 303) if user else page(request, "sign_in.html")


@router.post("/sign-in")
def sign_in(request: Request, token: Annotated[str, Form()]) -> Response:
    try:
        sign_in_id = auth.sign_in(token)
    except auth.NotAuthenticated:
        return page(request, "sign_in.html", error="That token belongs to nobody.")
    response = RedirectResponse("/workspace", 303)
    # The id, not the token; Secure whenever the page itself came over TLS, which is where the cookie must stay.
    response.set_cookie(COOKIE, sign_in_id, httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=auth.SIGN_IN_HOURS * 3600)
    return response


@router.post("/sign-out")
def sign_out(da_sign_in: Annotated[str | None, Cookie()] = None) -> RedirectResponse:
    auth.sign_out(da_sign_in)  # the row goes too, so the id is worthless even if it was copied
    response = RedirectResponse("/", 303)
    response.delete_cookie(COOKIE)
    return response


def workspace_page(request: Request, user: User, store: SessionStore, error: str = "") -> HTMLResponse:
    entries = registry.load()
    databases = [entries[name] for name in auth.effective_databases(user, entries)]
    return page(request, "workspace.html", user, databases=databases, sessions=store.list_sessions(user.name), error=error)


@router.get("/workspace", response_class=HTMLResponse)
def workspace(request: Request, user: Me, store: Store) -> HTMLResponse:
    return workspace_page(request, user, store)


@router.post("/workspace/sessions", response_class=HTMLResponse)
def start_session(request: Request, database: Annotated[str, Form()], user: Me, store: Store) -> Response:
    try:
        auth.authorize(user, database, registry.load())
    except auth.NotAuthorized:  # the Registry or the grant changed under an open page; say so rather than show a bare 403
        return workspace_page(request, user, store, error=f"{database} is no longer yours to open: it was unregistered, or the grant was revoked.")
    session = store.create(user.name, database)
    return RedirectResponse(f"/workspace/sessions/{session.id}", 303)  # switching database starts a new Session: history built on one schema is wrong context for another


@router.post("/workspace/sessions/{session_id}/delete", response_class=HTMLResponse)
def remove_session(session_id: str, user: Me, store: Store) -> HTMLResponse:
    """The row the list showed, gone for good; HTMX swaps the empty reply over it."""
    delete_session(store, own_session(store, user, session_id))
    return HTMLResponse("")


@router.get("/workspace/sessions/{session_id}", response_class=HTMLResponse)
def session_page(request: Request, session_id: str, user: Me, store: Store) -> HTMLResponse:
    session = own_session(store, user, session_id)
    entry = registry.load().get(session.database)
    turns = store.turns_of(session_id)
    return page(request, "session.html", user, session=session, turns=turns, charts=charts_of(store, session, turns),
                description=entry.description if entry else "", tables=schema_of(session.database) if entry else [])


@router.get("/workspace/sessions/{session_id}/turns/{n}", response_class=HTMLResponse)
def turn_card(request: Request, session_id: str, n: int, user: Me, store: Store) -> HTMLResponse:
    """One stored Turn as the card the history renders, so a live Turn and a reopened Session share their markup."""
    session = own_session(store, user, session_id)
    turns = [turn for turn in store.turns_of(session_id) if turn.n == n]
    if not turns:
        raise HTTPException(404, f"session {session_id} has no turn {n}")
    return page(request, "_card.html", user, turn=turns[0], charts=charts_of(store, session, turns))


@router.get("/workspace/sessions/{session_id}/suggestion", response_class=PlainTextResponse)
def suggestion_for(session_id: str, user: Me, store: Store) -> str:
    """What belongs in the box, given where the Session got to. Empty when nothing can be written, which is a fine outcome."""
    session = own_session(store, user, session_id)
    turns = store.turns_of(session_id)
    last = turns[-1] if turns else None
    if last is None or (session.id, last.n) not in _suggested:
        admit(user)  # only when a model is about to be called
    if last is None:
        return suggestion.first_question(session.database, "\n".join(f"{name}({columns})" for name, columns in schema_of(session.database)))
    if (session.id, last.n) not in _suggested:
        if isinstance(last.result, Clarification):  # the Turn ended in a question to the user; answer that, do not change the subject
            _suggested[session.id, last.n] = suggestion.answer_to(last.question, last.result.question)
        elif isinstance(last.result, AnalysisResult):
            _suggested[session.id, last.n] = suggestion.next_question(last.question, last.result.narrative, last.result.table.columns)
        else:
            _suggested[session.id, last.n] = ""
    return _suggested[session.id, last.n]


@router.post("/workspace/sessions/{session_id}/turns")
def take_turn(session_id: str, body: TurnRequest, user: Me, store: Store) -> StreamingResponse:
    session = own_session(store, user, session_id)
    admit(user)
    return event_stream(lambda emit: run_turn(store, session, body.question, emit, chart=body.chart, hints=body.hints), detailed=user.role == "admin")
