"""Sign-in, sessions, CSRF. See the 2026-08-23 web service design, §1.5, §4.1, §5.5

**Deny by default.** The gate lives in one place — `FastAPI(dependencies=[...])` applies to
every route, and newly added routes are blocked automatically. The allow list is an explicit
whitelist, not "remember to add a decorator".

S1 only decides **who you are**. Deciding **what you may do** (the permission matrix) is S2's job.

**The gate is not locked while the identity system is disabled.** Local `fr serve` is how this
product is used today; demanding sign-in out of the box would lock people out of their own
machines, and the first admin needs a way in too. The moment `fr account invite` sends the
first invitation, the gate locks — it does not wait for the invitee to accept, otherwise the
window in between is one open to everyone.
"""
from starlette.requests import Request

from framework_reader.identity.permissions import allows, permissions_of

COOKIE = "fr_session"

# These must stay reachable without sign-in, otherwise nobody could sign in
PUBLIC_PREFIXES = ("/login", "/logout", "/invite", "/auth", "/static", "/favicon",
                   "/favicon.ico", "/favicon.svg", "/apple-touch-icon.png",
                   # Custom logo: the sign-in page (a bare page) must show it too, so it has
                   # to be public. It holds only one admin-uploaded image and no session data.
                   "/branding")


class NeedsLogin(Exception):
    """No valid session. GET is sent to the sign-in page; POST is refused outright."""

    def __init__(self, next_url: str = "/") -> None:
        self.next_url = next_url


class BadCsrf(Exception):
    """A write operation did not carry this session's token."""


class Forbidden(Exception):
    """Signed in, but this role is not allowed to do this."""

    def __init__(self, permission: str) -> None:
        self.permission = permission


class Unlabelled(Exception):
    """The route does not declare what permission it needs.

    That is a **code defect**, not a user error, so the request is refused rather than let
    through. Deny by default means exactly this: the route that forgot its label must break —
    best in tests, but even in production that beats silently letting it through.
    See design §1.5
    """


def needs(permission: str):
    """Declares what permission a route needs. The decision happens in the guard; this only
    sticks on the label.

        @app.post("/c/{control_id}/confirm")
        @needs(INTERPRETATION_CONFIRM)
        def confirm_control(...): ...
    """

    def mark(func):
        func.__fr_permission__ = permission
        return func

    return mark


def permission_of(request) -> str | None:
    route = request.scope.get("route")
    endpoint = getattr(route, "endpoint", None)
    return getattr(endpoint, "__fr_permission__", None)


def is_public(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in PUBLIC_PREFIXES)


def make_guard(store_of, *, locked: bool | object = False):
    """`store_of()` returns the IdentityStore. It is a callable so tests can swap in another store.

    `locked=True` means "require sign-in whether or not any accounts exist". That is right once
    Entra is connected: configuring an IdP means this is an online deployment, and leaving the
    gate open then means the first person to walk in could be anyone. You may also pass a
    **callable returning a bool**: the single sign-on configuration saved on the settings page
    changes at runtime, so the locked state has to be judged on every request, not taken from
    the startup snapshot.
    """

    async def guard(request: Request) -> None:
        from framework_reader.web import views

        locked_now = locked() if callable(locked) else locked
        store = store_of()
        request.state.session = None
        request.state.login_enabled = locked_now or store.configured()
        # Set on every request: on the one request where it is missed, the page would receive
        # the previous request's token.
        views.CHROME.set(("", ""))
        views.PERMS.set(None)

        if not request.state.login_enabled:
            # No accounts exist yet: local single-user usage, gate unlocked. See the module docstring.
            return

        if is_public(request.url.path):
            return

        session = store.resume(request.cookies.get(COOKIE, ""))
        if session is None:
            raise NeedsLogin(next_url=str(request.url.path))
        request.state.session = session
        views.CHROME.set((session.csrf, session.account.email))
        views.PERMS.set(permissions_of(session.account.roles))

        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            # Starlette caches the form() result on the Request,
            # so reading it here once does not drain the route's own form() call.
            form = await request.form()
            if not _same(str(form.get("csrf", "")), session.csrf):
                raise BadCsrf

        # ---- Authorization. Deny by default: unlabelled routes are never let through ----
        permission = permission_of(request)
        if permission is None:
            raise Unlabelled(request.url.path)
        if not allows(session.account.roles, permission):
            raise Forbidden(permission)

    return guard


def _same(a: str, b: str) -> bool:
    import hmac

    return bool(a) and hmac.compare_digest(a, b)
