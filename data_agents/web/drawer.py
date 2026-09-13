"""The admin drawer: the Registry, the Users, their Grants and the audit trail, as HTMX fragments. A role check, nothing more.

Access is navigated from either side — a searchable, paged list of Users opening one User's access, and a Database opening its
grantees — and granted or revoked through a typeahead, never a matrix. Roles are the two the system has, analyst and admin.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from data_agents.data import registry, schema_document
from data_agents.system import auth
from data_agents.system.auth import User
from data_agents.web.gui import Me, page
from data_agents.web.serving import admit, same_origin

router = APIRouter(tags=["drawer"], dependencies=[Depends(same_origin)])
PAGE = 10


def signed_in_admin(user: Me) -> User:
    try:
        auth.require_admin(user)
    except auth.NotAuthorized as e:
        raise HTTPException(403, str(e))
    return user


Admin = Annotated[User, Depends(signed_in_admin)]


def changed(response: HTMLResponse, refresh: bool = False) -> HTMLResponse:
    """Tell the page: the lists that watch for `changed` reload themselves; the whole page reloads when the tiles above are stale."""
    response.headers["HX-Trigger"] = "changed"
    if refresh:
        response.headers["HX-Refresh"] = "true"
    return response


def drawer_page(request: Request, admin: User, error: str = "") -> HTMLResponse:
    return page(request, "_drawer.html", admin, databases=list(registry.load().values()), found=registry.unregistered_files(), error=error)


@router.get("/drawer", response_class=HTMLResponse)
def drawer(request: Request, admin: Admin) -> HTMLResponse:
    return drawer_page(request, admin)


@router.get("/drawer/users", response_class=HTMLResponse)
def users(request: Request, admin: Admin, q: str = "", p: int = 1) -> HTMLResponse:
    listed, total = auth.list_users(q, p, PAGE)
    return page(request, "_users.html", admin, users=listed, total=total, q=q, p=p, pages=max(1, -(-total // PAGE)))


@router.post("/drawer/users", response_class=HTMLResponse)
def create_user(request: Request, admin: Admin, name: Annotated[str, Form()], role: Annotated[str, Form()] = "analyst") -> HTMLResponse:
    try:
        auth.create_user(name.strip(), role, by=admin.name)
    except ValueError as e:
        return page(request, "_users.html", admin, users=[], total=0, q="", p=1, pages=1, error=str(e))
    return changed(user_page(request, name.strip(), admin))


@router.get("/drawer/users/{name}", response_class=HTMLResponse)
def user_page(request: Request, name: str, admin: Admin) -> HTMLResponse:
    """One User's access: what they hold, a typeahead of what they could be granted, and the way out."""
    try:
        user = auth.user(name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return page(request, "_user.html", admin, subject=user, offer=[d for d in registry.load() if d not in user.grants])


@router.post("/drawer/users/{name}/remove", response_class=HTMLResponse)
def remove_user(request: Request, name: str, admin: Admin) -> HTMLResponse:
    if name == admin.name:
        raise HTTPException(400, "you cannot remove yourself")
    try:
        auth.remove_user(name, by=admin.name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return changed(HTMLResponse(""))


@router.get("/drawer/databases/{name}", response_class=HTMLResponse)
def grantees(request: Request, name: str, admin: Admin) -> HTMLResponse:
    """One Database's side: who holds it, and a typeahead over every User who does not."""
    if name not in registry.load():
        raise HTTPException(404, f"{name} is not registered")
    holders = auth.grantees(name)
    users = auth.load_users()
    return page(request, "_grantees.html", admin, database=name, holders=holders, admins=[u for u in users if users[u].role == "admin"],
                offer=[u for u, user in users.items() if u not in holders and user.role != "admin"])


@router.post("/drawer/grants", response_class=HTMLResponse)
def grant(request: Request, admin: Admin, user_name: Annotated[str, Form()], database: Annotated[str, Form()],
          revoke: Annotated[bool, Form()] = False, side: Annotated[str, Form()] = "user") -> HTMLResponse:
    """Grant or revoke, then redraw whichever side the admin was looking from."""
    if database not in registry.load():
        raise HTTPException(404, f"{database} is not registered")
    try:
        (auth.revoke if revoke else auth.grant)(user_name, database, by=admin.name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:  # an admin: nothing to grant or revoke
        raise HTTPException(400, str(e))
    return changed(grantees(request, database, admin) if side == "database" else user_page(request, user_name, admin))


@router.get("/drawer/audit", response_class=HTMLResponse)
def audit(request: Request, admin: Admin) -> HTMLResponse:
    return page(request, "_audit.html", admin, entries=auth.audit(20))


@router.post("/drawer/databases", response_class=HTMLResponse)
def register_database(request: Request, admin: Admin, path: Annotated[str, Form()] = "", name: Annotated[str, Form()] = "",
                      url: Annotated[str, Form()] = "", description: Annotated[str, Form()] = "") -> HTMLResponse:
    """Register a file found on disk in one click, or anything else from the form below it.

    A file whose Schema Document is already on file (an unregistered database, registered again) keeps that document and the
    description it was written with: no model call, and the hand-verified Gotchas survive. Anything new is drafted and verified.
    """
    if path:
        found = {str(p): p for p in registry.unregistered_files()}
        if path not in found:  # only ever a file the Registry offered; never a path the browser chose
            return drawer_page(request, admin, error=f"{path} is not one of the files on offer.")
        name, url = found[path].stem, f"sqlite:///{found[path]}"
    try:
        auth.check_name(name, "database")  # before the name touches the filesystem below
        on_file = registry.store.DIR / "schemas" / f"{name}.md"
        document = on_file.read_text() if path and on_file.exists() else None
        if document is None:
            admit(admin)  # drafting the Schema Document is a model call
        registry.register(name, url, description or (schema_document.description_in(document) if document else ""), document, by=admin.name)
    except (ValueError, OSError) as e:
        return drawer_page(request, admin, error=str(e))
    return changed(drawer_page(request, admin), refresh=True)  # what is registered changed, and the page around this fragment shows it


@router.post("/drawer/databases/{name}/unregister", response_class=HTMLResponse)
def unregister_database(request: Request, name: str, admin: Admin) -> HTMLResponse:
    try:
        registry.unregister(name, by=admin.name)  # revokes every Grant on it, so the effective set shrinks for everyone
    except KeyError as e:
        raise HTTPException(404, str(e))
    return changed(drawer_page(request, admin), refresh=True)
