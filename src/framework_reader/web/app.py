"""本地 Web 壳。主 spec §7.3.6

**只包一层。** 数据与业务逻辑全在 QueryAPI 与既有模块里；这里不许写裸 SQL
（主 spec §8①），也不许有业务判断——那样 Web 与 CLI 会慢慢长出两套行为。

本地部署：不做账号、不做租户隔离。用户导入的东西写进他自己机器上的用户库，
一个字节都不出网。主 spec §7.3.5
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
    """用户自己导入的框架。tier 是唯一判据，别拿编号前缀猜。"""
    from framework_reader.schema.entities import LicenseTier

    return view is not None and view.tier == LicenseTier.U_USER


# 给模型看的对话轮数。**必须封顶**——每一句都要把历史重新喂一遍，
# 不封顶的话聊得越久每句越贵，三小时前那个已经放弃的说法还会一直跟着。
CHAT_CONTEXT_TURNS = 6


def create_app(
    db: Path = DEFAULT_DB, draft_runner=None, rewrite_runner=None,
    user_db: Path | None = None, identity_db: Path | None = None,
    secure_cookies: bool = False, entra=None, entra_fetch=None,
    http_get=None, probe_runner=None, outline_runner=None, shape_runner=None,
    chat_runner=None, search_runner=None, body_rewrite_runner=None,
) -> FastAPI:
    """`user_db` 是这套部署的用户库。整个组织**共用一个**——

    这个产品是一个安全团队协作一套材料，不是多个客户公司各管各的。
    用户之间不隔离数据，隔离的是**动作**（谁能改、谁能签字），见
    `docs/superpowers/specs/2026-08-23-hosted-service-rbac-aad-design.md` §3。

    参数化只为可测：默认仍是 `$FRAMEWORK_READER_HOME/user.sqlite`。

    `draft_runner` / `rewrite_runner` / `search_runner` 同样只为测试留：
    默认就是真调模型，测试塞一个不出网的替身。
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

    # 与身份层同一个运营库：这是部署配置，不是业务数据。
    models_config = ModelConfig(identity_db)

    entra_config = entra if entra is not None else EntraConfig.from_env()

    # 设置里保存且启用的单点登录配置优先，环境变量兜底。**每个请求现取**——
    # 管理员在设置页存完配置，下一个请求就要生效，不能吃启动时的快照。
    def _entra_client():
        saved = identity.sso_config()
        if saved and saved.get("enabled"):
            from framework_reader import crypto

            try:
                secret = identity.sso_secret()
            except crypto.SecretError:
                # 主密钥没配或解不开：签名交换反正做不成，按没配置处理，
                # 让登录页回到口令登录，而不是让整个站点 500。
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

    # 启动时的环境变量快照仍然负责 cookie Secure 的兜底；设置里保存的
    # https 回调地址每个请求现判（见 _set_cookie）。
    secure_cookies = secure_cookies or entra_config.redirect_uri.startswith("https://")

    # 门设在一处：对每条路由都生效，新加的路由自动被挡住。
    # 靠「记得加装饰器」的写法，漏一条就是一个未鉴权入口。设计 §1.5
    #
    # 配了 Entra 也算锁门：接了 IdP 就说明这是联网部署，这时还敞着，
    # 等于第一个走进来的是任何人。传 callable 是因为设置页里的单点登录
    # 配置是运行时改的，锁门状态不能吃启动快照。
    # 关掉 Swagger 与 openapi.json：它们由 FastAPI 特殊注册，**绕过上面这道门**，
    # 等于一个不需要登录就能拿到的完整路由清单。内部工具不需要它们。
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
        # 路由忘了标权限。这是代码缺陷，所以拒绝而不是放行——
        # 坏在测试里最好，坏在生产上也比悄悄放行强。设计 §1.5
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
        """「不能给自己加角色」比的是 account_id。没登录（本机用法）时给 None。"""
        session = getattr(request.state, "session", None)
        return session.account.id if session else None

    def _local_user() -> str:
        import getpass

        return getpass.getuser()

    def _default_runner(key: str, user_db: Path | None = None, only=None):
        """key 带冒号就是一条控制，否则是一个框架。任务表用同一个键。

        `user_db` 由发起请求的那一刻算好传进来——后台线程里没有请求上下文，
        在线程里再解析租户会解析成默认那个，等于把 A 的活干到 B 的库里。
        """
        from framework_reader.interpret.run import draft_framework, fill_blanks_one

        if ":" in key:
            return fill_blanks_one(db, key, user_db, overlay=True)
        # 网页上一律七个字段写全。内置框架也 overlay 到用户库——那是工作
        # 副本，不进 content/interpretations/。
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
        # 每个请求一个连接：底层驱动默认禁止跨线程共享连接。
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
        """默认的 {"detail":"Not Found"} 看起来就是「点了没东西」。

        最常见的成因是服务还跑着旧代码——uvicorn 不带 --reload，
        路由在启动那一刻定死。所以这一页要把「重启一下」直接说出来。
        """
        return HTMLResponse(views.page(
            "Not found",
            "<h2>This address does not exist</h2>"
            '<p class="note">If this link worked a moment ago, the local service is probably running old code: '
            "uvicorn does not hot-reload, so restart <code>fr serve</code> after updating the code.</p>"
            '<p><a href="/">Back to home</a></p>',
        ), 404)

    # ---------- 登录 / 邀请 ----------

    def _set_cookie(response: Response, token: str) -> Response:
        # Secure 只在 https 下有意义；本机 http 调试时带上会让 cookie 直接不发。
        # 所以按部署方式给，不写死。反向代理终止 TLS 时会带 x-forwarded-proto。
        response.set_cookie(
            COOKIE, token, httponly=True, samesite="lax", path="/",
            # 设置里保存的单点登录配置每个请求现判：管理员存了 https 回调
            # 地址，下一个登录的 cookie 就该带上 Secure，不等重启。
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
        """只跳站内路径。放行任意 next 就是一个开放重定向。"""
        return target if target.startswith("/") and not target.startswith("//") else "/"

    @app.get("/auth/entra")
    def entra_start(next: str = "/"):
        """点「用公司账号登录」。state / nonce / verifier 都存服务端，用一次即删。"""
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
            # Entra 自己说不行（没被指派 App Role 是最常见的一种）。
            # 原样把它的代号显出来——管理员要拿它去 Entra 里查。
            return refuse(f"The company sign-in service refused this sign-in: {error}",
                          error_description or "Show this message to an administrator.")
        flow = identity.take_oidc_flow(state)
        if flow is None:
            # state 认不出 = 这次回调不是我们发起的那一次。拿别人的 code
            # 配自己的 state，能把你登进他的账号。
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
        """搜索工作台。三样东西：搜索框、经常搜索、今天学三条。

        不要在 / 上再摆一个「内置框架 + 我导入的」的目录页——那是
        /frameworks 的事。本页是「你来这个系统要做什么」，不是「你已
        经有什么」。`roll` 是「换一批」的批次号：0 是按日期的默认三条，
        ≥1 换一组，同一个批次当天稳定。"""
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
        # 待确认的初稿数。审阅是签字人的日常入口，放在他每天打开的第一页；
        # 一条不剩时不渲染——安静的页面比一枚恒为零的徽章有用。
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
        # 网页起草一律 overlay 到用户库当工作副本——导入的、内置的都一样，
        # 不进 git。views.framework 看 pending 决定画不画「起草 N 条」。
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
            # CSF 的正文是官方 label 兑现的；用户贴/导入的标「你导入的原文」。
            body_label=("Official text" if reader.body_is_official(control_id)
                        else "Your imported text"),
            # 内置框架也能改了（见 `_mine_or_400` 的说明）。
            # 用户改过的字段逐字段盖住内容包那一版，见 query/api.py 的合并视图。
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
        """谁签的、什么时候签的。只有用户库存得下这个——内容包里没有这一栏。"""
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
        """这条解读是从哪条旧条款继承来的。继承产物落用户库，这里跟着查用户库。

        不看 `_is_mine`：继承对内置框架开放（纯复制不花钱、不绕签字），
        拦显示等于把官方映射的价值藏一半。
        """
        from framework_reader.interpret.user_store import UserInterpretationStore

        store = UserInterpretationStore(_user_db())
        if not store.exists(control_id):
            return ""
        return store.load(control_id).provenance.inherited_from or ""

    def _mine_or_400(control_id: str):
        """这条控制在不在。返回 (view, 错误响应)。

        **早先这里还拦着内置框架**，理由写的是「受版权原文不得出网」。
        查下来那个理由不成立：

        - NIST CSF 2.0 与 800-53 是 tier A（美国政府作品，公共领域）
        - ISO 27002 是 tier C，但库里存的是**自写** label（`label_is_original=0`），
          不是 ISO 原文
        - `original_text` 表 0 条——受版权原文根本没进过库

        出网守卫（`PayloadGuard` 拿 `original_text` 当禁词表）留着当拦网：
        哪天真有 C/D 原文进库，它会拦住。但拿「是不是内置」当判据是错的，
        代价是团队九成时间在用的 CSF 和 800-53 上一句都问不了。

        名字留着不改：调用点太多，而它现在的语义就是「这条在不在」。
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
        """一个字段「多大」。**只记大小，不记正文**——审计日志是只追加的，
        把制度正文灌进去就等于给它做一个永久副本：删不掉，导出时一并出去。
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

    # ---------- 条款正文（内置条款贴覆盖层，导入条款改本行） ----------

    def _editable_control(control_id: str):
        """正文编辑的准入：这条存在即可——内置、导入都放行。

        _mine_or_400 只查「这条在不在」，正合需要。内置条款的正文写
        control_body_override 覆盖层（用户库），内容库的官方基准一个字节
        不动；original_text 那块墓碑照旧——贴进来的原文进的是用户自己的库。
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
        """AI 帮改正文：只出提议稿回显在编辑框里，**一个字都不写库**——
        写库永远是「保存」那一下，和字段重写同一道闸。"""
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
        # 审计只记大小不记正文——跟字段编辑同一个理由：审计日志只追加，
        # 把正文灌进去就等于给它做一个删不掉的永久副本。
        identity.log("control.body_edit", actor=_who(request) or _local_user(),
                     detail=f"{control_id}: {len(before)} chars -> {len(body)} chars")
        return RedirectResponse(f"/c/{control_id}", status_code=303)

    def _charge(request: Request, controls: int, what: str):
        """花钱之前过三道闸：每人每小时、全组织每月、同时几个任务。

        返回 None 表示放行；否则返回那一页拒绝。**拒了不记账**——
        拒了还扣，等于第二次更容易被拒。
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
        # 记在**开跑的那一刻**，不是跑完之后。跑完记的话，跑挂了就没有任何
        # 痕迹说明有人按过这个按钮、花过这笔钱。
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
        # 重写和起草共用一道闸：它们的共同点不是「都用 AI」，是**都花钱**。
        over = _charge(request, 1, f"{control_id} · {field}")
        if over is not None:
            return over
        try:
            value = run_rewrite(control_id, field, instruction)
        except Exception as exc:                              # noqa: BLE001
            # 形状不对、key 没设、模型抽风——都不落盘，原样退回让用户再提一次。
            return back(f"Rewrite failed: {exc}")
        # 要求是他提的，字是模型写的，所以标 inferred。
        write_field(UserInterpretationStore(_user_db()), control_id, field, value,
                    basis=Basis.INFERRED)
        # **和手改分成两个事件。** 谁写的要能分出来，这是这个产品的地基。
        # 要求本身不进日志——那是他打的字，可能带公司内部系统名。
        _log_field(request, "interpretation.rewrite", control_id, field,
                   current, value)
        return RedirectResponse(f"/c/{control_id}", status_code=303)

    # ---------- 条款页上的 AI 对话 ----------
    #
    # **只在自己导入的框架上开。** 内置框架的正文是 Tier C/D 受版权原文，
    # 一个字不许出网（主 spec §9）。判据用 `_mine_or_400`，和改解读同一个。

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
        # 守卫用**真的**受版权原文清单，不是空守卫：这条路径上正文来自
        # 用户自己的框架，但守卫是最后一道拦网，不该因为「应该不会有」就撤掉。
        guard = PayloadGuard(reader.forbidden_outbound_texts())
        from framework_reader.llm.guard import GuardedClient

        guarded = GuardedClient(client, guard)
        return guarded.complete(
            load_prompt("clause_chat"),
            [Message(role="user", content="\n".join(lines))],
            model=model, max_tokens=4096)

    def _one_turn(request: Request, control_id: str, said: str):
        """跑一轮对话，回 (给人看的话, 提议, turn_id)。

        表单那条路和浮窗那条 JSON 路**共用这一个**——两条路各写一遍，
        迟早一条有闸另一条没有。
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
        # 问一句 = 一次调用 = 一笔钱。和起草同一本账、同一道闸。
        over = _charge(request, 1, f"{control_id} · chat")
        if over is not None:
            return over
        _one_turn(request, control_id, said)
        return RedirectResponse(f"/c/{control_id}", status_code=303)

    @app.post("/c/{control_id}/chat.json")
    @needs(perm.INTERPRETATION_DRAFT)
    async def clause_chat_json(control_id: str, request: Request,
                               message: str = Form(""), quote: str = Form("")):
        """浮窗用的。**它一个字都不写解读**——写库仍然走上面那条表单路，
        那条路上挂着预检、审计、和「点头才写」那道闸。
        """
        from framework_reader.interpret.render import FIELD_LABELS

        _view, refused = _mine_or_400(control_id)
        if refused is not None:
            return JSONResponse({"reply": "No such control.", "turn_id": "",
                                 "fields": []}, 404)
        said = message.strip()
        if not said:
            return JSONResponse({"reply": "", "turn_id": "", "fields": []})
        # 选中的那段话是这次提问的上下文，不带上就白选了。
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
        """**模型说的话到这一步才进库。** 中间隔着人点的这一下。"""
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
        # 刷新页面就会重发一次 POST。写两次库、记两条审计是最难查的那种重复。
        if not store.mark_applied(turn_id):
            return RedirectResponse(f"/c/{control_id}", status_code=303)

        written = UserInterpretationStore(_user_db())
        for update in turn.proposal:
            field = update["field"]
            before = (api().interpretation(control_id).get(field) or {}).get("value")
            # 要求是人提的，字是模型写的，所以标 inferred。
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

        # 签字人是**登录的那个人**，不是跑服务器的系统账号。
        # 用 getpass.getuser() 的话，全组织的签名都会写成同一个名字——
        # 而「这段话有人认领」正是这个产品的立身之本。
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
        # 确认是产品的核心动作，「谁认领的」必须可追溯。设计 §4.4
        identity.log("interpretation.confirm", actor=signer, detail=control_id)
        # 审阅队列里点的确认直接翻到下一条——一条条点进条款页再出来，
        # 一千条的初稿就把人磨没了。签字本身没变快，变快的只是找下一条。
        if next:
            return RedirectResponse("/review", status_code=303)
        return RedirectResponse(f"/c/{control_id}", status_code=303)

    @app.get("/review", response_class=HTMLResponse)
    @needs(perm.CONTENT_READ)
    def review_queue(after: str = "", before: str = ""):
        """审阅队列：一次一条 AI 初稿，确认或跳过，键盘左右翻。

        签字必须一条一条签（批量一键确认等于把「逐条过眼」从前门放出去），
        但「找下一条看什么」不该花人的时间。确认按钮 POST 的是既有的
        /c/{id}/confirm，带 next=1 翻下一条——签字逻辑只有一份。
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
        """队列里的定位。after/before 是上一页的当前条——跳过、确认之后
        从它旁边接着走，翻到头就绕回另一端。找不到参照（链接太旧、
        条款已删）就回到队头。"""
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
            # 一条自评都没有：这一页没有内容可给，只有下一步可指。
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
        """一行一份表单：control_id 已立项就是改，没立项就是补录一条。

        补录表单里人填的是短编号（`4.1`），这里拼回完整 id——条款号的
        稳定前缀就是框架 id，见 spec §8②。授权矩阵的逐格测试会带着
        空表单打进来——control_id 为空时原地回台账页，别让人看到 500。"""
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
        """把差距报告里还没立项的条目一次立项。已经立项的不动——
        重复点这个按钮不会把人填的负责人跟期限冲掉。"""
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
        # 800-53 有一千多条叶子。每人每小时默认 300，一次点完会撞上限。
        # 这一趟只跑预算里还能装下的，跑完再点。
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
                # 测试注入的 runner 只有两个参数。
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
        """两段共用一份数据。导入的那几行多带导入时间与来源文件名。"""
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
        """字面没命中时才走到这里。只把用户那句话发给模型，不把条款目录送出去。"""
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
        """收藏夹里可能存着旧地址。别让人撞一个 404。"""
        return RedirectResponse("/frameworks", status_code=303)

    def _deletable(framework_id: str):
        """(框架, 错误响应)。只有自己导入的能删——内置框架随内容包走。"""
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
        """**必须把编号原样打一遍。** 它会连着毁掉这个框架下所有的自评和签字，
        一个手滑点不掉几十小时的工作。和 `fr` 那套删除动作一个规矩。
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

    # 文档（.docx / .pdf / .txt / .md）走 AI 切分那条管线；表格直接落库。
    # 见 2026-08-25 AI 导入设计 §5.1
    _DOCUMENT_SUFFIXES = (".docx", ".pdf", ".txt", ".md", ".markdown")

    def _extractor_client():
        """抽结构用的 client。空守卫：payload 是用户自己的制度，
        不是 Tier C/D 原文——与 `fr llm check` 的探针同一个用法。设计 §6"""
        from framework_reader.llm.config import effective_registry
        from framework_reader.llm.guard import PayloadGuard

        registry, key_lookup = effective_registry(config=models_config)
        return (registry.build("extractor", guard=PayloadGuard([]),
                               key_lookup=key_lookup),
                registry.role("extractor").model)

    def _shape_table(request: Request, framework_id: str, name: str,
                     filename: str, sheets, fail):
        """表头认不出来：让模型看一眼这张表长什么样。设计 §5.1

        **只在确定性解析失败后才走这里。** 表头在第一行那条路是免费的、
        瞬时的、不会错，没理由换成一次模型调用。
        """
        from framework_reader.llm.config import BudgetError
        from framework_reader.llm.registry import MissingApiKeyError
        from framework_reader.userframework import table_ai
        from framework_reader.userframework.import_draft import ImportDraftStore
        from framework_reader.web import jobs

        # 认不出表头时**为什么没让 AI 帮忙**，要说出来。不说的话，
        # 管理员只会看到一句「表头里找不到编号」，而他有别的路可走。
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
            # 模型指了哪张表就校验哪张。没指就用第一张。
            chosen = by_name.get(shape.sheet, sheets[0][1] if sheets else [])
            shape, error = table_ai.validate_shape(shape, chosen, sheet_names=names)
        identity.log("framework.tableshape", actor=actor,
                     detail=f"{framework_id} <- {filename}, read as "
                            f"{shape.kind if shape else 'nothing recognisable'}")
        if shape is None:
            # 模型也没认出来。退回那条人话报错，让人看见它到底看见了什么。
            return None, f"AI could not read this table's structure either ({error})."
        if shape.kind == "document":
            # 一份制度贴进了 Excel。硬凑列映射会生成一整张假清单。
            # 指了工作表就只摊那一张，没指就整本摊平。
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
        """文档导入：抽文本 → 预检 → 切分 → 落预览态。**确认前不写框架库。**"""
        from framework_reader.llm.config import BudgetError, effective_registry
        from framework_reader.llm.guard import PayloadGuard
        from framework_reader.llm.registry import MissingApiKeyError
        from framework_reader.userframework import outline as outline_mod
        from framework_reader.userframework.extract import (
            UnsupportedDocument, extract,
        )
        from framework_reader.userframework.import_draft import ImportDraftStore
        from framework_reader.web import jobs

        # 文档导入调模型，花的是组织的钱，所以门槛比表格导入高一档。
        # permissions.py：admin 管系统，**不含起草与确认**。设计 §4.1
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

        # 预检：先算要发几次，闸不过就一个请求都不发。设计 §4
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
            # 没配 key 不该是一个 500。**这一步在扣额度之后**，所以要退回来。
            models_config.refund_draft(actor, planned)
            return fail(f"{exc}")
        run = outline_runner or outline_mod.outline_document

        def work(report) -> str:
            """后台跑。**预检、权限、key 都在上面同步做完了**——
            那些是立刻能知道的，扔进后台只会让人先看三秒转圈再看到
            「你没权限」。这里剩下的只有真正耗时的那一段。
            """
            result = run(text, client=client, model=model, on_chunk=report)
            # 审计只记发生了这件事。**制度正文一个字都不进日志。**
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

        # 每次上传一个独立的临时文件。原先是固定文件名 `_upload{后缀}`——
        # 单人时无害，两个人同时导入就是一个人的表被另一个人的覆盖掉。
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
            # 挨个工作表试。「说明页在前、真表在后」是最常见的排法，
            # 只读 book.active 的结果是整份文件白导。
            _, controls = parse_any_sheet(sheets)
            if controls is None:
                # 都认不出来。让用户去改自己的表，就是把我们的问题推给他。
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
        """切分进度。跑着的时候自己刷新——否则人只能盯着一个不动的页面猜。"""
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
        # 正文在这儿才从原文截出来。草稿里存的是行号，不是正文——
        # 存一份正文的副本，就等于给了它一个可以和原文对不上的机会。
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
        # 每次提交都把框里的编号与标题写回草稿——合并和确认都要它们，
        # 而「改完标题再点合并」不能把改动丢掉。
        edited = [_with_edits(s, form, i) for i, s in enumerate(draft.spans)]
        kept_keys = set(form.getlist("keep"))

        if form.get("merge"):
            index = int(form["merge"])
            if index < 1 or index >= len(edited):
                return RedirectResponse(f"/import/{draft_id}", status_code=303)
            # 与紧邻的上一条合并，不管它勾没勾。行号取并集——两段之间
            # 没被覆盖的行一并并进来，那正是想要的。编号与标题取上一条的。
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

        # 编号是控制 ID 的一半，而 user_control.id 是主键——重号和空号都会在
        # `add_framework` 里炸成 IntegrityError。那一刻人已经在预览页改了半天，
        # 而炸完什么都没落库。**在这儿拦住，并说清楚是哪一个。**
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
        """把框里的编号标题写回。**人改过的就归人**——AI 起的名字被改掉之后
        再标「AI 起的」，是把功劳和责任都记错了。
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
        """确认被拦下。**一条都不写库**——半张框架比没有框架糟。"""
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

    # ---------- 成员与审计 ----------
    #
    # 管账号原先只有 CLI（`fr account grant`）。托管服务里管理员未必有
    # 服务器的 shell——能在 CLI 做而界面上做不了的管理动作，等于没做。

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
        """第一个管理员。在这之前只有 CLI 一条路。

        **门闩是 `configured()`，不是页面上藏起表单。** 一旦有了账号（或者
        发出过邀请），这条路由必须死透：否则它就是一条绕开邀请、给自己发
        管理员的路，而且守卫在本机模式下连 CSRF 都不校验。

        关门后拒的是 **409 不是 403**：403 在这套代码里专指「你这个角色不能
        做这件事」，授权矩阵的遍历测试靠这条约定分辨真假。这里拒的理由和
        角色无关，占了 403 就等于在那张矩阵上戳个假洞。
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
        # 直接种会话：门在这一刻锁上，让人刚建完就被踢回登录页很蠢。
        # 和 invite_submit 是同一个路子。
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
        # 链接直接渲染，不走重定向——令牌进 URL 就会躺在代理日志与浏览器历史里。
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
        """开关本身归 role:grant 管，因为它管的就是授权。关掉会留痕。设计 §4.3"""
        identity.set_self_grant(allowed == "1", by=_who(request))
        return RedirectResponse("/members", status_code=303)

    # ---------- 配套文档 ----------
    #
    # 起草器写出来的是通用建议。这个团队真正的落地方式写在他们自己的制度里，
    # 而那一行不是模型能猜出来的。

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
        # 内部制度进了服务器、并且会进模型的 payload。谁传的必须留痕。
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

    # ---------- 模型与 key ----------
    #
    # 「用户接入自己的 AI」的正解：不是把 key 写进服务器的环境变量（那要
    # shell，改一次还要重启），是管理员在界面上填、加密落库、脱敏回显。

    def _models_page(error: str = "", notice: str = "", status: int = 200,
                     focus: tuple[str, str, str] | None = None):
        from framework_reader import crypto
        from framework_reader.llm.config import effective_registry
        from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry

        presets = [{"id": p.id, "note": p.note, "verified": p.verified,
                    "custom": False}
                   for p in LLMRegistry.load(DEFAULT_REGISTRY_PATH).providers]
        custom = models_config.custom_providers()
        # 自定义端点也要能在角色下拉里选中——否则配了也用不上。
        # verified=True：它是你自己的端点，「我们验没验过」这个问题不适用。
        presets += [{"id": pid, "note": row["base_url"], "verified": True,
                     "custom": True}
                    for pid, row in custom.items()]
        # 显示**实际生效**的值，不只是这一页配过的：这一页要回答的问题是
        # 「现在到底谁在收我们的钱」，而不是「我在这儿点过什么」。
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
        """「测一下」：真发一次最小请求。`probe_runner` 只为测试注入。"""
        from framework_reader.llm import probe as probe_mod
        from framework_reader.llm.config import effective_registry

        registry, _ = effective_registry(config=models_config)
        preset = registry.preset(provider)
        run = probe_runner or (
            lambda preset, model, api_key: probe_mod.probe_model(
                preset, model, api_key))
        # **和起草走同一个取 key 路径**（先库、后环境变量）。只看库的话，
        # 服务器上用环境变量配好的厂商会被报成「还没配 key」，而它跑得好好的。
        return run(preset, model, _key_for(preset))

    def _key_for(preset) -> str | None:
        return models_config.key_lookup()(preset.api_key_env)

    def _known_providers() -> set[str]:
        from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry

        return ({p.id for p in LLMRegistry.load(DEFAULT_REGISTRY_PATH).providers}
                | set(models_config.custom_providers()))

    def _fetch_catalog(provider: str) -> None:
        """拉一次目录并落库。**任何失败都不得让调用方失败**——
        保存 key 是主动作，拉目录是搭便车的那一个。

        `http_get` 只为测试注入（与 `entra_fetch` 同一个模式）。默认 None →
        catalog 用它自己的 `_default_get`：真实出网收在那一个函数里。
        """
        from framework_reader.llm.catalog import CatalogError, fetch_models
        from framework_reader.llm.config import effective_registry

        registry, _ = effective_registry(config=models_config)
        try:
            preset = registry.preset(provider)
        except Exception:  # noqa: BLE001 —— 厂商刚被删掉之类
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
        """门槛取 member:read——四个角色都有它，本机单人模式 `may()` 恒真。

        页面内每一块再按权限单独判：这一页本身不是一个动作，
        它只是把入口收在一处（§1.2 权限的单位是动作，不是页面）。
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
        # CSP 连栅格图一起给：直接在地址栏打开 logo 也不许执行任何脚本。
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
        """表单里 secret 留空 = 沿用已保存的那份，不是清掉。"""
        return form_secret.strip() or identity.sso_secret()

    @app.post("/settings/sso")
    @needs(perm.MEMBER_MANAGE)
    def sso_save(request: Request, tenant_id: str = Form(""),
                 client_id: str = Form(""), client_secret: str = Form(""),
                 redirect_uri: str = Form(""), authority: str = Form(""),
                 enabled: str = Form("")):
        if not (tenant_id.strip() or client_id.strip() or client_secret.strip()):
            # 授权矩阵会拿空表单打每一条路由：空表单不许把已存的好配置抹掉。
            return RedirectResponse("/settings/sso", status_code=303)
        from framework_reader import crypto

        try:
            identity.save_sso_config(
                tenant_id=tenant_id, client_id=client_id,
                redirect_uri=redirect_uri,
                secret=_form_secret_or_saved(client_secret),
                authority=authority, enabled=enabled == "on", by=_who(request))
        except crypto.SecretError as exc:
            # 没配主密钥就拒绝落库（和模型 key 同一条规矩）：悄悄明文存，
            # 你会以为它是加密的，而它不是。
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
        """测的是**表单里现填的这一份**（空 secret 用已存的补上）——
        测完再存，存的就是测过的。出网只发一次发现文档请求。"""
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

        # 别的体检项没过也照样拉一次发现文档——一次把所有问题都亮出来，
        # 不让人改一项、测一次、再发现下一项。出网只这一发。
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
            # 预设里没有的厂商，端点是空的——存了也只是一个用不了的 key。
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
        # 审计里**只记发生了这件事**。key 一个字符都不进日志。
        identity.log("model.key", actor=_who(request),
                     detail=f"set {provider}")
        # 配完就顺手问一次「你这儿有哪些模型」。失败不影响 key 已经存好这件事。
        _fetch_catalog(provider)
        return RedirectResponse("/models", status_code=303)

    @app.post("/models/role")
    @needs(perm.MODEL_WRITE)
    def models_role(request: Request, role: str = Form(""),
                    provider: str = Form(""), model: str = Form(""),
                    key: str = Form("")):
        from framework_reader import crypto
        from framework_reader.web.views import ROLE_WHAT_FOR

        # 模型名现在只有一个框（datalist 既能选也能填），不再有「下拉 vs 手填」
        # 二选一那回事。从目录里复制粘贴常带尾空格，所以照样要 strip。
        model = model.strip()

        if key.strip():
            # 在这一块里填了 key：先存 key、拉目录，**这一次不改角色**。
            # 模型名框里此刻装的多半还是上一家的模型，拿它去配新厂商是错的。
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
        # 换 endpoint = 数据流向变了。这一条必须留痕。设计 §4.4
        identity.log("model.role", actor=_who(request),
                     detail=f"{role} → {provider} / {model.strip()}")
        return RedirectResponse("/models", status_code=303)

    @app.post("/models/role/test")
    @needs(perm.MODEL_WRITE)
    def models_role_test(request: Request, role: str = Form(""),
                         provider: str = Form(""), model: str = Form("")):
        """测这一组能不能用。**测的是表单里此刻选的那组，不是库里存的那组。**

        「选 → 测 → 存」比「存 → 测」少一步错误状态：不用先把一个没验证过的
        配置写进库，再回头验它。所以这条路径一个字都不写库。

        返回页 `focus` 到被测的那家，它的模型目录跟着渲出来——换厂商时
        「要选模型必须先有目录、要有目录必须先提交、提交又要求模型名非空」
        那个环，就是在这儿断开的。
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
            # **不回落到 default_model。** 那样人会以为测的是自己填的那个，
            # 而绿勾说的其实是另一个模型的事。
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
        # 留痕：这是一次真实出网，花的是组织的钱。设计 §4.4 同一条理由。
        # **模型回的内容不进日志**——万一有人拿这个框当聊天窗。
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
        """预设里没有的厂商：内网网关、Azure 部署、本机 vLLM/Ollama。

        加端点 = 决定框架正文发往哪里，所以只有 admin 能做，且必须留痕。
        """
        from framework_reader.llm.config import CustomProviderError

        try:
            models_config.set_custom_provider(
                provider, base_url=base_url, default_model=default_model,
                by=_who(request))
        except CustomProviderError as exc:
            return _models_page(error=str(exc), status=400)
        # 数据流向进审计。base_url 记下来，key 不在这条路径上。设计 §4.4
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
    """按字段的形状读表单。形状读错，practice 会从三档塌成一句话。"""
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
