"""What the JSON API and the GUI share once a User is known: the Session they may touch, the pace they are held to, a Turn's
events as a stream, and the removal of a Session with everything it wrote."""

import contextvars
import logging
import queue
import threading
from typing import Callable, Iterator

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from data_agents.contracts import TurnError, TurnEvent
from data_agents.orchestrator import Emit, plain
from data_agents.web import pacing
from data_agents.system.auth import User
from data_agents.system.sessions import Session, SessionStore

log = logging.getLogger("data_agents")


def own_session(store: SessionStore, user: User, session_id: str) -> Session:
    try:
        session = store.get(session_id)
    except KeyError:
        raise HTTPException(404, f"no session {session_id}")
    if session.user != user.name:
        raise HTTPException(403, "that session belongs to another user")
    return session


def admit(user: User) -> None:
    """Every route that will call a model asks first; a refusal is a 429 the client can wait out."""
    try:
        pacing.admit(user.name)
    except pacing.Throttled as e:
        raise HTTPException(429, str(e), headers={"Retry-After": "60"})


def same_origin(request: Request) -> None:
    """Cross-site request forgery: a state-changing request must come from this origin. Current browsers say so in
    Sec-Fetch-Site; Origin is the fallback; neither header means no browser, and the cookie was never theirs to send."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        if site not in ("same-origin", "none"):
            raise HTTPException(403, "cross-site request refused")
        return
    origin = request.headers.get("origin")
    if origin is not None and origin != f"{request.url.scheme}://{request.headers.get('host', '')}":
        raise HTTPException(403, "cross-site request refused")


def event_stream(work: Callable[[Emit], object], detailed: bool) -> StreamingResponse:
    """Run the Turn in a thread; each event becomes one SSE message as it is emitted. A None closes the stream.

    `detailed` is the role check on error text: an admin reads the exception, an analyst reads the sentence written for them."""
    try:
        release = pacing.slot()
    except pacing.Throttled as e:
        raise HTTPException(429, str(e), headers={"Retry-After": "10"})
    events: queue.Queue[TurnEvent | None] = queue.Queue()
    last: list[TurnEvent | None] = [None]

    def emit(event: TurnEvent) -> None:
        last[0] = event
        events.put(event)

    def run() -> None:
        try:
            work(emit)
        except Exception as e:
            if not isinstance(last[0], TurnError):  # the orchestrator reports its own failures; this covers the rest
                log.exception("turn failed outside the orchestrator")
                emit(TurnError(stage="service", message=plain(e), detail=str(e)))
        finally:
            release()
            events.put(None)

    def messages() -> Iterator[str]:
        while (event := events.get()) is not None:
            if isinstance(event, TurnError) and not detailed:
                event = event.model_copy(update={"detail": ""})
            yield f"event: {event.kind}\ndata: {event.model_dump_json()}\n\n"

    threading.Thread(target=contextvars.copy_context().run, args=(run,), daemon=True).start()  # the request's context (tracing, test overrides) follows the work
    return StreamingResponse(messages(), media_type="text/event-stream")


def delete_session(store: SessionStore, session: Session) -> None:
    """The Session, its Turns and the charts they kept, gone; nothing keeps a copy."""
    store.delete(session.id)
