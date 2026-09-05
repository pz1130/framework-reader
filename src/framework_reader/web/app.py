"""Local web shell. Main spec §7.3.6

**A thin wrapper, nothing more.** Data and business logic live entirely in QueryAPI and the
existing modules; no raw SQL may be written here (main spec §8①) and no business decisions
either — otherwise the web and the CLI slowly grow two divergent sets of behaviour.

Local deployment: no accounts, no tenant isolation. What the user imports goes into the user
database on their own machine, and not a single byte leaves it. Main spec §7.3.5
"""
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from framework_reader.query.api import QueryAPI
from framework_reader.web import views
from framework_reader import paths

DEFAULT_DB = paths.content_db()


def _short(control_id: str) -> str:
    return control_id.split(":", 1)[-1]


def _is_mine(view) -> bool:
    """Frameworks the user imported themselves. `tier` is the only test; do not guess from ID prefixes."""
    from framework_reader.schema.entities import LicenseTier

    return view is not None and view.tier == LicenseTier.U_USER


# Number of conversation turns shown to the model. **Must be capped** — every message re-feeds
# the whole history; uncapped, the longer the chat the more each message costs, and the approach
# you abandoned three hours ago keeps tagging along forever.
CHAT_CONTEXT_TURNS = 6


def create_app(
    db: Path = DEFAULT_DB, draft_runner=None, rewrite_runner=None,
    user_db: Path | None = None, identity_db: Path | None = None,
    secure_cookies: bool = False, entra=None, entra_fetch=None,
    http_get=None, probe_runner=None, outline_runner=None, shape_runner=None,
    chat_runner=None, search_runner=None, body_rewrite_runner=None,
) -> FastAPI:
    """`user_db` is the user database of this deployment. The whole organisation **shares one** —

    This product is one security team collaborating on one body of material, not several customer
    companies each keeping their own. Data is not isolated between users; what is isolated is
    **actions** (who may edit, who may sign off), see
    `docs/superpowers/specs/2026-08-23-hosted-service-rbac-aad-design.md` §3.

    The parameter exists only for testability: the default is still `$FRAMEWORK_READER_HOME/user.sqlite`.

    `draft_runner` / `rewrite_runner` / `search_runner` likewise exist only for tests:
    by default the real model is called; tests plug in a stand-in that never touches the network.
    """
    from framework_reader import usage
    from framework_reader.identity.store import IdentityStore
    from framework_reader.identity import permissions as perm
    from framework_reader.web.auth import (
        COOKIE, BadCsrf, Forbidden, NeedsLogin, Unlabelled, make_guard, needs,
    )

    library = Path(user_db) if user_db else usage.home() / "user.sqlite"
    identity = IdentityStore(identity_db)

    from framework_reader.identity.entra import EntraClient, EntraConfig
    from framework_reader.llm.config import BudgetError, ModelConfig

    # Same operational database as the identity layer: this is deployment configuration, not business data.
    models_config = ModelConfig(identity_db)

    entra_config = entra if entra is not None else EntraConfig.from_env()

    # SSO configuration saved and enabled on the settings page wins; environment variables are
    # the fallback. **Fetched fresh on every request** — once the admin saves the configuration
    # on the settings page it must take effect on the next request, not on a startup snapshot.
    def _entra_client():
        saved = identity.sso_config()
        if saved and saved.get("enabled"):
            from framework_reader import crypto

            try:
                secret = identity.sso_secret()
            except crypto.SecretError:
                # Master key missing or undecryptable: the token exchange cannot succeed anyway,
                # so treat it as unconfigured and let the sign-in page fall back to passphrase
                # sign-in instead of 500-ing the whole site.
                secret = ""
            cfg = EntraConfig(
                tenant_id=saved.get("tenant_id", ""),
                client_id=saved.get("client_id", ""),
                client_secret=secret,
                redirect_uri=saved.get("redirect_uri", ""),
                authority=saved.get("authority") or "https://login.microsoftonline.com",
            )
            if cfg.configured():
                return EntraClient(cfg, fetch=entra_fetch)
        if entra_config.configured():
            return EntraClient(entra_config, fetch=entra_fetch)
        return None

    def _saved_sso_is_https() -> bool:
        saved = identity.sso_config()
        return bool(saved and saved.get("enabled")
                    and saved.get("redirect_uri", "").startswith("https://"))

    # The startup environment-variable snapshot still covers the cookie Secure fallback; the
    # https callback address saved on the settings page is judged fresh on every request
    # (see _set_cookie).
    secure_cookies = secure_cookies or entra_config.redirect_uri.startswith("https://")

    # The gate lives in one place: it applies to every route, and newly added routes are blocked
    # automatically. A "remember to add the decorator" scheme means one missed route is an
    # unauthenticated entrance. Design §1.5
    #
    # Entra being configured also counts as locking the gate: an IdP in the picture means this is
    # an online deployment, and leaving it open then means the first person to walk in could be
    # anyone. A callable is passed because the single sign-on configuration on the settings page
    # changes at runtime; the locked state cannot be a startup snapshot.
    # Swagger and openapi.json are switched off: FastAPI registers them specially and they
    # **bypass the gate above**, amounting to a complete route list anyone could fetch without
    # signing in. An internal tool does not need them.
    app = FastAPI(title="Framework Reader",
                  docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(make_guard(
                      lambda: identity,
                      locked=lambda: _entra_client() is not None))])

    @app.exception_handler(NeedsLogin)
    async def _needs_login(request: Request, exc: NeedsLogin):
        if request.method != "GET":
            return HTMLResponse(views.refused(
                "Sign in required",
                "Your session has expired or you are not signed in.",
                "Sign in again on the sign-in page and retry."), 401)
        target = f"/login?next={quote(exc.next_url, safe='')}"
        return RedirectResponse(target, status_code=303)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        session = getattr(request.state, "session", None)
        roles = ", ".join(sorted(session.account.roles)) if session else "(none)"
        return HTMLResponse(views.refused(
            "Your role cannot do this",
            f"This action requires the {exc.permission} permission.",
            f"Your current roles: {roles}. To do this, ask an administrator to grant you the matching role."), 403)

    @app.exception_handler(Unlabelled)
    async def _unlabelled(_request: Request, exc: Unlabelled):
        # The route forgot to declare its permission. That is a code defect, so refuse instead
        # of letting it through — best if it breaks in tests, but even in production that beats
        # silently letting it through. Design §1.5
        return HTMLResponse(views.refused(
            "This route has no permission declared",
            f"{exc.args[0] if exc.args else ''} does not declare what permission it needs; refused.",
            "This is a code defect, please report it."), 403)

    @app.exception_handler(BadCsrf)
    async def _bad_csrf(_request: Request, _exc: BadCsrf):
        return HTMLResponse(views.refused(
            "This submission failed validation",
            "The form is missing this session's token, so the request was refused.",
            "Usually the page has been open too long or the session changed. Refresh the page and submit again."), 403)



    def _user_db() -> Path:
        return library

    def _who(request: Request) -> str:
        session = getattr(request.state, "session", None)
        return session.account.email if session else ""

    def _account_id(request: Request) -> str | None:
        """The "must not grant yourself a role" rule compares account_id. Returns None when not signed in (local usage)."""
        session = getattr(request.state, "session", None)
        return session.account.id if session else None

    def _local_user() -> str:
        import getpass

        return getpass.getuser()

    def _default_runner(key: str, user_db: Path | None = None, only=None):
        """A key with a colon is one control; otherwise it is a framework. The job table uses the same key.

        `user_db` is resolved at the moment the request arrives and passed in — there is no
        request context on a background thread, and resolving the tenant again inside the thread
        yields the default one, i.e. doing A's work into B's database.
        """
        from framework_reader.interpret.run import draft_framework, fill_blanks_one

        if ":" in key:
            return fill_blanks_one(db, key, user_db, overlay=True)
        # From the web, all seven fields are always drafted in full. Built-in frameworks are also
        # overlaid into the user database — that is the working copy, and it never enters
        # content/interpretations/.
        return draft_framework(db, key, full=True, user_db=user_db, overlay=True,
                               only=only)

    def _default_rewriter(control_id: str, field: str, instruction: str):
        from framework_reader.interpret.run import rewrite_one

        return rewrite_one(db, control_id, field, instruction, _user_db())

    run_draft = draft_runner or _default_runner
    run_rewrite = rewrite_runner or _default_rewriter

    def _default_body_rewriter(control_id: str, instruction: str, current: str):
        from framework_reader.interpret.run import rewrite_body

        return rewrite_body(db, control_id, instruction, current, _user_db())

    def api() -> QueryAPI:
        # One connection per request: the underlying driver forbids sharing connections across threads by default.
        return QueryAPI(db, user_db=library)

    def _frameworks(reader: QueryAPI) -> list[dict]:
        from framework_reader.userframework.store import UserFrameworkStore

        mine = {f.id for f in UserFrameworkStore(_user_db()).list_frameworks()}
        progress = reader.framework_progress()
        out = []
        for view in reader.list_frameworks():
            controls, with_interp = progress.get(view.id, (0, 0))
            out.append({
                "id": view.id, "name": view.name, "mine": view.id in mine,
                "controls": controls, "with_interp": with_interp,
            })
        out.sort(key=lambda f: (not f["mine"], f["id"]))
        return out

    @app.exception_handler(404)
    async def not_found(_request: Request, _exc) -> HTMLResponse:
        """The default {"detail":"Not Found"} just reads as "clicked and got nothing".

        The most common cause is the service still running old code — without --reload, uvicorn
        fixes the routes at startup. So this page says "restart it" outright.
        """
        return HTMLResponse(views.page(
            "Not found",
            "<h2>This address does not exist</h2>"
            '<p class="note">If this link worked a moment ago, the local service is probably running old code: '
            "uvicorn does not hot-reload, so restart <code>fr serve</code> after updating the code.</p>"
            '<p><a href="/">Back to home</a></p>',
        ), 404)

    # ---------- Sign-in / invitations ----------

    def _set_cookie(response: Response, token: str) -> Response:
        # Secure is only meaningful over https; sending it while debugging over local http stops
        # the cookie from being delivered at all. So set it according to the deployment mode,
        # never hardcoded. A reverse proxy terminating TLS passes x-forwarded-proto.
        response.set_cookie(
            COOKIE, token, httponly=True, samesite="lax", path="/",
            # The single sign-on configuration saved on the settings page is judged fresh on
            # every request: once the admin saves an https callback address, the very next
            # sign-in cookie should carry Secure, without waiting for a restart.
            secure=secure_cookies or _saved_sso_is_https(),
        )
        return response

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/", error: str = ""):
        if request.state.session is not None:
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(views.login(error=error, next_url=next,
                                        entra=_entra_client() is not None))

    @app.post("/login")
    def login_submit(
        email: str = Form(""), password: str = Form(""), next: str = Form("/"),
    ):
        from framework_reader.identity.store import IdentityError

        try:
            session = identity.login(email, password)
        except IdentityError as exc:
            return HTMLResponse(views.login(error=str(exc), next_url=next,
                                            entra=_entra_client() is not None), 401)
        return _set_cookie(
            RedirectResponse(_inside(next), status_code=303), session.token)

    def _inside(target: str) -> str:
        """Only follow in-site paths. Letting an arbitrary next through is an open redirect."""
        return target if target.startswith("/") and not target.startswith("//") else "/"

    @app.get("/auth/entra")
    def entra_start(next: str = "/"):
        """Clicking "sign in with your company account". state / nonce / verifier are all stored server-side and deleted after a single use."""
        client = _entra_client()
        if client is None:
            return RedirectResponse("/login", status_code=303)
        state, nonce, verifier = identity.start_oidc_flow(_inside(next))
        from framework_reader.identity.entra import EntraError, challenge_for

        try:
            url = client.authorize_url(
                state=state, nonce=nonce, challenge=challenge_for(verifier))
        except EntraError as exc:
            return HTMLResponse(views.refused(
            "Cannot reach the sign-in service", str(exc)), 502)
        return RedirectResponse(url, status_code=303)

    @app.get("/auth/entra/callback")
    def entra_callback(code: str = "", state: str = "", error: str = "",
                       error_description: str = ""):
        from framework_reader.identity.entra import EntraError
        from framework_reader.identity.store import IdentityError

        def refuse(message: str, hint: str = "") -> HTMLResponse:
            return HTMLResponse(views.refused("Sign-in did not complete", message, hint), 400)

        client = _entra_client()
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if error:
            # Entra itself said no (not being assigned the App Role is the most common case).
            # Show its error code verbatim — the administrator needs it to investigate inside Entra.
            return refuse(f"The company sign-in service refused this sign-in: {error}",
                          error_description or "Show this message to an administrator.")
        flow = identity.take_oidc_flow(state)
        if flow is None:
            # Unrecognised state = this callback is not the flow we started. Pairing someone
            # else's code with your own state could sign you into their account.
            return refuse(
                "This sign-in has expired, or it was not started from here.",
                "Go back to the sign-in page and start again.")
        try:
            tokens = client.exchange(code=code, verifier=flow["verifier"])
            claims = client.verify_id_token(
                tokens.get("id_token", ""), nonce=flow["nonce"])
            session = identity.sign_in_entra(claims)
        except (EntraError, IdentityError) as exc:
            return refuse(str(exc),
                          "Go back to the sign-in page and try again.")
        return _set_cookie(
            RedirectResponse(flow["next_url"] or "/", status_code=303),
            session.token)

    @app.get("/logout")
    @app.post("/logout")
    def logout(request: Request):
        token = request.cookies.get(COOKIE, "")
        if token:
            identity.logout(token)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/invite/{token}", response_class=HTMLResponse)
    def invite_page(token: str, error: str = ""):
        pending = identity.peek_invite(token)
        if pending is None:
            return HTMLResponse(views.refused(
                "Invalid invitation",
                "This invitation link is invalid or has expired.",
                "Ask an administrator to send a new one with fr account invite."), 404)
        return HTMLResponse(views.invite(
            token, pending["email"], pending["role"], error=error))

    @app.post("/invite/{token}")
    def invite_submit(
        token: str, password: str = Form(""), again: str = Form(""),
        display_name: str = Form(""),
    ):
        from framework_reader.identity.store import IdentityError

        pending = identity.peek_invite(token)
        if pending is None:
            return HTMLResponse(views.refused(
                "Invalid invitation",
                "This invitation link is invalid or has expired."), 404)

        def back(message: str) -> HTMLResponse:
            return HTMLResponse(views.invite(
                token, pending["email"], pending["role"], error=message), 400)

        if password != again:
            return back("The two passphrase entries do not match.")
        if len(password) < 12:
            return back("The passphrase must be at least 12 characters. It is the key to all your compliance material.")
        try:
            account = identity.accept_invite(
                token, password=password, display_name=display_name)
        except IdentityError as exc:
            return back(str(exc))
        identity.log("account.created", actor=account.email,
                     detail=f"created via invitation, role {pending['role']}")
        session = identity.start_session(account)
        return _set_cookie(RedirectResponse("/", status_code=303), session.token)

    @app.get("/", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def home(roll: int = 0):
        """Search workbench. Three things: the search box, frequently searched, three to learn today.

        Do not put another "built-in frameworks + my imports" catalogue page on / — that is
        /frameworks' job. This page answers "what did you come here to do", not "what do you
        already have". `roll` is the "shuffle" batch number: 0 is the default three by date,
        ≥1 swaps in another set, and the same batch stays stable for the day."""
        from datetime import date

        from framework_reader.query.daily import daily_controls
        from framework_reader.userframework.search_stats import top as popular_ids

        reader = api()
        popular = []
        for cid in popular_ids(_user_db()):
            view = reader.get_control(cid)
            if view is None:
                continue
            popular.append({
                "id": view.id, "short": _short(view.id), "label": view.label,
            })
        # Number of drafts awaiting confirmation. Review is the signer's daily entry point, so
        # it sits on the first page they open each day; when nothing is left, render nothing —
        # a quiet page is more useful than a badge permanently stuck at zero.
        review = reader.pending_review()
        return HTMLResponse(views.home(
            popular=popular,
            daily=daily_controls(reader, today=date.today(), roll=roll),
            review={"count": len(review)} if review else None,
            roll=roll,
        ))

    @app.get("/f/{framework_id}", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def framework(framework_id: str):
        reader = api()
        view = reader.get_framework(framework_id)
        if view is None:
            return HTMLResponse(views.page("Not found",
                                           "<p>No such framework.</p>"), 404)
        controls = [
            {
                "id": c.id, "short": _short(c.id), "label": c.label,
                "has_interp": c.has_interpretation,
                "confirmed": c.interpretation_state == "confirmed",
            }
            for c in reader.control_summaries(framework_id)
        ]
        # Web drafting always overlays into the user database as the working copy — imported
        # and built-in alike; nothing enters git. views.framework reads pending to decide
        # whether to draw "N controls to draft".
        pending = sum(1 for c in controls if not c["has_interp"])
        return HTMLResponse(views.framework(view, controls, pending))

    @app.get("/f/{framework_id}/supersession", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def supersession_overview(framework_id: str):
        reader = api()
        view = reader.get_framework(framework_id)
        if view is None:
            return HTMLResponse(views.page("Not found",
                                           "<p>No such framework.</p>"), 404)
        return HTMLResponse(
            views.supersession_page(view, reader.supersessions_in(framework_id)))

    @app.post("/c/{control_id}/inherit")
    @needs(perm.INTERPRETATION_DRAFT)
    async def inherit_interpretation(control_id: str, request: Request):
        from framework_reader.interpret.user_store import UserInterpretationStore
        from framework_reader.userframework.inherit import InheritDenied
        from framework_reader.userframework.inherit import inherit as do_inherit

        form = await request.form()
        target = (form.get("target") or "").strip()
        if not target:
            return HTMLResponse(views.page(
                "Missing target", "<p>No target control was specified.</p>"), 400)
        try:
            do_inherit(control_id, target, UserInterpretationStore(_user_db()), api())
        except InheritDenied as exc:
            return HTMLResponse(views.page(
                "Cannot inherit", f'<p class="note">{exc}</p>'), 409)
        identity.log("interpretation.inherit", actor=_who(request) or _local_user(),
                     detail=f"{control_id} -> {target}")
        return RedirectResponse(f"/c/{target}", status_code=303)

    @app.get("/c/{control_id}", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def control(control_id: str):
        reader = api()
        view = reader.get_control(control_id)
        if view is None:
            return HTMLResponse(views.page("Not found",
                                           "<p>No such control.</p>"), 404)
        mappings = [
            {"short": _short(n.control_id), "label": n.label}
            for n in reader.neighbors(control_id, exportable_only=True)
        ]
        signer, signed_at = _signature(reader, view.framework_id, control_id)
        framework = reader.get_framework(view.framework_id)
        return HTMLResponse(views.control(
            view, reader.interpretation(control_id),
            reader.interpretation_state(control_id), mappings,
            body=reader.control_body(control_id),
            # CSF's body text comes from the official label; user-pasted/imported text is labelled "Your imported text".
            body_label=("Official text" if reader.body_is_official(control_id)
                        else "Your imported text"),
            # Built-in frameworks can be edited now too (see the `_mine_or_400` notes).
            # Fields the user changed override the content package's version field by field; see
            # the merged view in query/api.py.
            editable=True,
            signer=signer, signed_at=signed_at,
            framework_name=getattr(framework, "name", ""),
            chat=_chat_store().history(control_id),
            inherited_from=_inherited_from(control_id),
            superseded=[
                {"control_id": n.control_id, "label": n.label,
                 "relation": n.relation}
                for n in reader.superseded_by(control_id)
            ],
        ))

    def _signature(reader: QueryAPI, framework_id: str, control_id: str):
        """Who signed, and when. Only the user database can hold this — the content package has no such column."""
        if not _is_mine(reader.get_framework(framework_id)):
            return "", ""
        from framework_reader.interpret.user_store import UserInterpretationStore

        store = UserInterpretationStore(_user_db())
        if not store.exists(control_id):
            return "", ""
        provenance = store.load(control_id).provenance
        if not provenance.confirmed_by:
            return "", ""
        when = provenance.confirmed_at
        return provenance.confirmed_by, when.strftime("%Y-%m-%d %H:%M") if when else ""

    def _inherited_from(control_id: str) -> str:
        """Which older control this interpretation was inherited from. Inherited artefacts land
        in the user database, so this reads the user database as well.

        It deliberately ignores `_is_mine`: inheritance is open to built-in frameworks (pure
        copying costs nothing and bypasses no sign-off), and blocking the display would hide
        half the value of the official mappings.
        """
        from framework_reader.interpret.user_store import UserInterpretationStore

        store = UserInterpretationStore(_user_db())
        if not store.exists(control_id):
            return ""
        return store.load(control_id).provenance.inherited_from or ""

    def _mine_or_400(control_id: str):
        """Whether this control exists. Returns (view, error response).

        **This used to block built-in frameworks too**, on the stated grounds that "copyrighted
        original text must not leave the network". That rationale does not hold up:

        - NIST CSF 2.0 and 800-53 are tier A (US government works, public domain)
        - ISO 27002 is tier C, but what the database stores is a **self-written** label
          (`label_is_original=0`), not ISO's original text
        - the `original_text` table has 0 rows — copyrighted original text never entered the
          database at all

        The outbound guard (`PayloadGuard` uses `original_text` as its blocklist) stays in place
        as the safety net: if C/D original text ever does enter the database, it will catch it.
        But using "is it built-in" as the test is wrong, and the price is not being able to ask
        a single question about CSF and 800-53, which the team uses ninety percent of the time.

        The name stays unchanged: too many call sites, and its meaning today is exactly "does
        this one exist".
        """
        reader = api()
        view = reader.get_control(control_id)
        if view is None:
            return None, HTMLResponse(
                views.page("Not found", "<p>No such control.</p>"), 404)
        return view, None

    @app.get("/c/{control_id}/edit/{field}", response_class=HTMLResponse)
    @needs(perm.INTERPRETATION_WRITE)
    def edit_field_page(control_id: str, field: str):
        from framework_reader.interpret.render import FIELD_LABELS

        view, refused = _mine_or_400(control_id)
        if refused is not None:
            return refused
        labels = dict(FIELD_LABELS)
        if field not in labels:
            return HTMLResponse(views.page(
                "Not found", f'<p>No "{field}" field exists.</p>'), 404)
        current = (api().interpretation(control_id).get(field) or {}).get("value")
        return HTMLResponse(views.edit_field(
            view, field, labels[field], current))

    def _size_of(value) -> str:
        """How "big" a field is. **Record the size only, never the text** — the audit log is
        append-only, and pouring policy text into it amounts to making a permanent copy: it can
        never be deleted and rides along in every export.
        """
        if value in (None, "", [], {}):
            return "cleared"
        if isinstance(value, (list, tuple)):
            return f"{len(value)} items"
        if isinstance(value, dict):
            return f"{sum(len(str(v)) for v in value.values())} chars"
        return f"{len(str(value))} chars"

    def _log_field(request: Request, event: str, control_id: str,
                   field: str, before, after) -> None:
        from framework_reader.interpret.render import FIELD_LABELS

        label = dict(FIELD_LABELS).get(field, field)
        identity.log(event, actor=_who(request),
                     detail=f"{control_id} · {label} ({field})"
                            f": {_size_of(before)} -> {_size_of(after)}")

    @app.post("/c/{control_id}/edit/{field}")
    @needs(perm.INTERPRETATION_WRITE)
    async def edit_field_save(control_id: str, request: Request, field: str):
        from framework_reader.interpret.authoring import write_field
        from framework_reader.interpret.user_store import UserInterpretationStore

        _view, refused = _mine_or_400(control_id)
        if refused is not None:
            return refused
        form = await request.form()
        try:
            value = _parse_field(field, form)
        except ValueError as exc:
            return HTMLResponse(views.page("Cannot save", f"<p>{exc}</p>"), 404)
        before = (api().interpretation(control_id).get(field) or {}).get("value")
        write_field(UserInterpretationStore(_user_db()), control_id, field, value)
        _log_field(request, "interpretation.edit", control_id, field, before, value)
        return RedirectResponse(f"/c/{control_id}", status_code=303)

    # ---------- Control body text (built-in controls get an override layer pasted on; imported controls edit their own row) ----------

    def _editable_control(control_id: str):
        """Admission for body editing: the control only has to exist — built-in and imported
        are both allowed through.

        _mine_or_400 checks exactly "does this control exist", which is precisely what is needed.
        A built-in control's body is written to the control_body_override overlay (user database)
        and the official baseline in the content database is not moved by a single byte; the
        original_text tombstone stays as it is — pasted-in original text goes into the user's
        own database.
        """
        return _mine_or_400(control_id)

    @app.get("/c/{control_id}/edit-body", response_class=HTMLResponse)
    @needs(perm.INTERPRETATION_WRITE)
    def edit_body_page(control_id: str):
        from framework_reader.userframework.store import UserFrameworkStore

        view, refused = _editable_control(control_id)
        if refused is not None:
            return refused
        body = UserFrameworkStore(_user_db()).load_body(control_id) or ""
        return HTMLResponse(views.edit_body(view, body))

    @app.post("/c/{control_id}/edit-body/ai")
    @needs(perm.INTERPRETATION_WRITE)
    async def edit_body_ai(control_id: str, request: Request):
        """AI revises the body: it only produces a proposal echoed back into the edit box,
        **not a single character is written to the database** — writes always happen on "Save",
        behind the same gate as field rewrites."""
        view, refused = _editable_control(control_id)
        if refused is not None:
            return refused
        form = await request.form()
        current = (form.get("body") or "").strip()
        instruction = (form.get("instruction") or "").strip()
        if not current:
            return HTMLResponse(views.page(
                "Nothing to rewrite",
                "<p>This control has no body text yet; AI will not invent policy from scratch. Paste a passage in first, "
                "then have it revised.</p>"), 400)
        if not instruction:
            return HTMLResponse(views.edit_body(
                view, current,
                note="Say how it should change first, then click Have AI revise."), 400)
        over = _charge(request, 1, f"{control_id} · body rewrite")
        if over is not None:
            return over
        run = body_rewrite_runner or _default_body_rewriter
        try:
            proposal = run(control_id, instruction, current)
        except Exception as exc:  # noqa: BLE001
            return HTMLResponse(views.edit_body(
                view, current,
                note=f"This revision failed ({exc}). Try again."), 502)
        return HTMLResponse(views.edit_body(
            view, proposal,
            note="AI produced a revision per your instruction. Review it, then click Save; if you do not like it, "
                 "edit the box directly or ask again."))

    @app.post("/c/{control_id}/edit-body")
    @needs(perm.INTERPRETATION_WRITE)
    async def edit_body_save(control_id: str, request: Request):
        from framework_reader.userframework.store import UserFrameworkStore

        view, refused = _editable_control(control_id)
        if refused is not None:
            return refused
        form = await request.form()
        body = (form.get("body") or "").replace("\r\n", "\n").strip()
        store = UserFrameworkStore(_user_db())
        before = store.load_body(control_id) or ""
        if body == before.strip():
            return RedirectResponse(f"/c/{control_id}", status_code=303)
        store.update_body(control_id, body)
        # The audit log records the size, not the text — same reason as field edits: the audit
        # log is append-only, and pouring the body into it amounts to a permanent copy that can
        # never be deleted.
        identity.log("control.body_edit", actor=_who(request) or _local_user(),
                     detail=f"{control_id}: {len(before)} chars -> {len(body)} chars")
        return RedirectResponse(f"/c/{control_id}", status_code=303)

    def _charge(request: Request, controls: int, what: str):
        """Three gates before any money is spent: per person per hour, per organisation per
        month, and how many jobs may run at once.

        Returning None means allowed; otherwise the refusal page is returned. **A refusal is
        not charged** — charging on refusal would make the second attempt even more likely to
        be refused.
        """
        from framework_reader.web import jobs

        try:
            models_config.charge_draft(
                _who(request) or _local_user(), controls, what=what,
                running_jobs=jobs.running_count())
        except BudgetError as exc:
            return HTMLResponse(views.refused(
                "Not allowed this time", str(exc),
                'The limits are on the "Models and keys" page and can be adjusted by an administrator.'), 429)
        return None

    @app.post("/c/{control_id}/draft")
    @needs(perm.INTERPRETATION_DRAFT)
    def draft_one(control_id: str, request: Request):
        from framework_reader.web import jobs

        view, refused = _mine_or_400(control_id)
        if refused is not None:
            return refused
        over = _charge(request, 1, control_id)
        if over is not None:
            return over
        # Log at **the moment it starts**, not after it finishes. Logged on completion, a
        # crashed run would leave no trace that someone pressed this button and spent this money.
        identity.log("interpretation.draft", actor=_who(request),
                     detail=f"{control_id}: have AI draft all seven fields")
        jobs.start(control_id, 1, lambda key: run_draft(key, library))
        return RedirectResponse(f"/c/{control_id}/draft", status_code=303)

    @app.get("/c/{control_id}/draft", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def draft_one_status(control_id: str):
        from framework_reader.web import jobs

        reader = api()
        view = reader.get_control(control_id)
        if view is None:
            return HTMLResponse(views.page("Not found",
                                           "<p>No such control.</p>"), 404)
        job = jobs.get(control_id)
        if job is None:
            return RedirectResponse(f"/c/{control_id}", status_code=303)
        return HTMLResponse(views.draft_status(
            view.label, f"/c/{control_id}", job, crumb=view.framework_id))

    @app.get("/c/{control_id}/rewrite/{field}", response_class=HTMLResponse)
    @needs(perm.INTERPRETATION_DRAFT)
    def rewrite_page(control_id: str, field: str, error: str = ""):
        from framework_reader.interpret.render import FIELD_LABELS

        view, refused = _mine_or_400(control_id)
        if refused is not None:
            return refused
        labels = dict(FIELD_LABELS)
        if field not in labels:
            return HTMLResponse(views.page(
                "Not found", f'<p>No "{field}" field exists.</p>'), 404)
        current = (api().interpretation(control_id).get(field) or {}).get("value")
        return HTMLResponse(views.rewrite_field_page(
            view, field, labels[field], current, error=error))

    @app.post("/c/{control_id}/rewrite/{field}")
    @needs(perm.INTERPRETATION_DRAFT)
    async def rewrite_save(control_id: str, request: Request, field: str):
        from framework_reader.interpret.authoring import write_field
        from framework_reader.interpret.model import Basis
        from framework_reader.interpret.render import FIELD_LABELS
        from framework_reader.interpret.user_store import UserInterpretationStore

        view, refused = _mine_or_400(control_id)
        if refused is not None:
            return refused
        labels = dict(FIELD_LABELS)
        if field not in labels:
            return HTMLResponse(views.page(
                "Not found", f'<p>No "{field}" field exists.</p>'), 404)

        form = await request.form()
        instruction = str(form.get("instruction", "")).strip()
        current = (api().interpretation(control_id).get(field) or {}).get("value")

        def back(message: str) -> HTMLResponse:
            return HTMLResponse(views.rewrite_field_page(
                view, field, labels[field], current, error=message))

        if not instruction:
            return back("Write an instruction first; with no instruction there is nothing to rewrite.")
        # Rewrites and drafting share one gate: what they have in common is not "both use AI"
        # but **both spend money**.
        over = _charge(request, 1, f"{control_id} · {field}")
        if over is not None:
            return over
        try:
            value = run_rewrite(control_id, field, instruction)
        except Exception as exc:                              # noqa: BLE001
            # Wrong shape, missing key, model misbehaving — nothing is persisted; the failure is
            # handed straight back so the user can submit it again.
            return back(f"Rewrite failed: {exc}")
        # The instruction came from the user and the words were written by the model, so mark it inferred.
        write_field(UserInterpretationStore(_user_db()), control_id, field, value,
                    basis=Basis.INFERRED)
        # **Kept as a separate event from manual edits.** Who wrote what must be tellable apart;
        # that is this product's foundation. The instruction itself does not enter the log —
        # it is the user's own typing and may name internal company systems.
        _log_field(request, "interpretation.rewrite", control_id, field,
                   current, value)
        return RedirectResponse(f"/c/{control_id}", status_code=303)

    # ---------- AI chat on the control page ----------
    #
    # **Enabled only on frameworks the user imported themselves.** Built-in frameworks' body
    # text is Tier C/D copyrighted original, and not a word of it may leave the network
    # (main spec §9). The test is `_mine_or_400`, the same one as for editing interpretations.

    def _chat_store():
        from framework_reader.userframework.chat import ChatStore

        return ChatStore(_user_db())

    def _ask_model(control_id: str, message: str, history) -> str:
        from framework_reader.llm.client import Message
        from framework_reader.llm.guard import PayloadGuard
        from framework_reader.prompts import load_prompt

        reader = api()
        view = reader.get_control(control_id)
        fields = reader.interpretation(control_id)
        lines = [
            f"Control: {view.id} {view.label}",
            f"Body text: {reader.control_body(control_id) or '(none)'}",
            "",
            "The seven fields currently say:",
        ]
        for name, value in sorted(fields.items()):
            shown = (value or {}).get("value")
            lines.append(f"- {name}: {shown if shown else '(empty)'}")
        from framework_reader.userframework.chat import mapping_lines
        lines += ["", "The mappings for this control in the official mapping (when citing, copy IDs and sources verbatim; "
                  "do not invent entries that are not in the list):"]
        lines += mapping_lines(reader.neighbors(control_id, exportable_only=True))
        if history:
            lines += ["", "Earlier in this conversation:"]
            lines += [f"{'User' if h.role == 'user' else 'AI'}: {h.text}"
                      for h in history]
        lines += ["", f"The user now says: {message}"]

        client, model = _extractor_client()
        # The guard uses the **real** copyrighted-original list, not an empty guard: on this
        # path the body text comes from the user's own framework, but the guard is the last line
        # of defence and should not be withdrawn just because "there shouldn't be any".
        guard = PayloadGuard(reader.forbidden_outbound_texts())
        from framework_reader.llm.guard import GuardedClient

        guarded = GuardedClient(client, guard)
        return guarded.complete(
            load_prompt("clause_chat"),
            [Message(role="user", content="\n".join(lines))],
            model=model, max_tokens=4096)

    def _one_turn(request: Request, control_id: str, said: str):
        """Runs one conversation turn, returns (text for the person, proposal, turn_id).

        The form path and the floating widget's JSON path **share this one** — write the two
        paths separately and sooner or later one of them has the gate and the other does not.
        """
        from framework_reader.userframework.chat_reply import parse_reply

        store = _chat_store()
        store.say(control_id, role="user", text=said, actor=_who(request))
        history = store.recent(control_id, turns=CHAT_CONTEXT_TURNS)
        run = chat_runner or _ask_model
        try:
            raw = run(control_id, said, history[:-1])
        except Exception as exc:                        # noqa: BLE001
            text = f"This question did not go through ({exc}). Try again."
            return text, [], store.say(control_id, role="ai", text=text)
        reply, updates, error = parse_reply(raw)
        text = error or reply
        turn_id = store.say(control_id, role="ai", text=text,
                            proposal=[] if error else updates)
        return text, ([] if error else updates), turn_id

    @app.post("/c/{control_id}/chat")
    @needs(perm.INTERPRETATION_DRAFT)
    async def clause_chat(control_id: str, request: Request,
                          message: str = Form("")):
        _view, refused = _mine_or_400(control_id)
        if refused is not None:
            return refused
        said = message.strip()
        if not said:
            return RedirectResponse(f"/c/{control_id}", status_code=303)
        # One question = one call = one charge. Same ledger and same gate as drafting.
        over = _charge(request, 1, f"{control_id} · chat")
        if over is not None:
            return over
        _one_turn(request, control_id, said)
        return RedirectResponse(f"/c/{control_id}", status_code=303)

    @app.post("/c/{control_id}/chat.json")
    @needs(perm.INTERPRETATION_DRAFT)
    async def clause_chat_json(control_id: str, request: Request,
                               message: str = Form(""), quote: str = Form("")):
        """For the floating widget. **It writes not a single character of any interpretation** —
        writes still go through the form path above, which carries the pre-check, the audit
        trail, and the "nothing is written until you nod" gate.
        """
        from framework_reader.interpret.render import FIELD_LABELS

        _view, refused = _mine_or_400(control_id)
        if refused is not None:
            return JSONResponse({"reply": "No such control.", "turn_id": "",
                                 "fields": []}, 404)
        said = message.strip()
        if not said:
            return JSONResponse({"reply": "", "turn_id": "", "fields": []})
        # The selected passage is context for this question; leave it out and the selection was wasted.
        if quote.strip():
            said = f'About this passage: "{quote.strip()}"\n{said}'
        over = _charge(request, 1, f"{control_id} · chat")
        if over is not None:
            return JSONResponse(
                {"reply": "Not allowed this time: the spending limit was hit.", "turn_id": "", "fields": []})
        reply, updates, turn_id = _one_turn(request, control_id, said)
        labels = dict(FIELD_LABELS)
        return JSONResponse({
            "reply": reply, "turn_id": turn_id,
            "fields": [labels.get(u["field"], u["field"]) for u in updates],
        })

    @app.post("/c/{control_id}/chat/{turn_id}/apply")
    @needs(perm.INTERPRETATION_WRITE)
    def clause_chat_apply(control_id: str, turn_id: str, request: Request):
        """**Only at this step does what the model said enter the database.** In between stands
        this human click."""
        from framework_reader.interpret.authoring import write_field
        from framework_reader.interpret.model import Basis
        from framework_reader.interpret.user_store import UserInterpretationStore

        _view, refused = _mine_or_400(control_id)
        if refused is not None:
            return refused
        store = _chat_store()
        turn = store.turn(turn_id)
        if turn is None or turn.control_id != control_id or not turn.proposal:
            return RedirectResponse(f"/c/{control_id}", status_code=303)
        # Refreshing the page resends the POST. Writing to the database twice and logging two
        # audit entries is the hardest kind of duplicate to hunt down.
        if not store.mark_applied(turn_id):
            return RedirectResponse(f"/c/{control_id}", status_code=303)

        written = UserInterpretationStore(_user_db())
        for update in turn.proposal:
            field = update["field"]
            before = (api().interpretation(control_id).get(field) or {}).get("value")
            # The instruction came from a human and the words were written by the model, so mark it inferred.
            write_field(written, control_id, field, update["value"],
                        basis=Basis.INFERRED)
            _log_field(request, "interpretation.chat", control_id, field,
                       before, update["value"])
        return RedirectResponse(f"/c/{control_id}", status_code=303)

    @app.post("/c/{control_id}/confirm")
    @needs(perm.INTERPRETATION_CONFIRM)
    def confirm_control(control_id: str, request: Request, next: str = Form("")):
        from framework_reader.interpret.authoring import confirm
        from framework_reader.interpret.user_store import UserInterpretationStore

        _view, refused = _mine_or_400(control_id)
        if refused is not None:
            return refused

        # The signer is **the person who is signed in**, not the system account running the
        # server. With getpass.getuser(), every signature in the organisation would carry the
        # same name — yet "a named person stands behind this text" is what this product is
        # built on.
        signer = _who(request) or _local_user()
        try:
            confirm(UserInterpretationStore(_user_db()), control_id, signer=signer)
        except FileNotFoundError:
            return HTMLResponse(views.page(
                "Nothing to confirm yet",
                "<h2>This control has no interpretation</h2>"
                '<p class="note">Draft it or write a few fields first, then come back to confirm.</p>'
                f'<p><a href="/c/{control_id}">Back to the control</a></p>',
            ), 400)
        # Confirmation is the product's core action; "who claimed it" must be traceable. Design §4.4
        identity.log("interpretation.confirm", actor=signer, detail=control_id)
        # A confirmation clicked in the review queue jumps straight to the next control —
        # dipping into each control page and back, a thousand drafts would grind a person down.
        # Signing itself did not get faster; only finding the next one did.
        if next:
            return RedirectResponse("/review", status_code=303)
        return RedirectResponse(f"/c/{control_id}", status_code=303)

    @app.get("/review", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def review_queue(after: str = "", before: str = ""):
        """Review queue: one AI draft at a time, confirm or skip, page through with the keyboard.

        Signing must happen one control at a time (batch one-click confirmation would let "every
        control passes a human eye" out the front door), but "find the next thing to look at"
        should not cost a human their time. The confirm button POSTs to the existing
        /c/{id}/confirm with next=1 to advance — there is exactly one copy of the signing logic.
        """
        reader = api()
        pending = reader.pending_review()
        ids = [c.id for c in pending]
        if not ids:
            return HTMLResponse(views.review(None, remaining=0, total=0))
        current = _queue_pick(ids, after=after, before=before)
        view = next(c for c in pending if c.id == current)
        framework = reader.get_framework(view.framework_id)
        remaining = len(ids) - 1
        return HTMLResponse(views.review(
            {
                "id": view.id, "short": _short(view.id), "label": view.label,
                "framework": framework.name if framework else view.framework_id,
                "state": reader.interpretation_state(view.id),
                "fields": reader.interpretation(view.id),
                "body": reader.control_body(view.id),
                "body_label": "Official text" if reader.body_is_official(view.id)
                else "Your imported text",
            },
            remaining=remaining, total=remaining + 1,
        ))

    def _queue_pick(ids: list[str], *, after: str = "", before: str = "") -> str:
        """Positioning within the queue. after/before is the previous page's current item —
        after skipping or confirming, continue from beside it, wrapping around to the other end
        at the edge. If the reference cannot be found (stale link, control deleted), return to
        the head of the queue."""
        if before:
            older = [i for i in ids if i < before]
            if older:
                return older[-1]
        if after:
            newer = [i for i in ids if i > after]
            if newer:
                return newer[0]
            if after in ids:
                return ids[0]
        return ids[0]

    def _practice_of(reader: QueryAPI, control_id: str) -> dict:
        raw = reader.interpretation(control_id)
        return (raw.get("practice") or {}).get("value") or {}

    def _rows_for_assess(reader: QueryAPI, framework_id: str) -> tuple[list[dict], bool]:
        from framework_reader.assess.store import AssessStore

        store = AssessStore(_user_db())
        rows, maturity = [], False
        for ctl in reader.list_controls(framework_id, leaf_only=True):
            practice = _practice_of(reader, ctl.id)
            maturity = maturity or bool(practice)
            entry = store.get(ctl.id)
            if entry is None:
                answer, current = "", ""
            elif not entry.applicable:
                answer, current = "n", f"Not applicable · {entry.reason}"
            elif entry.level is not None:
                answer, current = str(entry.level), f"Level {entry.level}"
            else:
                answer = {"not started": "0", "in progress": "1",
                          "implemented": "2",
                          "implemented by a third party": "3"}.get(entry.status, "")
                current = entry.status
            rows.append({
                "id": ctl.id, "short": _short(ctl.id), "label": ctl.label,
                "practice": practice, "answer": answer, "current": current,
                "note": (entry.note or entry.reason) if entry else "",
            })
        return rows, maturity

    @app.get("/f/{framework_id}/assess", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def assess_page(framework_id: str):
        reader = api()
        view = reader.get_framework(framework_id)
        if view is None:
            return HTMLResponse(views.page("Not found",
                                           "<p>No such framework.</p>"), 404)
        rows, maturity = _rows_for_assess(reader, framework_id)
        return HTMLResponse(views.assess(view, rows, maturity))

    @app.post("/f/{framework_id}/assess")
    @needs(perm.ASSESSMENT_WRITE)
    def assess_save(
        framework_id: str,
        control_id: str = Form(...),
        answer: str = Form(""),
        note: str = Form(""),
    ):
        from framework_reader.assess.store import AssessStore

        reader = api()
        store = AssessStore(_user_db())
        if answer == "n":
            store.record(control_id, applicable=False, reason=note.strip())
        elif answer:
            if _practice_of(reader, control_id):
                store.record(control_id, level=int(answer), note=note.strip())
            else:
                status = {"0": "not started", "1": "in progress",
                          "2": "implemented",
                          "3": "implemented by a third party"}[answer]
                store.record(control_id, status=status, note=note.strip())
        return RedirectResponse(
            f"/f/{framework_id}/assess#{_short(control_id)}", status_code=303
        )

    @app.get("/f/{framework_id}/gap", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def gap_page(framework_id: str):
        from framework_reader.assess.report import build_gap, render_gap
        from framework_reader.assess.remediation import RemediationStore
        from framework_reader.assess.store import AssessStore

        reader = api()
        view = reader.get_framework(framework_id)
        if view is None:
            return HTMLResponse(views.page("Not found",
                                           "<p>No such framework.</p>"), 404)
        controls = reader.list_controls(framework_id, leaf_only=True)
        content = {
            c.id: {
                "label": c.label,
                "practice": _practice_of(reader, c.id),
                "evidence": (reader.interpretation(c.id).get("evidence") or {}).get("value")
                or "",
            }
            for c in controls
        }
        entries = [a for a in AssessStore(_user_db()).all() if a.control_id in content]
        if not entries:
            # Not a single self-assessment yet: this page has no content to show, only a next step to point to.
            return HTMLResponse(views.gap(
                view, "", to_assess=len(controls)))
        report = build_gap(entries, content, total=len(controls))
        tracked = RemediationStore(_user_db())
        untracked = sum(
            1 for i in report.items if tracked.get(i.control_id) is None)
        text = render_gap(report)
        changes = [
            {**c, "label": _gap_label(content, c["control_id"])}
            for c in AssessStore(_user_db()).changes()
            if c["control_id"] in content
        ]
        return HTMLResponse(views.gap(
            view, text, changes=changes, plan=untracked))

    def _gap_label(content: dict, control_id: str) -> str:
        return content.get(control_id, {}).get("label", "")

    @app.get("/f/{framework_id}/remediation", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def remediation_page(framework_id: str):
        from framework_reader.assess.remediation import RemediationStore
        from framework_reader.assess.store import AssessStore

        reader = api()
        view = reader.get_framework(framework_id)
        if view is None:
            return HTMLResponse(views.page("Not found",
                                           "<p>No such framework.</p>"), 404)
        store = RemediationStore(_user_db())
        assess = AssessStore(_user_db())
        rows = []
        for item in store.all():
            control = reader.get_control(item.control_id)
            if control is None or control.framework_id != framework_id:
                continue
            entry = assess.get(item.control_id)
            practice = _practice_of(reader, item.control_id)
            rows.append({
                "id": item.control_id,
                "short": _short(item.control_id),
                "label": control.label,
                "owner": item.owner, "due": item.due,
                "state": item.state, "note": item.note,
                "updated_at": item.updated_at,
                "current": (
                    "Not applicable" if entry and not entry.applicable
                    else f"Level {entry.level}" if entry and entry.level is not None
                    else entry.status if entry and entry.status else "Not assessed yet"),
                "next_step": str(practice.get(str(
                    (entry.level or 0) + 1), "")) if entry and entry.level is not None else "",
            })
        return HTMLResponse(views.remediation(view, rows))

    @app.post("/f/{framework_id}/remediation")
    @needs(perm.ASSESSMENT_WRITE)
    def remediation_update(
        framework_id: str,
        control_id: str = Form(""),
        ref: str = Form(""),
        state: str = Form(""),
        owner: str = Form(""),
        due: str = Form(""),
        note: str = Form(""),
    ):
        """One form per row: if control_id is already tracked this updates it, otherwise it
        backfills a new entry.

        The backfill form collects the short number (`4.1`); it is reassembled into the full id
        here — the stable prefix of a control number is the framework id, see spec §8②. The
        authorisation matrix's cell-by-cell tests hit every route with empty forms — when
        control_id is empty, return straight back to the register page instead of showing a 500."""
        from framework_reader.assess.remediation import RemediationStore, STATES

        store = RemediationStore(_user_db())
        if ref and not control_id:
            control_id = f"{framework_id}:{ref.strip()}"
        if control_id:
            control = api().get_control(control_id)
            if control is None or control.framework_id != framework_id:
                return HTMLResponse(views.page(
                    "Not found",
                    "<p>No such control under this framework.</p>"), 404)
            if store.get(control_id) is None:
                store.start(control_id)
            store.update(
                control_id,
                state=state if state in STATES else None,
                owner=owner or None, due=due or None, note=note or None,
            )
        return RedirectResponse(
            f"/f/{framework_id}/remediation", status_code=303)

    @app.post("/f/{framework_id}/remediation/remove")
    @needs(perm.ASSESSMENT_WRITE)
    def remediation_remove(framework_id: str, control_id: str = Form("")):
        from framework_reader.assess.remediation import RemediationStore

        if control_id:
            RemediationStore(_user_db()).remove(control_id)
        return RedirectResponse(
            f"/f/{framework_id}/remediation", status_code=303)

    @app.post("/f/{framework_id}/remediation/plan")
    @needs(perm.ASSESSMENT_WRITE)
    def remediation_plan(framework_id: str):
        """Creates entries in one go for every gap-report item not yet tracked. Items already
        tracked are left alone — repeated clicks on this button never wipe the owner and due
        date a person filled in."""
        from framework_reader.assess.remediation import RemediationStore
        from framework_reader.assess.report import build_gap
        from framework_reader.assess.store import AssessStore

        reader = api()
        view = reader.get_framework(framework_id)
        if view is None:
            return HTMLResponse(views.page("Not found",
                                           "<p>No such framework.</p>"), 404)
        controls = reader.list_controls(framework_id, leaf_only=True)
        content = {c.id: _practice_of(reader, c.id) for c in controls}
        entries = [a for a in AssessStore(_user_db()).all()
                   if a.control_id in content]
        store = RemediationStore(_user_db())
        gap_items = build_gap(
            entries,
            {c.id: {"label": c.label, "practice": content[c.id], "evidence": ""}
             for c in controls},
            len(controls),
        ).items
        for item in gap_items:
            if store.get(item.control_id) is None:
                store.start(item.control_id)
        return RedirectResponse(
            f"/f/{framework_id}/remediation", status_code=303)

    def _soa_rows(reader: QueryAPI, framework_id: str):
        from framework_reader.assess.soa import build_soa
        from framework_reader.assess.store import AssessStore

        controls = [
            (c.id, c.label) for c in reader.list_controls(framework_id, leaf_only=True)
        ]
        return build_soa(controls, AssessStore(_user_db()).all())

    @app.get("/f/{framework_id}/soa", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def soa_page(framework_id: str):
        reader = api()
        view = reader.get_framework(framework_id)
        if view is None:
            return HTMLResponse(views.page("Not found",
                                           "<p>No such framework.</p>"), 404)
        rows = [
            {
                "short": _short(r.control_id), "label": r.label,
                "applicable": ("TBD" if r.applicable is None
                               else "Applicable" if r.applicable else "Not applicable"),
                "reason": r.reason,
                "status": r.status or ("TBD" if r.applicable else ""),
                "note": r.note,
            }
            for r in _soa_rows(reader, framework_id)
        ]
        return HTMLResponse(views.soa(view, rows))

    @app.get("/f/{framework_id}/soa.csv")
    @needs(perm.REPORT_EXPORT)
    def soa_csv(framework_id: str):
        from framework_reader.assess.soa import render_soa_csv

        body = render_soa_csv(_soa_rows(api(), framework_id))
        return Response(
            content=body.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
            headers={"content-disposition": f'attachment; filename="soa-{framework_id}.csv"'},
        )

    @app.post("/f/{framework_id}/draft")
    @needs(perm.INTERPRETATION_DRAFT)
    def draft_start(framework_id: str, request: Request):
        from framework_reader.interpret.run import pending_controls
        from framework_reader.web import jobs

        view = api().get_framework(framework_id)
        if view is None:
            return HTMLResponse(views.page("Not found",
                                           "<p>No such framework.</p>"), 404)
        pending = pending_controls(db, framework_id, _user_db())
        if not pending:
            return RedirectResponse(f"/f/{framework_id}", status_code=303)
        # 800-53 has over a thousand leaf controls. The default is 300 per person per hour;
        # drafting them all in one click would hit the cap. This pass runs only what still fits
        # in the budget; click again when it finishes.
        room = models_config.remaining_draft(_who(request) or _local_user())
        batch = pending[:room] if room else pending
        over = _charge(request, len(batch), framework_id)
        if over is not None:
            return over
        library = _user_db()

        def go(key: str):
            try:
                return run_draft(key, library, only=batch)
            except TypeError:
                # The runner injected by tests only takes two arguments.
                return run_draft(key, library)

        jobs.start(framework_id, len(batch), go)
        return RedirectResponse(f"/f/{framework_id}/draft", status_code=303)

    @app.get("/f/{framework_id}/draft", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def draft_status(framework_id: str):
        from framework_reader.web import jobs

        view = api().get_framework(framework_id)
        if view is None:
            return HTMLResponse(views.page("Not found",
                                           "<p>No such framework.</p>"), 404)
        job = jobs.get(framework_id)
        if job is None:
            return RedirectResponse(f"/f/{framework_id}", status_code=303)
        return HTMLResponse(views.draft_status(
            view.name, f"/f/{framework_id}", job, crumb=view.id))

    def _framework_rows():
        """The two callers share one data set. The imported rows additionally carry the import time and the source filename."""
        from framework_reader.userframework.store import UserFrameworkStore

        reader = api()
        detail = {f.id: f for f in UserFrameworkStore(_user_db()).list_frameworks()}
        rows = []
        for item in _frameworks(reader):
            extra = detail.get(item["id"])
            rows.append({
                **item,
                "imported_at": (extra.imported_at.strftime("%Y-%m-%d %H:%M")
                                if extra else ""),
                "source_file": extra.source_file if extra else "",
            })
        return rows

    @app.get("/frameworks", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def frameworks_page():
        return HTMLResponse(views.frameworks(_framework_rows()))

    def _expand_query(query: str) -> str:
        """Only reached when the literal search found nothing. Sends just the user's sentence to the model; the control catalogue never goes out."""
        from framework_reader.llm.client import Message
        from framework_reader.llm.config import effective_registry
        from framework_reader.llm.guard import PayloadGuard
        from framework_reader.prompts import load_prompt

        registry, key_lookup = effective_registry(config=models_config)
        client = registry.build(
            "questioner", guard=PayloadGuard([]), key_lookup=key_lookup)
        return client.complete(
            load_prompt("search_expand"),
            [Message(role="user", content=query)],
            model=registry.role("questioner").model, max_tokens=512)

    @app.get("/search", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def search_page(request: Request, q: str = ""):
        from framework_reader.query.expand import hits_for, parse_expansion

        needle = q.strip()
        if not needle:
            return RedirectResponse("/", status_code=303)
        reader = api()
        found = reader.search(needle)
        via = "literal"
        expanded: list[str] = []
        note = ""
        if not found:
            if not views.may(perm.INTERPRETATION_DRAFT):
                note = ("Nothing found. Having AI look for close wording needs drafting permission.")
            else:
                over = _charge(request, 1, f"Search {needle[:40]}")
                if over is not None:
                    return over
                actor = _who(request) or _local_user()
                try:
                    raw = (search_runner(needle) if search_runner is not None
                           else _expand_query(needle))
                except Exception as exc:  # noqa: BLE001
                    models_config.refund_draft(actor, 1)
                    note = ("No literal hits. Wanted to have AI look for close wording, but the call failed "
                            f"({type(exc).__name__}).")
                else:
                    terms, ids, error = parse_expansion(raw)
                    if error:
                        models_config.refund_draft(actor, 1)
                        note = f"No literal hits. AI could not expand the query ({error})"
                    else:
                        found = hits_for(reader, terms, ids)
                        via = "ai"
                        expanded = list(dict.fromkeys([*ids, *terms]))
                        identity.log("search.expand", actor=actor,
                                     detail=needle[:80])
                        if not found:
                            note = ("Expanded the query like this and still nothing." if expanded
                                    else "AI returned no usable search terms.")
        rows = [
            {"id": h.id, "short": _short(h.id),
             "framework_id": h.framework_id, "label": h.label}
            for h in found
        ]
        if rows:
            from framework_reader.userframework.search_stats import record
            record(_user_db(), [r["id"] for r in rows])
        return HTMLResponse(views.search_results(
            needle, rows, via=via, expanded=expanded, note=note))

    @app.get("/mine")
    @needs(perm.CONTENT_READ)
    def mine_moved():
        """Bookmarks may still hold the old address. Do not let people run into a 404."""
        return RedirectResponse("/frameworks", status_code=303)

    def _deletable(framework_id: str):
        """(framework, error response). Only self-imported frameworks can be deleted — built-in ones ship with the content package."""
        from framework_reader.userframework.store import UserFrameworkStore

        mine = {f.id: f for f in UserFrameworkStore(_user_db()).list_frameworks()}
        if framework_id in mine:
            return mine[framework_id], None
        if api().get_framework(framework_id) is not None:
            return None, HTMLResponse(views.page(
                "Cannot delete", "<h2>Built-in frameworks cannot be deleted</h2>"
                '<p class="note">They ship with the content package; you did not import them. '
                "To change the set, rebuild the content package.</p>",
                crumb="My imports"), 400)
        return None, HTMLResponse(views.page(
            "Not found", "<p>No such framework.</p>", crumb="My imports"), 404)

    @app.get("/f/{framework_id}/delete", response_class=HTMLResponse)
    @needs(perm.FRAMEWORK_DELETE)
    def framework_delete_page(framework_id: str, error: str = ""):
        from framework_reader.userframework.store import UserFrameworkStore

        found, refused = _deletable(framework_id)
        if refused is not None:
            return refused
        cost = UserFrameworkStore(_user_db()).what_removing_costs(framework_id)
        return HTMLResponse(views.framework_delete(
            found, cost, error=error), 400 if error else 200)

    @app.post("/f/{framework_id}/delete")
    @needs(perm.FRAMEWORK_DELETE)
    def framework_delete(request: Request, framework_id: str,
                         confirm: str = Form("")):
        """**The ID must be typed out exactly.** Deleting takes every self-assessment and
        sign-off under this framework down with it, and one accidental click must not be able
        to destroy dozens of hours of work. Same rule as the `fr` delete commands.
        """
        from framework_reader.userframework.store import UserFrameworkStore

        found, refused = _deletable(framework_id)
        if refused is not None:
            return refused
        if confirm.strip() != framework_id:
            return framework_delete_page(
                framework_id,
                error="The ID does not match. To delete, type it exactly: this step is deliberately tedious, "
                      "and what is deleted cannot be recovered.")
        store = UserFrameworkStore(_user_db())
        cost = store.what_removing_costs(framework_id)
        store.remove(framework_id)
        identity.log("framework.delete", actor=_who(request),
                     detail=f"{framework_id}, along with {cost['controls']} controls, "
                            f"{cost['assessments']} self-assessments, "
                            f"{cost['confirmations']} sign-offs")
        return RedirectResponse("/mine", status_code=303)

    @app.get("/import", response_class=HTMLResponse)
    @needs(perm.FRAMEWORK_IMPORT)
    def import_page(error: str = "") -> str:
        return views.import_page(error=error)

    # Documents (.docx / .pdf / .txt / .md) go through the AI splitting pipeline; spreadsheets
    # go straight into the database.
    # See the 2026-08-25 AI import design §5.1
    _DOCUMENT_SUFFIXES = (".docx", ".pdf", ".txt", ".md", ".markdown")

    def _extractor_client():
        """Client for structure extraction. Empty guard: the payload is the user's own policy,
        not Tier C/D original text — same usage as the `fr llm check` probe. Design §6"""
        from framework_reader.llm.config import effective_registry
        from framework_reader.llm.guard import PayloadGuard

        registry, key_lookup = effective_registry(config=models_config)
        return (registry.build("extractor", guard=PayloadGuard([]),
                               key_lookup=key_lookup),
                registry.role("extractor").model)

    def _shape_table(request: Request, framework_id: str, name: str,
                     filename: str, sheets, fail):
        """The header could not be recognised: have the model take a look at what this table
        looks like. Design §5.1

        **Only reached after deterministic parsing has failed.** The header-on-the-first-row
        path is free, instantaneous, and never wrong; there is no reason to swap it for a model
        call.
        """
        from framework_reader.llm.config import BudgetError
        from framework_reader.llm.registry import MissingApiKeyError
        from framework_reader.userframework import table_ai
        from framework_reader.userframework.import_draft import ImportDraftStore
        from framework_reader.web import jobs

        # When the header cannot be recognised, say **why AI was not asked to help**. Unspoken,
        # the administrator sees only "no control number found in the header", even though
        # another route is open to them.
        if not views.may(perm.INTERPRETATION_DRAFT):
            return None, ("Having AI read this table's structure calls the model and spends the organization's money; "
                          "it needs the author role.")
        actor = _who(request)
        try:
            models_config.charge_draft(actor, 1, what=f"table shape {framework_id}",
                                       running_jobs=jobs.running_count())
        except BudgetError as exc:
            return None, f"AI could have read this table's structure, but {exc}"
        sample = table_ai.sample_sheets(sheets)
        try:
            if shape_runner is not None:
                raw = shape_runner(sample, client=None, model="")
            else:
                client, model = _extractor_client()
                from framework_reader.llm.client import Message
                from framework_reader.prompts import load_prompt

                raw = client.complete(
                    load_prompt("table_shape"),
                    [Message(role="user", content=sample)],
                    model=model, max_tokens=512)
        except Exception as exc:                     # noqa: BLE001
            models_config.refund_draft(actor, 1)
            return None, f"Wanted AI to read this table, but the call failed ({type(exc).__name__})."
        names = [n for n, _ in sheets]
        by_name = dict(sheets)
        shape, error = table_ai.parse_shape(raw)
        if shape is not None:
            # Validate whichever sheet the model pointed at; if it named none, use the first.
            chosen = by_name.get(shape.sheet, sheets[0][1] if sheets else [])
            shape, error = table_ai.validate_shape(shape, chosen, sheet_names=names)
        identity.log("framework.tableshape", actor=actor,
                     detail=f"{framework_id} <- {filename}, read as "
                            f"{shape.kind if shape else 'nothing recognisable'}")
        if shape is None:
            # The model could not read it either. Fall back to that human-readable error, so
            # the person can see what the model was actually looking at.
            return None, f"AI could not read this table's structure either ({error})."
        if shape.kind == "document":
            # A policy document got pasted into Excel. Forcing a column mapping onto it would
            # fabricate an entire bogus control list. If a worksheet was named, flatten only
            # that one; if not, flatten the whole workbook.
            flat = (table_ai.rows_to_text(by_name[shape.sheet])
                    if shape.sheet in by_name else table_ai.sheets_to_text(sheets))
            return _outline_upload(request, framework_id, name, filename,
                                   None, fail, text=flat), ""
        text, spans = table_ai.to_draft(by_name.get(shape.sheet,
                                                    sheets[0][1]), shape)
        draft_id = ImportDraftStore(_user_db()).create(
            framework_id=framework_id, name=name, source_text=text,
            spans=spans, problems=[], actor=actor)
        return RedirectResponse(f"/import/{draft_id}", status_code=303), ""

    def _outline_upload(request: Request, framework_id: str, name: str,
                        filename: str, data: bytes, fail, text: str | None = None):
        """Document import: extract text → pre-check → split → land in preview state.
        **Nothing is written to the framework library before confirmation.**"""
        from framework_reader.llm.config import BudgetError, effective_registry
        from framework_reader.llm.guard import PayloadGuard
        from framework_reader.llm.registry import MissingApiKeyError
        from framework_reader.userframework import outline as outline_mod
        from framework_reader.userframework.extract import (
            UnsupportedDocument, extract,
        )
        from framework_reader.userframework.import_draft import ImportDraftStore
        from framework_reader.web import jobs

        # Document import calls the model and spends the organisation's money, so its bar sits
        # one notch above spreadsheet import.
        # permissions.py: admin runs the system, **which does not include drafting or
        # confirming**. Design §4.1
        if not views.may(perm.INTERPRETATION_DRAFT):
            return fail(
                "Importing from Word / PDF calls the model and spends the organization's money, so this step needs "
                "the author role. Spreadsheet (.csv / .xlsx) imports are unaffected.")
        if text is None:
            try:
                text = extract(filename, data)
            except UnsupportedDocument as exc:
                return fail(f"Import failed: {exc}")
        if not text.strip():
            return fail("This document contains no text.")

        # Pre-check: count how many calls will be sent first; if the gate refuses, not a single
        # request is sent. Design §4
        planned = len(outline_mod.plan_calls(text))
        actor = _who(request)
        try:
            models_config.charge_draft(
                actor, planned, what=f"splitting {framework_id}",
                running_jobs=jobs.running_count())
        except BudgetError as exc:
            return fail(str(exc))

        try:
            client, model = _extractor_client()
        except MissingApiKeyError as exc:
            # A missing key must not surface as a 500. **This step runs after the budget was
            # charged**, so it has to be refunded.
            models_config.refund_draft(actor, planned)
            return fail(f"{exc}")
        run = outline_runner or outline_mod.outline_document

        def work(report) -> str:
            """Runs in the background. **The pre-check, the permission check, and the key are
            all done synchronously above** — those can be known immediately; pushing them into
            the background only makes people watch a spinner for three seconds before seeing
            "you have no permission". All that remains here is the genuinely slow part.
            """
            result = run(text, client=client, model=model, on_chunk=report)
            # The audit log records only that this happened. **Not a single character of policy
            # text enters the log.**
            identity.log("framework.outline", actor=actor,
                         detail=f"{framework_id} <- {filename}, "
                                f"{planned} calls, cut into {len(result.spans)} controls")
            return ImportDraftStore(_user_db()).create(
                framework_id=framework_id, name=name, source_text=text,
                spans=result.spans, problems=result.problems, actor=actor)

        job = jobs.start_outline(framework_id, total=planned, runner=work)
        return RedirectResponse(f"/import/job/{job.job_id}", status_code=303)

    @app.post("/import")
    @needs(perm.FRAMEWORK_IMPORT)
    async def import_framework(
        request: Request,
        framework_id: str = Form(""),
        name: str = Form(""),
        file: UploadFile = File(...),
    ):
        from framework_reader.userframework.importer import (
            ImportError_, parse_any_sheet, parse_table, read_sheets,
        )
        from framework_reader.userframework.store import UserFrameworkStore
        from framework_reader.web.uploads import UploadTooLarge, save_limited

        def fail(message: str) -> HTMLResponse:
            return HTMLResponse(views.import_page(error=message))

        if not framework_id.strip() or not name.strip():
            return fail("Both the ID and the display name are required.")

        # A separate temporary file per upload. The original fixed name `_upload{suffix}` was
        # harmless for a single person; with two people importing at once, one person's sheet
        # got overwritten by the other's.
        import tempfile

        suffix = Path(file.filename or "").suffix
        store = UserFrameworkStore(_user_db())
        scratch = Path(store.path).parent / "_uploads"
        scratch.mkdir(parents=True, exist_ok=True)
        handle, raw_path = tempfile.mkstemp(suffix=suffix, dir=scratch)
        tmp = Path(raw_path)
        try:
            with open(handle, "wb") as sink:
                await save_limited(file, sink)
            if suffix.lower() in _DOCUMENT_SUFFIXES:
                return _outline_upload(
                    request, framework_id.strip(), name.strip(),
                    file.filename or "", tmp.read_bytes(), fail)
            sheets = read_sheets(tmp)
            # Try the worksheets one by one. "Instructions page first, real table after" is the
            # most common layout; reading only book.active means the whole file imports to
            # nothing.
            _, controls = parse_any_sheet(sheets)
            if controls is None:
                # None of them could be recognised. Making the user go fix their own sheet
                # means pushing our problem onto them.
                answered, note = _shape_table(
                    request, framework_id.strip(), name.strip(),
                    file.filename or "", sheets, fail)
                if answered is not None:
                    return answered
                first = sheets[0][1] if sheets else []
                try:
                    parse_table(first)
                except ImportError_ as exc:
                    return fail(f"Import failed: {exc}"
                                + (f"  {note}" if note else ""))
        except UploadTooLarge as exc:
            return HTMLResponse(views.import_page(error=str(exc)), 413)
        except ImportError_ as exc:
            return fail(f"Import failed: {exc}")
        finally:
            tmp.unlink(missing_ok=True)

        store.add_framework(
            framework_id=framework_id.strip(), name=name.strip(),
            controls=controls, source_file=file.filename or "",
        )
        return RedirectResponse(f"/f/{framework_id.strip()}", status_code=303)

    @app.get("/import/job/{job_id}", response_class=HTMLResponse)
    @needs(perm.FRAMEWORK_IMPORT)
    def import_job(job_id: str):
        """Splitting progress. The page refreshes itself while running — otherwise all a person
        can do is stare at a frozen page and guess."""
        from framework_reader.web import jobs

        job = jobs.get_outline(job_id)
        if job is None:
            return HTMLResponse(views.page(
                "Not found",
                "<p>No such splitting job: if the service restarted, it is gone. Upload the file again.</p>",
                crumb="Import"), 404)
        if job.status == "done" and job.draft_id:
            return RedirectResponse(f"/import/{job.draft_id}", status_code=303)
        return HTMLResponse(views.import_progress(job))

    @app.get("/import/{draft_id}", response_class=HTMLResponse)
    @needs(perm.FRAMEWORK_IMPORT)
    def import_preview(draft_id: str):
        from framework_reader.userframework.import_draft import ImportDraftStore
        from framework_reader.userframework.outline import slice_lines

        draft = ImportDraftStore(_user_db()).load(draft_id)
        if draft is None:
            return HTMLResponse(views.page(
                "Not found",
                "<p>No such import draft; it may already be confirmed or discarded.</p>",
                crumb="Import"), 404)
        # Only here is the body text sliced out of the source. The draft stores line numbers,
        # not text — storing a copy of the body hands it a chance to drift out of sync with the
        # source.
        bodies = [slice_lines(draft.source_text, s.start, s.end)
                  for s in draft.spans]
        return HTMLResponse(views.import_preview(draft, bodies))

    @app.post("/import/{draft_id}/confirm")
    @needs(perm.FRAMEWORK_IMPORT)
    async def import_confirm(draft_id: str, request: Request):
        from framework_reader.userframework.import_draft import ImportDraftStore
        from framework_reader.userframework.outline import Span, slice_lines
        from framework_reader.userframework.store import UserFrameworkStore

        store = ImportDraftStore(_user_db())
        draft = store.load(draft_id)
        if draft is None:
            return HTMLResponse(views.page(
                "Not found", "<p>No such import draft.</p>", crumb="Import"), 404)

        form = await request.form()
        # Every submission writes the IDs and titles from the boxes back into the draft —
        # merging and confirming both need them, and "edit the title, then click merge" must
        # not throw the edit away.
        edited = [_with_edits(s, form, i) for i, s in enumerate(draft.spans)]
        kept_keys = set(form.getlist("keep"))

        if form.get("merge"):
            index = int(form["merge"])
            if index < 1 or index >= len(edited):
                return RedirectResponse(f"/import/{draft_id}", status_code=303)
            # Merge with the immediately preceding entry, ticked or not. The line ranges are
            # unioned — lines between the two spans that nothing else covered come along too,
            # which is exactly what is wanted. ID and title come from the preceding entry.
            above, here = edited[index - 1], edited[index]
            merged = Span(ref=above.ref, label=above.label, parent=above.parent,
                          start=min(above.start, here.start),
                          end=max(above.end, here.end))
            edited = edited[:index - 1] + [merged] + edited[index + 1:]
            store.save(draft_id, spans=edited, dropped=set())
            return RedirectResponse(f"/import/{draft_id}", status_code=303)

        chosen = [s for i, s in enumerate(edited) if str(i) in kept_keys]
        if not chosen:
            store.save(draft_id, spans=edited,
                       dropped={str(i) for i in range(len(edited))})
            return HTMLResponse(views.page(
                "Nothing ticked", "<h2>Tick at least one control</h2>"
                '<p class="note">Confirming with nothing ticked would plant an empty framework.</p>'
                f'<p><a href="/import/{draft_id}">Go back and choose</a></p>',
                crumb="Import"), 400)

        # The number is half of the control ID, and user_control.id is the primary key —
        # duplicates and blanks alike would blow up as an IntegrityError inside
        # `add_framework`. By that point the person has spent ages editing the preview page,
        # and after the crash nothing is saved. **Catch it here, and say exactly which one it
        # is.**
        blank = [s.label or "(no title)" for s in chosen if not s.ref]
        if blank:
            return _confirm_refused(
                draft_id, "IDs cannot be empty",
                f"These controls have no ID yet: {', '.join(blank[:5])}. "
                "The ID is half of the control ID; saving one empty produces an entry like 'framework ID:'.")
        seen: set[str] = set()
        clash = sorted({s.ref for s in chosen if s.ref in seen or seen.add(s.ref)})
        if clash:
            return _confirm_refused(
                draft_id, "Duplicate IDs",
                f"These IDs appear more than once: {', '.join(clash[:5])}. "
                "They would collide in the database; change one of them and confirm again.")
        controls = [
            (s.ref, s.label, s.parent,
             slice_lines(draft.source_text, s.start, s.end))
            for s in chosen
        ]
        UserFrameworkStore(_user_db()).add_framework(
            framework_id=draft.framework_id, name=draft.name,
            controls=controls, source_file="")
        identity.log("framework.import", actor=_who(request),
                     detail=f"{draft.framework_id}, {len(controls)} controls (document splitting)")
        store.delete(draft_id)
        return RedirectResponse(f"/f/{draft.framework_id}", status_code=303)

    def _with_edits(span, form, index: int):
        """Writes the IDs and titles from the boxes back. **Whatever a human changed belongs
        to the human** — marking a replaced title as "AI-named" afterwards mis-attributes both
        the credit and the responsibility.
        """
        from framework_reader.userframework.outline import Span

        ref = str(form.get(f"ref-{index}", span.ref)).strip()
        label = str(form.get(f"label-{index}", span.label)).strip()
        return Span(
            ref=ref, label=label, parent=span.parent,
            start=span.start, end=span.end,
            ref_from="practitioner" if ref != span.ref else span.ref_from,
            label_from="practitioner" if label != span.label else span.label_from,
        )

    def _confirm_refused(draft_id: str, title: str, detail: str):
        """Confirmation refused. **Not one entry is written to the database** — half a framework
        is worse than no framework."""
        return HTMLResponse(views.page(
            title, f"<h2>{title}</h2>"
            f'<p class="note">{detail}</p>'
            f'<p><a href="/import/{draft_id}">Go back and fix it</a></p>',
            crumb="Import"), 400)

    @app.get("/import/{draft_id}/discard")
    @needs(perm.FRAMEWORK_IMPORT)
    def import_discard(draft_id: str):
        from framework_reader.userframework.import_draft import ImportDraftStore

        ImportDraftStore(_user_db()).delete(draft_id)
        return RedirectResponse("/import", status_code=303)

    # ---------- Members and audit ----------
    #
    # Account management used to be CLI-only (`fr account grant`). On a hosted service the
    # admin may not have a shell on the server — a management action possible in the CLI but
    # not in the UI is as good as not done.

    def _members_page(invite_link: str = "", error: str = "", status: int = 200):
        rows = [
            {"id": a.id, "email": a.email, "display_name": a.display_name,
             "roles": a.roles, "active": a.active}
            for a in identity.list_accounts()
        ]
        return HTMLResponse(views.members(
            rows,
            can_manage=views.may(perm.ROLE_GRANT),
            self_grant_allowed=identity.self_grant_allowed(),
            invite_link=invite_link, error=error,
            entra=_entra_client() is not None,
            bootstrap=not identity.configured(),
        ), status)

    @app.get("/members", response_class=HTMLResponse)
    @needs(perm.MEMBER_READ)
    def members_page():
        return _members_page()

    @app.post("/members/bootstrap")
    @needs(perm.MEMBER_MANAGE)
    def members_bootstrap(request: Request, email: str = Form(""),
                          display_name: str = Form(""),
                          password: str = Form(""), again: str = Form("")):
        """The first administrator. Until this exists, the CLI is the only way in.

        **The latch is `configured()`, not hiding the form on the page.** Once any account
        exists (or an invitation has been sent), this route must be dead: otherwise it is a way
        around the invitation flow to hand yourself admin, and in local mode the guard does not
        even check CSRF.

        Once closed it refuses with **409, not 403**: in this codebase 403 means exactly "your
        role cannot do this", and the authorisation matrix's exhaustive tests rely on that
        convention to tell real denials from spurious ones. The reason for refusing here has
        nothing to do with roles; occupying 403 would punch a fake hole in that matrix.
        """
        from framework_reader.identity.store import IdentityError

        if identity.configured():
            return HTMLResponse(views.refused(
                "This door is already closed",
                "Accounts exist; the first administrator can only be created once.",
                "To add people, send invitations on the members page; to grant the admin role, do it there."), 409)
        if password != again:
            return _members_page(
                error="The two passphrase entries do not match.", status=400)
        if len(password) < 12:
            return _members_page(
                error="The passphrase must be at least 12 characters. It is the key to all your compliance material.",
                status=400)
        try:
            account = identity.create_account(
                email=email, password=password, display_name=display_name,
                roles=("admin",))
        except IdentityError as exc:
            return _members_page(error=str(exc), status=400)
        identity.log("account.bootstrap", actor=account.email,
                     detail="first administrator, created on the web; the identity system is now enabled")
        # Start the session directly: the gate locks at this moment, and kicking someone
        # straight back to the sign-in page right after they created the first account would be
        # silly. Same approach as invite_submit.
        session = identity.start_session(account)
        return _set_cookie(RedirectResponse("/", status_code=303), session.token)

    @app.post("/members/invite")
    @needs(perm.MEMBER_MANAGE)
    def members_invite(request: Request, email: str = Form(""),
                       role: str = Form("viewer")):
        from framework_reader.identity.store import IdentityError

        try:
            token = identity.invite(email=email, role=role, by=_who(request))
        except IdentityError as exc:
            return _members_page(error=str(exc), status=400)
        identity.log("account.invite", actor=_who(request), detail=f"{email} {role}")
        # The link is rendered directly, not via a redirect — once the token is in a URL it
        # sits in proxy logs and browser history.
        return _members_page(
            invite_link=f"{request.base_url}".rstrip("/") + f"/invite/{token}")

    @app.post("/members/{account_id}/role")
    @needs(perm.ROLE_GRANT)
    def members_role(account_id: str, request: Request,
                     grant: str = Form(""), revoke: str = Form("")):
        from framework_reader.identity.store import IdentityError

        target = identity.by_id(account_id)
        if target is None:
            return HTMLResponse(views.refused(
                "No such account",
                "This member may have been deleted.",
                "Refresh the members page."), 404)
        role = grant or revoke
        try:
            if grant:
                identity.grant(account_id, role, by=_account_id(request))
            else:
                identity.revoke(account_id, role, by=_account_id(request))
        except IdentityError as exc:
            return _members_page(error=str(exc), status=400)
        identity.log("role.grant" if grant else "role.revoke",
                     actor=_who(request), detail=f"{target.email} {role}")
        return RedirectResponse("/members", status_code=303)

    @app.post("/members/{account_id}/status")
    @needs(perm.MEMBER_MANAGE)
    def members_status(account_id: str, request: Request, status: str = Form("")):
        from framework_reader.identity.store import IdentityError

        target = identity.by_id(account_id)
        if target is None:
            return HTMLResponse(views.refused(
                "No such account",
                "This member may have been deleted.",
                "Refresh the members page."), 404)
        try:
            identity.set_status(account_id, status)
        except IdentityError as exc:
            return _members_page(error=str(exc), status=400)
        identity.log(f"account.{status}", actor=_who(request), detail=target.email)
        return RedirectResponse("/members", status_code=303)

    @app.post("/members/self-grant")
    @needs(perm.ROLE_GRANT)
    def members_self_grant(request: Request, allowed: str = Form("0")):
        """The switch itself falls under role:grant, because what it governs is authorisation.
        Turning it off leaves an audit trail. Design §4.3"""
        identity.set_self_grant(allowed == "1", by=_who(request))
        return RedirectResponse("/members", status_code=303)

    # ---------- Supporting documents ----------
    #
    # What the drafter produces is generic advice. How this team actually implements controls
    # is written in their own policies, and that line is not something a model can guess.

    def _documents():
        from framework_reader.userframework.documents import DocumentStore

        return DocumentStore(_user_db())

    @app.get("/documents", response_class=HTMLResponse)
    @needs(perm.DOCUMENT_READ)
    def documents_page(error: str = ""):
        return HTMLResponse(views.documents(
            _documents().list_documents(),
            can_write=views.may(perm.DOCUMENT_WRITE), error=error))

    @app.post("/documents")
    @needs(perm.DOCUMENT_WRITE)
    async def document_upload(request: Request, title: str = Form(""),
                              file: UploadFile = File(...)):
        from framework_reader.userframework.extract import UnsupportedDocument
        from framework_reader.web.uploads import UploadTooLarge, read_limited

        try:
            doc = _documents().add(
                file.filename or "", await read_limited(file),
                by=_who(request) or _local_user(), title=title.strip())
        except UploadTooLarge as exc:
            return HTMLResponse(views.documents(
                _documents().list_documents(), can_write=True,
                error=str(exc)), 413)
        except UnsupportedDocument as exc:
            return HTMLResponse(views.documents(
                _documents().list_documents(), can_write=True,
                error=str(exc)), 400)
        # Internal policy has now entered the server and will enter the model's payload. Who
        # uploaded it must leave a trace.
        identity.log("document.upload", actor=_who(request),
                     detail=f"{doc.filename} ({doc.chars} chars)")
        return RedirectResponse("/documents", status_code=303)

    @app.get("/documents/{doc_id}", response_class=HTMLResponse)
    @needs(perm.DOCUMENT_READ)
    def document_page(doc_id: str):
        store = _documents()
        doc = store.get(doc_id)
        if doc is None:
            return HTMLResponse(views.page(
                "Not found", "<p>No such document.</p>"), 404)
        return HTMLResponse(views.document(doc, store.chunks(doc_id)))

    @app.post("/documents/{doc_id}/delete")
    @needs(perm.DOCUMENT_WRITE)
    def document_delete(doc_id: str, request: Request):
        store = _documents()
        doc = store.get(doc_id)
        if doc is None:
            return HTMLResponse(views.page(
                "Not found", "<p>No such document.</p>"), 404)
        store.delete(doc_id)
        identity.log("document.delete", actor=_who(request), detail=doc.filename)
        return RedirectResponse("/documents", status_code=303)

    # ---------- Models and keys ----------
    #
    # The right answer to "bring your own AI": not writing the key into the server's
    # environment variables (that needs a shell, and every change needs a restart), but the
    # admin filling it in through the UI, storing it encrypted, and echoing it back masked.

    def _models_page(error: str = "", notice: str = "", status: int = 200,
                     focus: tuple[str, str, str] | None = None):
        from framework_reader import crypto
        from framework_reader.llm.config import effective_registry
        from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry

        presets = [{"id": p.id, "note": p.note, "verified": p.verified,
                    "custom": False}
                   for p in LLMRegistry.load(DEFAULT_REGISTRY_PATH).providers]
        custom = models_config.custom_providers()
        # Custom endpoints must also be selectable in the role dropdown — otherwise
        # configuring one is useless. verified=True: it is your own endpoint; "have we vetted
        # it" does not apply.
        presets += [{"id": pid, "note": row["base_url"], "verified": True,
                     "custom": True}
                    for pid, row in custom.items()]
        # Show the values **actually in effect**, not just what was configured on this page:
        # the question this page must answer is "who is really taking our money right now", not
        # "what did I once click here".
        registry, _ = effective_registry(config=models_config)
        overridden = set(models_config.roles())
        roles = {
            name: {"provider": cfg.provider, "model": cfg.model,
                   "overridden": name in overridden}
            for name, cfg in registry.roles.items()
        }
        return HTMLResponse(views.models(
            roles=roles, presets=presets,
            keys=models_config.masked(), limits=models_config.limits(),
            custom=custom,
            catalogs=models_config.catalogs(), focus=focus,
            spent=models_config.spent_this_month(),
            can_write=views.may(perm.MODEL_WRITE),
            master_key=crypto.configured(),
            error=error, notice=notice,
        ), status)

    def _probe(provider: str, model: str):
        """The "test it" action: really sends one minimal request. `probe_runner` is injected
        for tests only."""
        from framework_reader.llm import probe as probe_mod
        from framework_reader.llm.config import effective_registry

        registry, _ = effective_registry(config=models_config)
        preset = registry.preset(provider)
        run = probe_runner or (
            lambda preset, model, api_key: probe_mod.probe_model(
                preset, model, api_key))
        # **Takes the same key-lookup path as drafting** (database first, then environment
        # variables). Looking only at the database, a provider configured on the server via
        # environment variables would be reported as "no key yet" while working perfectly well.
        return run(preset, model, _key_for(preset))

    def _key_for(preset) -> str | None:
        return models_config.key_lookup()(preset.api_key_env)

    def _known_providers() -> set[str]:
        from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry

        return ({p.id for p in LLMRegistry.load(DEFAULT_REGISTRY_PATH).providers}
                | set(models_config.custom_providers()))

    def _fetch_catalog(provider: str) -> None:
        """Fetches the model catalogue once and stores it. **No failure may ever fail the
        caller** — saving the key is the main action; fetching the catalogue is the free rider.

        `http_get` exists for test injection only (same pattern as `entra_fetch`). Default
        None → the catalog module uses its own `_default_get`: real network access is contained
        in that one function.
        """
        from framework_reader.llm.catalog import CatalogError, fetch_models
        from framework_reader.llm.config import effective_registry

        registry, _ = effective_registry(config=models_config)
        try:
            preset = registry.preset(provider)
        except Exception:  # noqa: BLE001 - e.g. the provider was just deleted
            return
        key = models_config.key(provider)
        if not key:
            return
        try:
            models = fetch_models(preset, key, http_get=http_get)
        except CatalogError as exc:
            models_config.set_catalog(provider, [], error=str(exc))
            return
        except Exception:  # noqa: BLE001
            models_config.set_catalog(
                provider, [],
                error=f"Something unexpected happened while fetching {provider}'s model catalog. Click Refresh to retry.")
            return
        models_config.set_catalog(provider, models)

    @app.get("/settings", response_class=HTMLResponse)
    @needs(perm.MEMBER_READ)
    def settings_page():
        """The gate is member:read — all four roles hold it, and in local single-user mode
        `may()` is always true.

        Each block inside the page is then judged against its own permission: the page itself
        is not an action, it only gathers the entrances in one place (§1.2 — the unit of
        permission is the action, not the page).
        """
        return HTMLResponse(views.settings(bootstrap=not identity.configured()))

    def _branding_logo() -> Path | None:
        from framework_reader import usage

        base = usage.home() / "branding"
        for ext in ("png", "jpg", "webp", "gif", "svg"):
            candidate = base / f"logo.{ext}"
            if candidate.exists():
                return candidate
        return None

    def _product_mark(name: str) -> Path:
        return Path(__file__).parent / "static" / name

    def _product_mark_response(name: str, media_type: str) -> Response:
        path = _product_mark(name)
        return Response(
            path.read_bytes(),
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/favicon.ico")
    def favicon_ico():
        return _product_mark_response("favicon.ico", "image/x-icon")

    @app.get("/favicon.svg")
    def favicon_svg():
        return _product_mark_response("favicon.svg", "image/svg+xml")

    @app.get("/apple-touch-icon.png")
    def apple_touch_icon():
        return _product_mark_response("apple-touch-icon.png", "image/png")

    @app.get("/branding/logo")
    def branding_logo():
        from fastapi.responses import FileResponse

        found = _branding_logo()
        if found is None:
            return HTMLResponse(views.page("Not found", "<p>No custom logo.</p>"), 404)
        media = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp",
                 "gif": "image/gif", "svg": "image/svg+xml"}[found.suffix.lstrip(".")]
        # The CSP is sent for raster images too: opening the logo directly in the address bar
        # must not be able to run any scripts.
        return FileResponse(found, media_type=media, headers={
            "Cache-Control": "public, max-age=600",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff"})

    @app.get("/settings/sso", response_class=HTMLResponse)
    @needs(perm.MEMBER_MANAGE)
    def sso_settings_page():
        return HTMLResponse(views.sso_settings(
            identity.sso_config(), from_env=entra_config.configured()))

    def _form_secret_or_saved(form_secret: str) -> str:
        """Leaving the secret blank on the form = keep the saved one, not clear it."""
        return form_secret.strip() or identity.sso_secret()

    @app.post("/settings/sso")
    @needs(perm.MEMBER_MANAGE)
    def sso_save(request: Request, tenant_id: str = Form(""),
                 client_id: str = Form(""), client_secret: str = Form(""),
                 redirect_uri: str = Form(""), authority: str = Form(""),
                 enabled: str = Form("")):
        if not (tenant_id.strip() or client_id.strip() or client_secret.strip()):
            # The authorisation matrix hits every route with empty forms: an empty form must
            # not wipe out a saved, working configuration.
            return RedirectResponse("/settings/sso", status_code=303)
        from framework_reader import crypto

        try:
            identity.save_sso_config(
                tenant_id=tenant_id, client_id=client_id,
                redirect_uri=redirect_uri,
                secret=_form_secret_or_saved(client_secret),
                authority=authority, enabled=enabled == "on", by=_who(request))
        except crypto.SecretError as exc:
            # Refuse to store anything when the master key is not configured (same rule as
            # model keys): silently storing plaintext would make you believe it is encrypted
            # when it is not.
            return HTMLResponse(views.refused("Cannot store the client secret",
                                              str(exc)), 400)
        identity.log("sso.configured", actor=_who(request), detail=tenant_id.strip())
        return RedirectResponse("/settings/sso", status_code=303)

    @app.post("/settings/sso/check")
    @needs(perm.MEMBER_MANAGE)
    def sso_check(request: Request, tenant_id: str = Form(""),
                  client_id: str = Form(""), client_secret: str = Form(""),
                  redirect_uri: str = Form(""), authority: str = Form(""),
                  enabled: str = Form("")):
        """Tests **the copy currently filled in on the form** (a blank secret is filled in
        from the saved one) — test first, then save, so what gets saved is what was tested.
        Exactly one discovery-document request goes out."""
        from framework_reader.identity.entra import EntraClient, EntraConfig, EntraError

        from framework_reader import crypto

        try:
            secret = _form_secret_or_saved(client_secret)
        except crypto.SecretError as exc:
            secret, secret_problem = "", str(exc)
        else:
            secret_problem = ""
        cfg = EntraConfig(
            tenant_id=tenant_id.strip(), client_id=client_id.strip(),
            client_secret=secret,
            redirect_uri=redirect_uri.strip(),
            authority=authority.strip() or "https://login.microsoftonline.com")
        problems = []
        if not cfg.configured():
            problems.append("Directory (tenant) ID and Application (client) ID are both required")
        if secret_problem:
            problems.append(secret_problem)
        elif not cfg.client_secret:
            problems.append("Client secret is missing - the token exchange will fail")
        if not cfg.redirect_uri:
            problems.append("Redirect URI is missing - it must match the app registration exactly")
        elif not cfg.redirect_uri.startswith("https://"):
            problems.append(f"Redirect URI is not https: {cfg.redirect_uri}"
                            " - session cookies would cross the network unencrypted")
        elif not cfg.redirect_uri.endswith("/auth/entra/callback"):
            problems.append(f"Redirect URI should end with /auth/entra/callback: {cfg.redirect_uri}")

        # Even when the other checks fail, the discovery document is still fetched once —
        # surface every problem in one pass, instead of fix one item, retest, discover the
        # next. Only this one request leaves the network.
        discovery_error, issuer = "", ""
        if cfg.configured():
            try:
                issuer = EntraClient(cfg, fetch=entra_fetch).discovery().get("issuer", "")
            except EntraError as exc:
                discovery_error = str(exc)
        return HTMLResponse(views.sso_settings(
            identity.sso_config(), from_env=entra_config.configured(),
            report={"problems": problems, "discovery_error": discovery_error,
                    "issuer": issuer}))

    @app.post("/settings/sso/disable")
    @needs(perm.MEMBER_MANAGE)
    def sso_disable(request: Request):
        identity.clear_sso_config()
        identity.log("sso.removed", actor=_who(request))
        return RedirectResponse("/settings/sso", status_code=303)

    @app.get("/settings/branding", response_class=HTMLResponse)
    @needs(perm.MEMBER_MANAGE)
    def branding_page():
        found = _branding_logo()
        logo = ({"version": int(found.stat().st_mtime)} if found else None)
        return HTMLResponse(views.branding_settings(logo))

    @app.post("/settings/branding")
    @needs(perm.MEMBER_MANAGE)
    async def branding_upload(request: Request):
        from framework_reader.web.images import (
            looks_like_svg, sanitize_svg, sniff_image,
        )
        from framework_reader.web.uploads import UploadTooLarge, read_limited
        def refuse(message: str):
            found = _branding_logo()
            logo = ({"version": int(found.stat().st_mtime)} if found else None)
            return HTMLResponse(views.branding_settings(logo, error=message), 400)

        form = await request.form()
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            return refuse("Choose a file first.")
        try:
            data = await read_limited(upload, max_bytes=512 * 1024)
        except UploadTooLarge as exc:
            return refuse(str(exc))
        kind = sniff_image(data)
        if kind is None and looks_like_svg(data):
            try:
                data = sanitize_svg(data)
            except ValueError as exc:
                return refuse(f"That SVG is not accepted: {exc}")
            except Exception:
                return refuse("That file is not a well-formed SVG document.")
            kind = "svg"
        if kind is None:
            return refuse("Only PNG, JPEG, WebP, GIF or SVG.")
        from framework_reader import usage

        base = usage.home() / "branding"
        base.mkdir(parents=True, exist_ok=True)
        for old in base.glob("logo.*"):
            old.unlink()
        (base / f"logo.{kind}").write_bytes(data)
        identity.log("branding.logo", actor=_who(request))
        return RedirectResponse("/settings/branding", status_code=303)

    @app.post("/settings/branding/remove")
    @needs(perm.MEMBER_MANAGE)
    def branding_remove(request: Request):
        from framework_reader import usage

        base = usage.home() / "branding"
        if base.is_dir():
            for old in base.glob("logo.*"):
                old.unlink()
        identity.log("branding.removed", actor=_who(request))
        return RedirectResponse("/settings/branding", status_code=303)

    @app.get("/settings/backup", response_class=HTMLResponse)
    @needs(perm.FRAMEWORK_IMPORT)
    def backup_page():
        return HTMLResponse(views.backup(_framework_rows()))

    @app.post("/settings/backup")
    @needs(perm.FRAMEWORK_IMPORT)
    def backup_download(request: Request):
        from datetime import date

        from framework_reader.userframework.backup import snapshot

        blob = snapshot(_user_db())
        identity.log("backup.download", actor=_who(request) or _local_user(),
                     detail=f"{len(blob)} bytes")
        stamp = date.today().isoformat()
        return Response(
            content=blob,
            media_type="application/vnd.sqlite3",
            headers={"content-disposition":
                     f'attachment; filename="framework-reader-user-{stamp}.sqlite"'},
        )

    @app.post("/settings/backup/{framework_id}/pdf")
    @needs(perm.FRAMEWORK_IMPORT)
    def backup_pdf(request: Request, framework_id: str):
        from framework_reader.publish.pdf import render_framework_pdf

        try:
            blob = render_framework_pdf(api(), framework_id)
        except LookupError:
            return HTMLResponse(views.page(
                "No interpretations",
                "<h2>This framework has no interpretations to export</h2>"
                '<p class="note">The PDF includes only controls that have interpretations.</p>',
                crumb="Backup", crumb_href="/settings/backup",
            ), 404)
        identity.log("backup.pdf", actor=_who(request) or _local_user(),
                     detail=framework_id)
        return Response(
            content=blob, media_type="application/pdf",
            headers={"content-disposition":
                     f'attachment; filename="{framework_id}.pdf"'},
        )

    @app.get("/models", response_class=HTMLResponse)
    @needs(perm.MODEL_READ)
    def models_page():
        return _models_page()

    @app.post("/models/key")
    @needs(perm.MODEL_WRITE)
    def models_key(request: Request, provider: str = Form(""),
                   key: str = Form(""), clear: str = Form("")):
        from framework_reader import crypto

        if provider not in _known_providers():
            # A provider outside the presets has an empty endpoint — the key would be saved
            # only to sit there unusable.
            return _models_page(
                error=f"Provider not in the presets: {provider}", status=400)
        if clear:
            models_config.clear_key(provider)
            identity.log("model.key", actor=_who(request),
                         detail=f"clear {provider}")
            return RedirectResponse("/models", status_code=303)
        if not key.strip():
            return _models_page(error="The key cannot be empty.", status=400)
        try:
            models_config.set_key(provider, key.strip(), by=_who(request))
        except crypto.SecretError as exc:
            return _models_page(error=str(exc), status=400)
        # The audit log **records only that this happened**. Not a single character of the key
        # enters the log.
        identity.log("model.key", actor=_who(request),
                     detail=f"set {provider}")
        # With the key saved, ask once, while we are at it, "which models do you have". A
        # failure does not undo the fact that the key is already stored.
        _fetch_catalog(provider)
        return RedirectResponse("/models", status_code=303)

    @app.post("/models/role")
    @needs(perm.MODEL_WRITE)
    def models_role(request: Request, role: str = Form(""),
                    provider: str = Form(""), model: str = Form(""),
                    key: str = Form("")):
        from framework_reader import crypto
        from framework_reader.web.views import ROLE_WHAT_FOR

        # The model name now has a single box (a datalist you can pick from or type into); the
        # "dropdown vs typed" either/or is gone. Copy-pasting from the catalogue often drags in
        # a trailing space, so it is stripped all the same.
        model = model.strip()

        if key.strip():
            # A key was filled in within this block: save the key and fetch the catalogue
            # first, **and do not change the role this time**. The model-name box most likely
            # still holds the previous provider's model; pairing it with the new provider would
            # be wrong.
            if role not in ROLE_WHAT_FOR:
                return _models_page(
                    error=f"No such calling role: {role}", status=400)
            if provider not in _known_providers():
                return _models_page(
                error=f"Provider not in the presets: {provider}", status=400)
            try:
                models_config.set_key(provider, key.strip(), by=_who(request))
            except crypto.SecretError as exc:
                return _models_page(error=str(exc), status=400)
            identity.log("model.key", actor=_who(request),
                     detail=f"set {provider}")
            _fetch_catalog(provider)
            cached = models_config.catalog(provider) or {}
            if cached.get("error"):
                return _models_page(
                    notice=f"The {provider} key is saved. {cached['error']}",
                    focus=(role, provider, ""))
            return _models_page(
                notice=f"The {provider} key is saved and {len(cached.get('models', []))} models came back. "
                       "Pick one, then save.",
                focus=(role, provider, ""))

        if role not in ROLE_WHAT_FOR:
            return _models_page(
                error=f"No such calling role: {role}", status=400)
        if provider not in _known_providers():
            return _models_page(
                error=f"Provider not in the presets: {provider}", status=400)
        if not model.strip():
            return _models_page(error="The model name cannot be empty.", status=400)
        models_config.set_role(role, provider=provider, model=model.strip(),
                               by=_who(request))
        # Changing the endpoint = the data flow changed. This one must leave a trace.
        # Design §4.4
        identity.log("model.role", actor=_who(request),
                     detail=f"{role} → {provider} / {model.strip()}")
        return RedirectResponse("/models", status_code=303)

    @app.post("/models/role/test")
    @needs(perm.MODEL_WRITE)
    def models_role_test(request: Request, role: str = Form(""),
                         provider: str = Form(""), model: str = Form("")):
        """Tests whether this combination works. **It tests the combination currently chosen
        on the form, not the one stored in the database.**

        "Pick → test → save" has one fewer error state than "save → test": no need to write an
        unverified configuration into the database first, then go back and validate it. So this
        path writes not a single character to the database.

        The returned page `focus`es on the provider under test and renders its model catalogue
        along with it — when switching providers, the loop of "to pick a model you need a
        catalogue, to have a catalogue you must submit, and submitting demands a non-empty
        model name" is broken exactly here.
        """
        from framework_reader.web.views import ROLE_WHAT_FOR

        model = model.strip()

        if role not in ROLE_WHAT_FOR:
            return _models_page(
                error=f"No such calling role: {role}", status=400)
        if provider not in _known_providers():
            return _models_page(
                error=f"Provider not in the presets: {provider}", status=400)
        if not model:
            # **No fallback to default_model.** Otherwise people believe they tested the model
            # they typed, while the green tick was actually about a different model.
            return _models_page(
                error="Pick a model name (or type one) before testing; without it there is nothing to test.",
                status=400, focus=(role, provider, model))
        from framework_reader.llm.config import effective_registry

        registry, _ = effective_registry(config=models_config)
        if not _key_for(registry.preset(provider)):
            return _models_page(
                error=f"{provider} has no key yet and cannot be tested. Enter the key in this block first.",
                status=400, focus=(role, provider, model))

        result = _probe(provider, model)
        # Trace it: this is a real outbound call spending the organisation's money. Same
        # reasoning as design §4.4.
        # **What the model replied does not enter the log** — in case someone uses this box as
        # a chat window.
        identity.log("model.test", actor=_who(request),
                     detail=f"{provider} / {model} -> "
                            f"{'ok' if result.ok else result.kind}")
        if result.ok:
            spoken = f'It replied: "{result.reply}"' if result.reply else "It returned nothing (normal)"
            return _models_page(
                notice=f"{result.message}{spoken}, took {result.elapsed_ms} ms. "
                       "Not saved yet; save only when it looks right.",
                focus=(role, provider, model))
        return _models_page(error=result.message, status=400,
                            focus=(role, provider, model))

    @app.post("/models/provider")
    @needs(perm.MODEL_WRITE)
    def models_provider(request: Request, provider: str = Form(""),
                        base_url: str = Form(""), default_model: str = Form("")):
        """Providers that are not in the presets: intranet gateways, Azure deployments, local
        vLLM/Ollama.

        Adding an endpoint = deciding where framework body text gets sent, so only admin may do
        it, and it must leave a trace.
        """
        from framework_reader.llm.config import CustomProviderError

        try:
            models_config.set_custom_provider(
                provider, base_url=base_url, default_model=default_model,
                by=_who(request))
        except CustomProviderError as exc:
            return _models_page(error=str(exc), status=400)
        # The data flow goes into the audit log. The base_url is recorded; the key is not on
        # this path. Design §4.4
        identity.log("model.provider", actor=_who(request),
                     detail=f"custom endpoint {provider.strip()} -> {base_url.strip()}")
        return RedirectResponse("/models", status_code=303)

    @app.post("/models/provider/delete")
    @needs(perm.MODEL_WRITE)
    def models_provider_delete(request: Request, provider: str = Form("")):
        from framework_reader.llm.config import CustomProviderError

        try:
            models_config.delete_custom_provider(provider)
        except CustomProviderError as exc:
            return _models_page(error=str(exc), status=400)
        identity.log("model.provider", actor=_who(request),
                     detail=f"delete custom endpoint {provider}")
        return RedirectResponse("/models", status_code=303)

    @app.post("/models/catalog/refresh")
    @needs(perm.MODEL_WRITE)
    def models_catalog_refresh(request: Request, provider: str = Form("")):
        if provider not in _known_providers():
            return _models_page(error=f"No such provider: {provider}", status=400)
        if not models_config.key(provider):
            return _models_page(
                error=f"{provider} has no key yet; set the key before fetching the catalog.", status=400)
        _fetch_catalog(provider)
        return RedirectResponse("/models", status_code=303)

    @app.post("/models/limits")
    @needs(perm.MODEL_WRITE)
    def models_limits(request: Request, draft_cap_hour: str = Form(""),
                      draft_cap_month: str = Form(""),
                      draft_max_jobs: str = Form("")):
        raw = {"draft_cap_hour": draft_cap_hour,
               "draft_cap_month": draft_cap_month,
               "draft_max_jobs": draft_max_jobs}
        values = {}
        for name, text in raw.items():
            if not text.strip():
                continue
            if not text.strip().isdigit():
                return _models_page(error=f"{name} must be an integer.", status=400)
            values[name] = int(text.strip())
        try:
            models_config.set_limits(by=_who(request), **values)
        except ValueError as exc:
            return _models_page(error=str(exc), status=400)
        identity.log("model.limits", actor=_who(request),
                     detail=", ".join(f"{k}={v}" for k, v in sorted(values.items())))
        return RedirectResponse("/models", status_code=303)

    @app.get("/audit", response_class=HTMLResponse)
    @needs(perm.AUDIT_READ)
    def audit_page(limit: int = 200):
        return HTMLResponse(views.audit(identity.audit(limit)))

    return app


def _parse_field(field: str, form):
    """Reads the form according to the field's shape. Read with the wrong shape, practice
    collapses from three rungs into one sentence."""
    from framework_reader.interpret.model import ALL_FIELDS
    from framework_reader.web.views import LINES, RUNGS

    if field not in ALL_FIELDS:
        raise ValueError(f'No "{field}" field exists.')
    if field == RUNGS:
        rungs = {
            str(n): str(form.get(f"v{n}", "")).strip()
            for n in (1, 2, 3)
            if str(form.get(f"v{n}", "")).strip()
        }
        return rungs or None
    text = str(form.get("value", "")).strip()
    if not text:
        return None
    if field in LINES:
        return [line.strip() for line in text.splitlines() if line.strip()]
    return text
