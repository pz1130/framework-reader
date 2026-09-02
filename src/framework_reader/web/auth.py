"""登录、会话、CSRF。见 2026-08-23 网页服务化设计 §1.5、§4.1、§5.5

**默认拒绝。** 门设在一个地方——`FastAPI(dependencies=[...])` 对每条路由都生效，
新加的路由自动被挡住。放行清单是显式的白名单，不是「记得加装饰器」。

S1 只判**你是谁**。判**你能干什么**（权限矩阵）是 S2 的事。

**身份体系没启用时不锁门。** 本机 `fr serve` 是这个产品今天的用法，
开箱就要求登录等于把人锁在自己的机器外面；而且首个管理员也得有办法进来。
`fr account invite` 发出第一个邀请的那一刻，门就锁上了——不是等对方接受，
否则这中间是一段对所有人敞开的窗口。
"""
from starlette.requests import Request

from framework_reader.identity.permissions import allows, permissions_of

COOKIE = "fr_session"

# 这几条必须在没登录时也能到达，否则登不进来
PUBLIC_PREFIXES = ("/login", "/logout", "/invite", "/auth", "/static", "/favicon",
                   "/favicon.ico", "/favicon.svg", "/apple-touch-icon.png",
                   # 自定义 logo：登录页（裸页）也要显示它，必须公开。
                   # 里面只有管理员上传的一张图，没有任何会话信息。
                   "/branding")


class NeedsLogin(Exception):
    """没有有效会话。GET 引到登录页，POST 直接拒绝。"""

    def __init__(self, next_url: str = "/") -> None:
        self.next_url = next_url


class BadCsrf(Exception):
    """写操作没带上本次会话的令牌。"""


class Forbidden(Exception):
    """登录了，但这个角色不能做这件事。"""

    def __init__(self, permission: str) -> None:
        self.permission = permission


class Unlabelled(Exception):
    """路由没声明需要什么权限。

    这是**代码缺陷**，不是用户错误，所以它拒绝请求而不是放行。默认拒绝的
    意思就是：忘了标注的那条路由必须坏掉，坏在测试里最好，坏在生产上也比
    悄悄放行强。见设计 §1.5
    """


def needs(permission: str):
    """给路由声明它要什么权限。判定在守卫里做，这里只贴标签。

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
    """`store_of()` 返回 IdentityStore。做成可调用是为了测试能换一个库。

    `locked=True` 表示「不管有没有账号，都要求登录」。接了 Entra 就该这样：
    配了 IdP 说明这是联网部署，这时门还敞着，等于第一个走进来的是任何人。
    也可以传**返回布尔的 callable**：设置页里保存的单点登录配置是运行时
    改的，锁门状态得每个请求现判，不能吃启动时的快照。
    """

    async def guard(request: Request) -> None:
        from framework_reader.web import views

        locked_now = locked() if callable(locked) else locked
        store = store_of()
        request.state.session = None
        request.state.login_enabled = locked_now or store.configured()
        # 每个请求都要设：漏设的那次，页面会拿到上一个请求的令牌。
        views.CHROME.set(("", ""))
        views.PERMS.set(None)

        if not request.state.login_enabled:
            # 还没有任何账号：本机单人用法，门不锁。见模块开头。
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
            # form() 的结果会被 Starlette 缓存在 Request 上，
            # 所以这里读一次不会把路由里的 form() 掏空。
            form = await request.form()
            if not _same(str(form.get("csrf", "")), session.csrf):
                raise BadCsrf

        # ---- 授权。默认拒绝：没标注的路由一律不放行 ----
        permission = permission_of(request)
        if permission is None:
            raise Unlabelled(request.url.path)
        if not allows(session.account.roles, permission):
            raise Forbidden(permission)

    return guard


def _same(a: str, b: str) -> bool:
    import hmac

    return bool(a) and hmac.compare_digest(a, b)
