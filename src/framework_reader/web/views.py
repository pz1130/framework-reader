"""本地 Web 壳的 HTML。与发布页共用设计令牌（publish/theme.py）。

服务端渲染，无前端框架——第一版不需要，上 React 是三个月，这是三天。
"""
from contextvars import ContextVar
from html import escape
from urllib.parse import quote

from framework_reader.assess.remediation import STATE_LABELS
from framework_reader.publish.theme import THEME_CSS

# 本次请求的外壳信息：(csrf 令牌, 登录人显示名)。
#
# 不逐个参数往十个 view 函数里穿，是因为**漏一个就是一个 CSRF 洞**，
# 而且新加的页面一定会漏。page() 是唯一的外壳函数，让它从这里取，
# 就没有「忘了传」这种失败模式。ContextVar 按任务隔离，并发请求不串。
CHROME: ContextVar[tuple[str, str]] = ContextVar("chrome", default=("", ""))

# 本次请求的权限集合。None = 没启用登录（本机单人用法），一切照旧显示。
#
# 页面上藏按钮是**体验**，不是授权——授权在守卫里判过了（设计 §1.2、§4.1）。
# 这里只是别让人点了才被拒。
PERMS: ContextVar[frozenset[str] | None] = ContextVar("perms", default=None)


def may(permission: str) -> bool:
    perms = PERMS.get()
    return perms is None or permission in perms


def logged_in() -> bool:
    """身份体系启用了没有。本机单人用法下没有「成员」这回事，别挂那个入口。"""
    return PERMS.get() is not None

_CSS = THEME_CSS + """
/* ---------- 工作台令牌：默认深色，可切浅色 ----------
   发布手册与工作台共用 THEME_CSS；手册是打印用的正式文档，保持浅色。
   THEME_CSS 的 prefers-color-scheme 媒体查询特异性是 (0,1,1)，比裸 :root
   高——系统深色的 Mac 上会把它那套青灰色压进来。所以这里的深色也挂
   :not([data-theme="light"]) 抵消它，两套各归各。
   切换由顶栏按钮写 <html data-theme>，localStorage 记住选择。 */
:root{
  --mono:"SF Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --han:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC",
    "Hiragino Sans GB","Microsoft YaHei","Source Han Sans SC",system-ui,sans-serif;
}
:root:not([data-theme="light"]){
  --ground:#000; --surface:#161617; --sunk:#1d1d1f;
  --ink:#f5f5f7; --body:#d2d2d7; --muted:#86868b;
  --rule:#2c2c2e; --accent:#2997ff; --accent-soft:#15304b; --ask:#ff6b5e;
  --topbar-bg:rgba(10,10,12,.9); --topbar-line:rgba(255,255,255,.08);
  --topsheen1:rgba(94,92,230,.30); --topsheen2:rgba(100,210,255,.24);
  --topglow:rgba(41,151,255,.55);
  --row-hover:#121214; --card-hover:#1b1b1e; --card-hover-line:#3a3a3c;
  --selection:rgba(41,151,255,.35);
}
:root[data-theme="light"]{
  --ground:#fff; --surface:#f5f5f7; --sunk:#e8e8ed;
  --ink:#1d1d1f; --body:#424245; --muted:#6e6e73;
  --rule:#d2d2d7; --accent:#0066cc; --accent-soft:#e1effc; --ask:#c93400;
  --topbar-bg:rgba(255,255,255,.62); --topbar-line:rgba(0,0,0,.06);
  --topsheen1:rgba(94,92,230,.08); --topsheen2:rgba(100,210,255,.06);
  --topglow:rgba(41,151,255,.15);
  --row-hover:#f2f2f4; --card-hover:#fafafc; --card-hover-line:#c7c7cc;
  --selection:rgba(0,102,204,.22);
}
body{margin:0;background:var(--ground);color:var(--body);
  font-family:var(--han);font-size:16px;line-height:1.65;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
a{color:var(--accent);text-decoration:none;transition:color .2s ease}
a:hover{text-decoration:underline}
::selection{background:var(--selection)}

/* 入场：内容区一次性淡入上浮，纯 CSS，无 JS。注意：动画不能挂在 .wrap
   上——顶栏是它的子元素且 sticky + 毛玻璃，祖先带着 transform 动画时
   Chrome/Safari 对 backdrop-filter 的采样会失效，下面滚上来的字会
   清晰地透进顶栏（全挤成一团）。所以只动画 .pagein（顶栏之外的内容）。 */
@keyframes rise{from{opacity:0;transform:translateY(12px)}
  to{opacity:1;transform:none}}
.wrap{max-width:62rem;margin:0 auto;padding:0 1.5rem 5rem}
.pagein{animation:rise .55s cubic-bezier(.16,1,.3,1) both}

/* 顶栏：sticky 全宽毛玻璃。负 margin 把它拉出 .wrap 的内边距，
   HTML 结构不动。底色取近实底：backdrop-filter 真生效时它是通透的
   玻璃；采样在某些浏览器下失效时，8% 以下的透光度也读不出下面的字，
   不会再和标题揉在一起。 */
.top{display:flex;gap:1.1rem;align-items:baseline;flex-wrap:wrap;
  position:sticky;top:0;z-index:20;margin:0 -1.5rem 2.4rem;
  padding:1.15rem 1.5rem;background:var(--topbar-bg);
  -webkit-backdrop-filter:saturate(180%) blur(28px);
  backdrop-filter:saturate(180%) blur(28px);
  border-bottom:1px solid var(--topbar-line)}
/* 顶栏空位的动态填充，两件都是装饰（pointer-events:none）：
   ::before 光标扫过时一团柔光跟着走（与卡片 spotlight 同一束光，
   坐标由 JS 写进 --tx/--ty，只在 pointer:fine 的设备上存在）；
   ::after 一条光带来回漂移，静置时也有呼吸。
   动效层的元素要压在内容下：z-index 0，内容不另设层。
   强度调过两轮：0.12/0.05 的版本在近黑顶栏上肉眼不可见——
   装饰看不见等于没做。 */
.top::before{content:"";position:absolute;inset:0;pointer-events:none;
  opacity:0;transition:opacity .5s ease;z-index:0;
  background:radial-gradient(34rem circle at var(--tx,50%) var(--ty,50%),
    var(--topglow),transparent 68%)}
.top:hover::before{opacity:1}
.top::after{content:"";position:absolute;inset:0;pointer-events:none;
  z-index:0;
  background:linear-gradient(100deg,transparent 24%,
    var(--topsheen1) 45%,var(--topsheen2) 58%,transparent 78%);
  background-size:220% 100%;background-position:100% 0;
  animation:topsheen 9s ease-in-out infinite alternate}
@keyframes topsheen{to{background-position:0% 0}}
.top > *{position:relative}
.top h1{font-size:1.45rem;margin:0;color:var(--ink);font-weight:600;
  letter-spacing:-.02em}
.top h1 a{color:inherit;text-decoration:none}
.brandlogo{height:2.2rem;width:auto;display:block}
.crumb{font-family:var(--mono);font-size:.8rem;color:var(--muted)}
.crumb a{color:var(--muted)}
.crumb a:hover{color:var(--ink);text-decoration:none}
.empty-gap{background:var(--surface);border:1px solid var(--rule);
  border-radius:14px;padding:1.4rem 1.5rem;margin:1.2rem 0}
.empty-gap p{margin:0 0 1rem}
.empty-gap p:last-child{margin:0}
a.cta{display:inline-block;background:var(--accent);color:#fff;
  padding:.55rem 1.2rem;text-decoration:none;font-size:.9rem;font-weight:500;
  border-radius:980px;transition:opacity .2s ease,transform .2s ease}
a.cta:hover{opacity:.85;text-decoration:none}
a.back{font-size:.85rem;color:var(--muted);text-decoration:none}
a.back:hover{color:var(--ink);text-decoration:none}
p.back{margin:0 0 .6rem}
.topnav{font-size:.85rem;text-decoration:none;color:var(--muted);
  padding:.3rem .85rem;border-radius:980px;
  transition:color .2s ease,background .2s ease}
.topnav:hover{color:var(--ink);background:rgba(255,255,255,.08);
  text-decoration:none}
.who{font-size:.8rem;color:var(--muted);margin-left:auto;padding-left:.8rem}
.who + .topnav{margin-left:.6rem}
/* 顶栏右侧导航组整体打包：装得下跟在品牌后面，装不下整组掉到第二行
   左缘（与 logo 同一左缘），绝不逐项散落——英文标签比中文宽一大截，
   逐项换行会把顶栏绞成乱麻。账号与退出靠 .who 自己的 margin-left:auto
   始终贴右，两种排法都不产生左右交错的锯齿。 */
.topright{display:flex;gap:1.1rem;align-items:baseline;
  flex-wrap:wrap}
/* 深浅切换：图标按钮，图标即当前主题（深色月亮、浅色太阳）。 */
.themebtn{background:transparent;border:0;padding:.25rem .5rem;margin-left:.4rem;
  color:var(--muted);border-radius:980px;display:inline-flex;
  align-items:center;align-self:center;
  transition:color .2s ease,background .2s ease}
.themebtn:hover{color:var(--ink);background:rgba(128,128,128,.18)}
.themebtn svg{width:1rem;height:1rem;display:block}
.i-sun{display:none}
:root[data-theme="light"] .i-sun{display:block}
:root[data-theme="light"] .i-moon{display:none}

/* ---------- 动效层：React Bits 风格，全部原生实现，零依赖 ---------- */

/* 极光背景：三团大光晕在黑底上缓慢漂移，z-index 压在内容下。 */
.aurora{position:fixed;inset:0;z-index:-1;overflow:hidden;pointer-events:none}
.aurora i{position:absolute;display:block;border-radius:50%;
  filter:blur(90px);opacity:.17;will-change:transform}
.aurora i:nth-child(1){width:44rem;height:44rem;left:-12rem;top:-18rem;
  background:radial-gradient(circle,#0a84ff,transparent 65%);
  animation:drift1 26s ease-in-out infinite alternate}
.aurora i:nth-child(2){width:38rem;height:38rem;right:-10rem;top:30vh;
  background:radial-gradient(circle,#5e5ce6,transparent 65%);
  animation:drift2 32s ease-in-out infinite alternate}
.aurora i:nth-child(3){width:30rem;height:30rem;left:30vw;bottom:-16rem;
  background:radial-gradient(circle,#64d2ff,transparent 65%);
  animation:drift3 38s ease-in-out infinite alternate}
@keyframes drift1{to{transform:translate(9rem,6rem) scale(1.15)}}
@keyframes drift2{to{transform:translate(-8rem,-5rem) scale(.9)}}
@keyframes drift3{to{transform:translate(6rem,-7rem) scale(1.2)}}
:root[data-theme="light"] .aurora i{opacity:.09}

/* Spotlight 卡片：光标坐标由 JS 写进 --mx/--my，一团光晕跟着走，
   边框沿光标方向亮起（mask 挖空中间只留 1px 描边）。 */
.card{position:relative}
.card::before{content:"";position:absolute;inset:0;border-radius:inherit;
  opacity:0;transition:opacity .4s ease;pointer-events:none;
  background:radial-gradient(30rem circle at var(--mx,50%) var(--my,50%),
    rgba(41,151,255,.13),transparent 55%)}
.card::after{content:"";position:absolute;inset:0;border-radius:inherit;
  opacity:0;transition:opacity .4s ease;pointer-events:none;padding:1px;
  background:radial-gradient(22rem circle at var(--mx,50%) var(--my,50%),
    rgba(120,180,255,.9),rgba(255,255,255,.12) 45%,transparent 70%);
  -webkit-mask:linear-gradient(#000,#000) content-box,linear-gradient(#000,#000);
  -webkit-mask-composite:xor;mask-composite:exclude}
.card:hover::before,.card:hover::after{opacity:1}
:root[data-theme="light"] .card::after{background:radial-gradient(
  22rem circle at var(--mx,50%) var(--my,50%),
  rgba(0,102,204,.55),rgba(0,0,0,.08) 45%,transparent 70%)}

/* 标题逐字浮现：span.ch 由 JS 拆出（无 JS 时标题原样，天然安全）。 */
h1 .ch,h2 .ch{display:inline-block;opacity:0;filter:blur(8px);
  transform:translateY(.35em);
  animation:chIn .65s cubic-bezier(.16,1,.3,1) forwards;
  animation-delay:calc(var(--ci,0)*38ms)}
@keyframes chIn{to{opacity:1;filter:blur(0);transform:none}}

/* 表格行瀑布入场：--ri 由 JS 按行号写，封顶 14 免得末行等太久。 */
.js tr{opacity:0;transform:translateY(8px);
  transition:opacity .5s ease,transform .5s cubic-bezier(.16,1,.3,1);
  transition-delay:calc(var(--ri,0)*26ms)}
.js tr.in{opacity:1;transform:none}

/* 进度条生长：JS 把内联 width 挪进 --w，入场后从 0 长到目标。 */
.js .bar i{width:0;transition:width 1s cubic-bezier(.16,1,.3,1) .3s}
.js .bar.in i{width:var(--w,0%)}

/* 主按钮扫光：一道高光从左划到右。 */
button,a.cta{position:relative;overflow:hidden}
button::after,a.cta::after{content:"";position:absolute;top:0;left:-80%;
  width:45%;height:100%;transform:skewX(-24deg);pointer-events:none;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.32),transparent);
  transition:left .55s ease}
button:hover::after,a.cta:hover::after{left:125%}

/* 主题切换的圆形扩散：新主题从按钮位置涂开（View Transitions API，
   不支持的浏览器回退为直接切换）。 */
::view-transition-old(root),::view-transition-new(root){animation:none;
  mix-blend-mode:normal}
::view-transition-new(root){animation:vtIn .45s ease-in forwards;
  clip-path:circle(0px at var(--vx,100%) var(--vy,0px))}
@keyframes vtIn{to{clip-path:circle(150% at var(--vx,100%) var(--vy,0px))}}
form.seek{display:flex;gap:.5rem;align-items:stretch;background:transparent;
  border:0;padding:0;margin:0 0 .4rem}
form.seek input[type=search]{flex:1;width:auto;padding:.6rem .9rem;font:inherit;
  font-size:.95rem;background:var(--sunk);color:var(--ink);
  border:1px solid transparent;border-radius:10px;
  transition:border-color .2s ease,background .2s ease}
form.seek input[type=search]:focus{outline:none;border-color:var(--accent)}
form.seek button{margin:0;white-space:nowrap}
form.tiny{display:inline;background:transparent;border:0;padding:0;margin:0}
form.tiny button{font-size:.85rem;padding:.35rem .95rem}
.mark{font-size:.62rem;font-weight:400;letter-spacing:0;margin-left:.5rem;
  padding:.05rem .4rem;border:1px solid var(--rule);border-radius:980px;
  color:var(--muted);vertical-align:.12em}
.mark.mine{border-color:var(--accent);color:var(--accent)}
.edit{font-size:.7rem;font-weight:400;margin-left:.5rem;text-decoration:none}
.signed{font-family:var(--mono);font-size:.75rem;color:var(--accent);
  margin:0 0 1rem}
.claim{margin:2rem 0 0;font-size:.9rem}
textarea{width:100%;padding:.6rem .7rem;font:inherit;font-size:.9rem;
  background:var(--sunk);color:var(--ink);border:1px solid transparent;
  border-radius:10px;margin-bottom:.9rem;resize:vertical;
  transition:border-color .2s ease}
textarea:focus{outline:none;border-color:var(--accent)}
h2{font-size:1.35rem;color:var(--ink);margin:2.6rem 0 1rem;font-weight:600;
  letter-spacing:-.02em}
.cards{display:grid;gap:1rem;
  grid-template-columns:repeat(auto-fill,minmax(17rem,1fr))}
.card{display:block;padding:1.2rem 1.3rem;background:var(--surface);
  border:1px solid var(--rule);border-radius:14px;text-decoration:none;
  color:inherit;
  transition:transform .25s cubic-bezier(.16,1,.3,1),
             border-color .25s ease,background .25s ease}
.card:hover{border-color:var(--card-hover-line);background:var(--card-hover);
  transform:translateY(-2px);text-decoration:none}
.card h3{margin:0 0 .35rem;font-size:1.02rem;color:var(--ink);font-weight:600}
.card .id{font-family:var(--mono);font-size:.72rem;color:var(--accent)}
.card .meta{font-size:.82rem;color:var(--muted);margin:.5rem 0 0;
  font-variant-numeric:tabular-nums}
.tag{display:inline-block;font-size:.68rem;padding:.1rem .45rem;
  margin-left:.4rem;border:1px solid var(--rule);border-radius:980px;
  color:var(--muted);vertical-align:.1em}
.tag.mine{border-color:var(--accent);color:var(--accent)}
.bar{height:3px;background:var(--sunk);margin-top:.65rem;border-radius:3px;
  overflow:hidden}
.bar i{display:block;height:3px;background:var(--accent);
  transition:width .6s cubic-bezier(.16,1,.3,1)}
table{width:100%;border-collapse:collapse;font-size:.92rem}
td{padding:.6rem .65rem;border-bottom:1px solid var(--rule);vertical-align:top}
td.c{font-family:var(--mono);color:var(--accent);white-space:nowrap;width:1%}
tr{transition:background .15s ease}
tr:hover td{background:var(--row-hover)}
td a{text-decoration:none;color:inherit}
td a:hover{color:var(--accent);text-decoration:none}
form{background:var(--surface);border:1px solid var(--rule);border-radius:14px;
  padding:1.3rem 1.4rem}
label{display:block;font-size:.8rem;color:var(--muted);margin:0 0 .3rem}
input[type=text],input[type=file]{width:100%;padding:.55rem .7rem;font:inherit;
  font-size:.9rem;background:var(--sunk);color:var(--ink);
  border:1px solid transparent;border-radius:10px;
  transition:border-color .2s ease}
input[type=text]:focus{outline:none;border-color:var(--accent)}
.row{display:grid;gap:.9rem;grid-template-columns:1fr 1fr;margin-bottom:.9rem}
button{font:inherit;font-size:.9rem;padding:.5rem 1.25rem;cursor:pointer;
  background:var(--accent);color:#fff;border:1px solid var(--accent);
  border-radius:980px;font-weight:500;
  transition:opacity .2s ease,transform .15s ease}
button:hover{opacity:.85}
button:active{transform:scale(.97)}
.hint{font-size:.8rem;color:var(--muted);margin:.8rem 0 0}
.err{background:var(--sunk);border-left:3px solid var(--ask);
  padding:.9rem 1.1rem;border-radius:0 10px 10px 0;
  margin:0 0 1.2rem;color:var(--ink);font-size:.9rem}
/* 提示块：苹果 callout。是「值得注意」不是「出错了」，别拿警条吓人：
   圆角、无左边条，前面一枚信息图标，正文用 --body 保证可读。 */
.callout{display:flex;gap:.7rem;align-items:flex-start;background:var(--sunk);
  border-radius:12px;padding:1rem 1.2rem;margin:.4rem 0 1.4rem;
  font-size:.9rem;color:var(--body)}
.callout svg{width:1.05rem;height:1.05rem;flex:none;margin-top:.18em;
  color:var(--muted)}
.callout p{margin:0}
.callout strong{color:var(--ink)}
.note{font-size:.82rem;color:var(--muted);margin:.4rem 0 1.4rem}
.draft{font-family:var(--mono);font-size:.75rem;color:var(--ask);margin:0 0 1rem}
.doc h4{font-size:.75rem;font-weight:600;letter-spacing:.08em;
  color:var(--muted);margin:1.3rem 0 .4rem;text-transform:uppercase}
.doc p,.doc ul,.doc ol{margin:0;max-width:62ch}
.doc ul,.doc ol{padding-left:1.1rem}
.doc code{font-family:var(--mono);font-size:.82rem;color:var(--accent);
  background:var(--sunk);padding:.05rem .3rem;border-radius:5px}
.empty{color:var(--muted);padding:2rem 0}
.doc p.own{white-space:pre-wrap;border-left:2px solid var(--accent);
  padding-left:.9rem;color:var(--ink)}

/* 滚动渐现：有 JS 才藏（.js 门），没 JS 默认全可见。 */
.js .reveal{opacity:0;transform:translateY(14px);
  transition:opacity .6s ease,transform .6s cubic-bezier(.16,1,.3,1)}
.js .reveal.in{opacity:1;transform:none}

/* 同屏多张卡按出现顺序错开，成批渐现有节奏；第 6 张起不再递增。 */
.cards .reveal:nth-child(2){transition-delay:.06s}
.cards .reveal:nth-child(3){transition-delay:.12s}
.cards .reveal:nth-child(4){transition-delay:.18s}
.cards .reveal:nth-child(5){transition-delay:.24s}
.cards .reveal:nth-child(n+6){transition-delay:.3s}

/* 偏好减弱动态：装饰性动画全关，内容直接出现（可及性）。 */
@media (prefers-reduced-motion:reduce){
  .pagein{animation:none}
  *,*::before,*::after{transition:none!important;animation:none!important}
  .js .reveal{opacity:1;transform:none}
  .aurora{display:none}
  .js tr{opacity:1;transform:none}
  .js .bar i{width:var(--w,0%)}
  h1 .ch,h2 .ch{opacity:1;filter:none;transform:none}
  .top::after{animation:none;opacity:0}
  .top::before{display:none}
}
"""


def _with_csrf(body: str, csrf: str) -> str:
    """给每个 POST 表单塞一个隐藏令牌。

    靠「记得在每个表单里加一行」是靠不住的：漏一个就是一个 CSRF 洞，
    而新加的表单一定会漏。所以在唯一的外壳函数里机械地插。
    """
    import re

    if not csrf:
        return body
    field = f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
    return re.sub(
        r'(<form\b[^>]*method="post"[^>]*>)', r"\1" + field, body, flags=re.I
    )


def _brand_logo_img() -> str:
    """设置里上传了 logo 就顶到品牌位；文件在数据目录 branding/ 下，
    由公开路由 /branding/logo 伺服（登录页也要显示）。?v= 改版时间戳——
    换图不换名，浏览器缓存不会把旧 logo 留着。"""
    from framework_reader import usage

    base = usage.home() / "branding"
    for ext in ("png", "jpg", "webp", "gif", "svg"):
        p = base / f"logo.{ext}"
        if p.exists():
            return (f'<img class="brandlogo" src="/branding/logo?v={int(p.stat().st_mtime)}" '
                    'alt="Framework Workbench">')
    return ""


def page(title: str, body: str, crumb: str = "", nav: str = "",
         csrf: str = "", who: str = "", bare: bool = False,
         crumb_href: str = "", wide: bool = False, topbar: str = "") -> str:
    """`bare` 给登录与邀请页用：那时顶栏的「导入框架」点了只会弹回登录页。

    `crumb_href` 给面包屑一个去处。**没有去处就别给** —— 导入页的面包屑是
    「导入」，它不对应任何框架，编一个链接出来只会点到一个不相干的地方。
    """
    if not csrf and not who:
        csrf, who = CHROME.get()
    """自评页的样式在 _ASSESS_CSS，统一从这里注入——只有一处 <style>。"""
    return (
        # 缺了它整站落进怪异模式：documentElement 不再代表视口，
        # 整页截图与 vh／100% 的高度链全按错的尺寸算。见 tests/web/test_doctype.py
        "<!doctype html>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title>"
        # **不引任何外部资源。** `<link rel=stylesheet>` 是渲染阻塞的：
        # 外部主机连不上时浏览器要等到超时才继续，那段时间页面是半死的，
        # 点什么都没反应。而这个产品给中国的安全团队用，
        # fonts.googleapis.com 在墙内连不上。
        #
        # 代价只有两款西文字体（IBM Plex Mono、Spectral）。中文正文本来就是
        # 系统字体（PingFang SC / 微软雅黑 / 思源黑体），一个字都不受影响；
        # 那两款也各自有回落链，见 publish/theme.py 的 --mono / --serif。
        f"<style>{_CSS}{_ASSESS_CSS}{_AUTH_CSS}</style>"
        # 首屏防闪：必须在首帧渲染前把主题定下来，否则选了浅色的人
        # 每次导航都先看见一闪而过的黑底。localStorage 拿不到就维持默认深色。
        '<script>try{var t=localStorage.getItem("fr-theme");'
        'if(t)document.documentElement.dataset.theme=t}catch(e){}</script>'
        # 极光背景，压在一切内容之下；aria-hidden，纯装饰。
        '<div class="aurora" aria-hidden="true"><i></i><i></i><i></i></div>'
        f'<div class="wrap{" wide" if wide else ""}"><div class="top">'
        + '<h1><a href="/">' + (_brand_logo_img() or "Framework Workbench") + "</a></h1>"
        + f'<span class="crumb">'
        + (f'<a href="{crumb_href}">{crumb}</a>' if crumb_href and crumb else crumb)
        + "</span>"
        # 顶栏动作位：紧跟面包屑，跟 sticky 顶栏一起常驻——拉三屏表格的
        # 时候按钮不沉底。POST 表单同样要过 _with_csrf——顶栏在 .pagein
        # 外，包不到。右侧导航组（.topright）装不下时整组掉到第二行右侧，
        # 绝不逐项散落——英文标签比中文宽一大截，逐项换行会绞成乱麻。
        + (_with_csrf(topbar, csrf) if topbar else "")
        # 右侧导航组整体打包：装得下就在第一行右侧，装不下整组掉到第二行
        # 右侧——绝不逐项散落。组的推靠 .topright 自己的 margin-left:auto，
        # 原先挂在「导入框架」一个元素上的做法，英文标签一长就失效。
        + '<div class="topright">'
        # 「框架」是顶栏第一个 tab——从任何页面都能跳回框架目录。
        + ("" if bare else
           '<a class="topnav" href="/frameworks">Frameworks</a>')
        # CSRF 锚点。nav 里塞一个隐藏的 input 就行——
        # 测试用正则抓 name="csrf" value="..."，任何页面都能抓到，
        # 不依赖 body 里有没有 POST form。裸页（登录、邀请）不发 token 不渲染。
        + (f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
           if csrf and not bare else "")
        # 导入原先只挂在主页最底下。停在框架页或条款页的人
        # 整个界面里找不到任何导入的地方——入口必须每页都在。
        + ("" if bare or not may("framework:import") else
           '<a class="topnav" href="/import">Import framework</a>')
        # 配套文档留在顶栏：它是干活时用的内容（上传本组织制度做接地），
        # 不是配置。成员、模型与 key、审计日志三样收进「设置」。
        + ("" if bare or not may("document:read") else
           '<a class="topnav" href="/documents">Documents</a>')
        + ("" if bare or not may("member:read") else
           '<a class="topnav" href="/settings">Settings</a>')
        # 深浅切换。图标显示当前主题：深色月亮、浅色太阳（与 .themebtn
        # 的 CSS 显隐规则一致）。bare（登录页）也给——那是进站第一页，
        # 更该能挑自己看着舒服的。
        + ('<button type="button" class="themebtn" aria-label="Toggle dark and light"'
           ' data-toggle-theme>'
           '<svg class="i-moon" viewBox="0 0 24 24" fill="none"'
           ' stroke="currentColor" stroke-width="1.8" stroke-linecap="round"'
           ' stroke-linejoin="round" aria-hidden="true">'
           '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'
           '<svg class="i-sun" viewBox="0 0 24 24" fill="none"'
           ' stroke="currentColor" stroke-width="1.8" stroke-linecap="round"'
           ' stroke-linejoin="round" aria-hidden="true">'
           '<circle cx="12" cy="12" r="4"/>'
           '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
           'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
           '</button>')
        + (f'<span class="who">{escape(who)}</span>'
           '<a class="topnav" href="/logout">Sign out</a>' if who and not bare else "")
        + "</div>"
        + "</div>"
        # 入场动画的宿主：只包顶栏之外的内容。毛玻璃顶栏的祖先链上
        # 不能有 transform 动画，否则模糊采样失效（见 .pagein 的注释）。
        f'<div class="pagein">{nav}{_with_csrf(body, csrf)}</div></div>'
        # 滚动渐现只用这几行原生脚本——不引任何外部 JS（同 <style> 的理由，
        # 见上）。没挂 .reveal 的元素不受影响；浏览器没有 JS 时全默认可见。
        + """
<script>
document.documentElement.classList.add('js');
/* 标题拆字：逐字 blur 浮现。只拆文本节点，h1 里的链接保留可点。 */
var _ci = 0;
function _split(el) {
  Array.from(el.childNodes).forEach(function (n) {
    if (n.nodeType === 3) {
      var f = document.createDocumentFragment();
      Array.from(n.textContent).forEach(function (ch, ix) {
        var s = document.createElement('span');
        s.className = 'ch';
        s.textContent = ch === ' ' ? '\u00A0' : ch;
        s.style.setProperty('--ci', Math.min(ix, 24));
        f.appendChild(s);
      });
      n.replaceWith(f);
    } else if (n.nodeType === 1 && n.tagName !== 'SVG') _split(n);
  });
}
if (!matchMedia('(prefers-reduced-motion: reduce)').matches)
  document.querySelectorAll('.top h1, .wrap h2').forEach(function (h) {
    _ci = 0; _split(h);
  });
var _io = new IntersectionObserver(function (entries) {
  entries.forEach(function (en) {
    if (en.isIntersecting) { en.target.classList.add('in'); _io.unobserve(en.target); }
  });
}, {threshold: 0.08});
document.querySelectorAll('.reveal').forEach(function (el) { _io.observe(el); });
/* 表格行瀑布入场 + 进度条生长。行号封顶，末行不用等到天荒地老。 */
document.querySelectorAll('.wrap table').forEach(function (t) {
  Array.from(t.querySelectorAll('tr')).forEach(function (tr, ix) {
    tr.style.setProperty('--ri', Math.min(ix, 14)); _io.observe(tr);
  });
});
document.querySelectorAll('.bar i').forEach(function (b) {
  b.style.setProperty('--w', b.style.width || '0%');
  b.style.width = '';
  _io.observe(b.parentElement);
});
/* Spotlight：光标坐标写进卡片变量，光晕与边框亮起跟着光标走。 */
if (matchMedia('(pointer:fine)').matches)
  document.addEventListener('pointermove', function (e) {
    var c = e.target.closest && e.target.closest('.card');
    if (c) {
      var r = c.getBoundingClientRect();
      c.style.setProperty('--mx', (e.clientX - r.left) + 'px');
      c.style.setProperty('--my', (e.clientY - r.top) + 'px');
    }
    /* 顶栏空位的柔光：同一束光扫过顶栏，坐标写进 --tx/--ty。 */
    var top = document.querySelector('.top');
    if (top) {
      var tr = top.getBoundingClientRect();
      top.style.setProperty('--tx', (e.clientX - tr.left) + 'px');
      top.style.setProperty('--ty', (e.clientY - tr.top) + 'px');
    }
  }, {passive: true});
var _tb = document.querySelector('[data-toggle-theme]');
if (_tb) _tb.addEventListener('click', function () {
  var _r = document.documentElement;
  var _t = _r.dataset.theme === 'light' ? 'dark' : 'light';
  var _apply = function () {
    _r.dataset.theme = _t;
    try { localStorage.setItem('fr-theme', _t); } catch (e) {}
  };
  /* 支持的话，新主题从按钮位置圆形扩散涂开；否则直接换。 */
  if (document.startViewTransition &&
      !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    var _b = _tb.getBoundingClientRect();
    _r.style.setProperty('--vx', (_b.left + _b.width / 2) + 'px');
    _r.style.setProperty('--vy', (_b.top + _b.height / 2) + 'px');
    document.startViewTransition(_apply);
  } else _apply();
});
</script>"""
    )


def _search_form(q: str = "") -> str:
    """首页和结果页共用。GET，不花钱，所以不走 CSRF。"""
    return (
        '<form class="seek" action="/search" method="get">'
        f'<input type="search" name="q" value="{escape(q)}" '
        'placeholder="Keywords, control number, or a question" aria-label="Search controls">'
        '<button type="submit">Search</button></form>'
        '<p class="note">Literal search over titles, control numbers and interpretations first; if nothing matches, AI looks for close wording.</p>'
    )


def search_results(
    q: str, hits: list[dict], *, via: str = "literal",
    expanded: list[str] | None = None, note: str = "",
) -> str:
    """`via` 是「字面」或「ai」。扩了哪些词必须写在页面上——否则相近语义是黑盒。"""
    body = [_search_form(q), f'<h2>Search "{escape(q)}"</h2>']
    if via == "ai" and expanded:
        shown = ", ".join(escape(t) for t in expanded)
        body.append(f'<p class="note">No literal hits. AI expanded: {shown}</p>')
    elif via == "literal" and hits:
        body.append(f'<p class="note">{len(hits)} literal hits.</p>')
    if note:
        body.append(f'<p class="note">{escape(note)}</p>')
    if hits:
        rows = "".join(
            f'<tr><td class="c"><a href="/c/{escape(h["id"])}">'
            f'{escape(h["short"])}</a></td>'
            f'<td>{escape(h["framework_id"])}</td>'
            f'<td><a href="/c/{escape(h["id"])}">{escape(h["label"])}</a></td></tr>'
            for h in hits
        )
        body.append(
            "<table><tr><td><strong>Control</strong></td>"
            "<td><strong>Framework</strong></td><td><strong>Title</strong></td></tr>"
            f"{rows}</table>"
        )
    elif not note:
        body.append('<p class="empty">Nothing found.</p>')
    return page(f"Search {q}", "".join(body), crumb="Search")


def _subnav(framework_id: str, here: str) -> str:
    items = [("", "Controls"), ("/supersession", "Supersession"),
             ("/assess", "Self-assessment"), ("/gap", "Gap report"),
             ("/remediation", "Remediation"),
             ("/soa", "Statement of Applicability")]
    return '<div class="subnav">' + "".join(
        f'<a href="/f/{framework_id}{path}"'
        f'{" style=\"border-color:var(--accent);color:var(--accent)\"" if path == here else ""}>'
        f"{label}</a>"
        for path, label in items
    ) + "</div>"


def supersession_page(view, edges: list) -> str:
    """换版对照：这个框架里谁能继承谁，一眼看完。

    `edges` 是 QueryAPI.supersessions_in() 的原样输出。动作列只对
    「旧有解读、新没有」的行出继承表单——后端校验是底线，前端不渲染是体面。
    """
    fid = escape(view.id)
    head = "<h2>Supersession</h2>" + _subnav(fid, "/supersession")
    note = (
        '<p class="note">Inheriting copies the old control\'s interpretation onto the new control; '
        "the sign-off does not carry over, the new control must be confirmed again.</p>"
    )
    if not edges:
        return page(
            "Supersession",
            head + note + '<p class="empty">This framework has no supersession relationships.</p>',
            crumb=f"{fid} Supersession", crumb_href=f"/f/{fid}",
        )
    rows = []
    for e in edges:
        old_short = escape(e.old_id.split(":", 1)[-1])
        new_short = escape(e.new_id.split(":", 1)[-1])
        if e.old_state and not e.new_state:
            action = (
                f'<form action="/c/{escape(e.old_id)}/inherit" method="post">'
                f'<input type="hidden" name="target" value="{escape(e.new_id)}">'
                '<button type="submit">Inherit</button></form>'
            )
        elif e.new_state:
            action = '<span style="color:var(--muted)">New control already has an interpretation</span>'
        else:
            action = '<span style="color:var(--muted)">Old control has no interpretation</span>'
        rows.append(
            "<tr>"
            f'<td><a href="/c/{escape(e.old_id)}">{old_short}</a> {escape(e.old_label)}</td>'
            f"<td>{_relation_label(e.relation)}</td>"
            f'<td><a href="/c/{escape(e.new_id)}">{new_short}</a> {escape(e.new_label)}</td>'
            f"<td>{action}</td></tr>"
        )
    body = (
        head + note
        + "<table><tr><th>Old control</th><th>Relation</th><th>New control</th><th></th></tr>"
        + "".join(rows) + "</table>"
    )
    return page(
        "Supersession", body,
        crumb=f"{fid} Supersession", crumb_href=f"/f/{fid}",
    )


def _relation_label(relation: str) -> str:
    return {"incorporated_into": "incorporated into",
            "moved_to": "renumbered"}.get(relation, relation)


def frameworks(items: list[dict], error: str = "", nav: str = "") -> str:
    """框架都在这一页：内置一段、导入的一段。

    **两段用不同的形状是有意的。** 内置就那么几个，卡片好看也点得准；
    导入的会越来越多，卡片十几个就没法看，表格一百行还能翻。

    搜索框放在首页（/）——框架页只管「挑一个进去干活」。
    """
    builtin = [f for f in items if not f.get("mine")]
    mine = [f for f in items if f.get("mine")]

    cards = []
    for item in builtin:
        pct = int(100 * item["with_interp"] / item["controls"]) if item["controls"] else 0
        cards.append(
            f'<a class="card reveal" href="/f/{escape(item["id"])}">'
            f'<span class="id">{escape(item["id"])}</span>'
            '<span class="tag">Built-in</span>'
            f'<h3>{escape(item["name"])}</h3>'
            f'<p class="meta">{item["controls"]} controls · has interpretation '
            f'{item["with_interp"]}/{item["controls"]}</p>'
            f'<span class="bar"><i style="width:{pct}%"></i></span></a>'
        )

    body = ["<h2>Built-in frameworks</h2>"]
    body.append(f'<div class="cards">{"".join(cards)}</div>' if cards
                else '<p class="empty">No frameworks in the content package.</p>')

    if mine:
        rows = "".join(
            f'<tr><td class="c"><a href="/f/{escape(f["id"])}">'
            f'{escape(f["id"])}</a></td>'
            f'<td><a href="/f/{escape(f["id"])}">{escape(f["name"])}</a></td>'
            f'<td style="white-space:nowrap">{f["controls"]} controls</td>'
            f'<td style="white-space:nowrap">{f["with_interp"]}/{f["controls"]}</td>'
            f'<td style="white-space:nowrap">{escape(f.get("imported_at", ""))}</td>'
            f'<td>{escape(f.get("source_file", "") or "-")}</td>'
            + (f'<td><a class="linky" href="/f/{escape(f["id"])}/delete">Delete</a></td>'
               if may("framework:delete") else "<td></td>")
            + "</tr>"
            for f in mine
        )
        body += [
            f"<h2>My imports ({len(mine)})</h2>",
            '<p class="note">"Source" is the file that was originally uploaded; when the wrong thing got imported you need to know which run it was.</p>',
            '<table><tr><td><strong>ID</strong></td><td><strong>Name</strong></td>'
            "<td><strong>Controls</strong></td><td><strong>Has interpretation</strong></td>"
            "<td><strong>Imported</strong></td><td><strong>Source</strong></td>"
            f"<td></td></tr>{rows}</table>",
        ]

    return page("Framework Workbench", "".join(body), nav=nav)


def import_page(error: str = "", nav: str = "") -> str:
    """导入自己有一页。顶栏那个链接要指得到一个地方，锚点不够——

    出错时得有一页能把错误报在原地，而不是把人踢回主页最底下重新找表单。
    """
    return page("Import your own framework", (
        "<h2>Import your own framework</h2>"
        '<p class="note">Import your organization\'s policies and procedures, then browse them like the built-in frameworks, run self-assessments and produce gap reports. '
        "Data is written only to the user database on this machine.</p>"
        + _import_form(error)
    ), crumb="Import", nav=nav)


def _import_form(error: str = "") -> str:
    return (
        (f'<p class="err">{escape(error)}</p>' if error else "")
        + '<form action="/import" method="post" enctype="multipart/form-data">'
        '<div class="row">'
        '<div><label for="fid">ID (unique, e.g. ACME-SEC-2026)</label>'
        '<input type="text" id="fid" name="framework_id" required></div>'
        '<div><label for="fname">Display name</label>'
        '<input type="text" id="fname" name="name" required></div>'
        "</div>"
        '<label for="file">File (.csv / .xlsx / .docx / .pdf)</label>'
        # **不写 accept。** 它的唯一好处是方便，代价是「文件在那儿但点不中，
        # 而且没有任何解释」——实测就是这么表现的：对话框弹出来，
        # 用户要传的那份是灰的，看起来就是「点了没反应」。
        # .doc 改名成 .docx、系统 UTI 认不出来、从别处拷来的文件丢了扩展名，
        # 每一种都会掉进这个坑，而它们在服务端本来就有确切的报错。
        # 宁可让人选中一个我们不收的文件，然后告诉他为什么不收。
        '<input type="file" id="file" name="file" required>'
        # 两条路的结果不一样，必须说：表格直接进库，文档要先确认。
        '<p class="hint"><strong>Spreadsheets</strong> (.csv / .xlsx) need "ID" and "Title" columns, "Parent" optional, '
        '<strong>"Body" optional but strongly recommended</strong>: with a title alone the draft is a guess; with body text it is an interpretation grounded in your actual requirements. '
        'Spreadsheets go straight into the database.<br>'
        '<strong>Documents</strong> (.docx / .pdf) are first split control by control by the model, '
        '<strong>then you confirm, and nothing is written before you confirm</strong>: the control text is cut verbatim from your original; the model only draws the boundaries. '
        'This step calls the model and spends the organization\'s money.<br>'
        'Scanned files (image-only PDFs) are not accepted yet: there is no text in them, only images.</p>'
        '<p style="margin:1rem 0 0"><button type="submit">Import</button></p>'
        "</form>"
    )


def home(popular: list[dict], daily: list[dict],
         review: dict | None = None, roll: int = 0, nav: str = "") -> str:
    """搜索工作台。三样东西，别的不要：

    - 搜索框（GET /search）
    - 「经常搜索」——从搜索统计里取的热门条款，点进去学
    - 「今天学三条」——同一天是同一组，让「打开就有事做」；
      「换一批」把 `roll+1` 当下一批的种子——GET 导航，不花钱不发请求，
      同一个批次当天稳定，书签收住的就是这一批

    `review` 是待确认的 AI 初稿数（一条不剩时是 None，不渲染——安静的页面
    比一枚恒为零的徽章有用）。审阅是签字人的日常入口，放在他每天打开的
    第一页。

    **不放**内置框架卡片、不放导入表单、不放「我导入的」表格——
    那些是 /frameworks 的事。首页是「你来这里干什么」的入口，
    不是「你已经有了什么」的库存。

    视觉上和框架页一致：复用 `.seek`（搜索表单）和 `.cards` / `.card`
    （卡片网格），不引入新 CSS。苹果黑白风要整页统一，不要首页自己
    另起一套。
    """
    seek = (
        '<form class="seek" action="/search" method="get">'
        '<input type="search" name="q" placeholder="Keywords, control number, or a question"'
        ' autofocus aria-label="Search controls">'
        '<button type="submit">Search</button></form>'
        '<p class="note">Literal search over titles, control numbers and interpretations first; if nothing matches, AI looks for close wording.</p>'
    )
    review_block = ""
    if review and review.get("count"):
        review_block = (
            '<div class="cards"><a class="card" href="/review">'
            f'<span class="id">{review["count"]} drafts</span>'
            "<h3>AI drafts awaiting confirmation</h3>"
            '<p class="meta">Open the review queue and sign them one by one</p></a></div>'
        )
    if popular:
        cards = "".join(
            f'<a class="card reveal" href="/c/{escape(p["id"])}">'
            f'<span class="id">{escape(p["short"])}</span>'
            f'<h3>{escape(p["label"])}</h3>'
            '<p class="meta">Frequently searched</p></a>'
            for p in popular
        )
        popular_block = '<h2>Frequently searched</h2><div class="cards">' + cards + '</div>'
    else:
        popular_block = '<h2>Frequently searched</h2><p class="empty">No search history yet.</p>'
    if daily:
        cards = "".join(
            f'<a class="card reveal" href="/c/{escape(d["id"])}">'
            f'<span class="id">{escape(d["short"])}</span>'
            f'<h3>{escape(d["label"])}</h3>'
            f'<p class="meta">{escape(d["snippet"])}</p>'
            f'<p class="meta" style="margin-top:.35rem">{escape(d["framework"])}</p>'
            '</a>'
            for d in daily
        )
        refresh = (
            '<form class="tiny" action="/" method="get">'
            f'<input type="hidden" name="roll" value="{roll + 1}">'
            '<button type="submit">Shuffle</button></form>'
        )
        daily_block = (
            # 标题的外边距挪到容器上：h2 在 flex 里 margin 归零后，
            # 「标题 → 卡片」那 1rem 的呼吸间距就没了，会贴死。
            '<div style="display:flex;align-items:baseline;gap:.8rem;'
            'margin:2.6rem 0 1rem">'
            '<h2 style="margin:0">Learn three today</h2>' + refresh + '</div>'
            + '<div class="cards">' + cards + '</div>'
        )
    else:
        daily_block = '<h2>Learn three today</h2><p class="empty">Nothing for today.</p>'
    return page("Framework Workbench", seek + review_block + popular_block + daily_block,
                nav=nav)


def framework(
    view, controls: list[dict], pending: int | None = None, nav: str = ""
) -> str:
    """`pending` 非 None 时给起草入口，导入的、内置的都一样。

    网页起草一律 overlay 进用户库当工作副本，不进 git；要写内容包
    （发布用）仍走 `fr draft`。顶栏那份跟着 sticky 顶栏走——800-53
    一千多条，不该拉到底才找得到按钮。
    """
    rows = "".join(
        f'<tr><td class="c"><a href="/c/{escape(c["id"])}">{escape(c["short"])}</a></td>'
        f'<td><a href="/c/{escape(c["id"])}">{escape(c["label"])}</a></td>'
        f'<td style="white-space:nowrap;width:1%">{_state_cell(c)}</td></tr>'
        for c in controls
    )
    head = (
        # 框架页是层级里唯一没有「返回」的一层：条款页的面包屑能回框架，
        # 这里也得能回目录——跟条款页的 back 同一个样式。
        '<p class="back"><a class="back" href="/frameworks">'
        "← Back to Frameworks</a></p>"
        f"<h2>{escape(view.name)}</h2>"
        + _subnav(escape(view.id), ""))
    invite = (
        _draft_invite(escape(view.id), pending)
        if pending is not None and may("interpretation:draft") else "")
    topbar = (
        f'<form class="tiny" action="/f/{escape(view.id)}/draft" method="post">'
        f'<button type="submit">Draft these {pending} controls</button></form>'
        if invite and pending else "")
    body = (
        head + invite + f'<p class="note">{len(controls)} controls</p><table>{rows}</table>'
        if controls
        else head + invite + '<p class="empty">No controls under this framework.</p>'
    )
    return page(view.name, body, crumb=escape(view.id), nav=nav, topbar=topbar)


def _state_cell(c: dict) -> str:
    """哪几条已经有人认领，是这个框架能不能交出去的唯一指标。"""
    if c.get("confirmed"):
        return '<span class="mark mine">Confirmed</span>'
    if c["has_interp"]:
        return '<span style="color:var(--muted)">has interpretation</span>'
    return '<span style="color:var(--muted)">-</span>'


def _draft_invite(framework_id: str, pending: int) -> str:
    """点一下就是一次真花钱。花在几条上、用谁的 key，点之前要看得见。"""
    if pending == 0:
        return (
            '<h2>Draft interpretations</h2>'
            '<p class="note">Every control in this framework already has an interpretation. '
            f'To redo the whole thing, run <code>fr draft --framework-id {framework_id} '
            "--full --force</code> on the command line.</p>"
        )
    return (
        "<h2>Draft interpretations</h2>"
        f'<form action="/f/{framework_id}/draft" method="post">'
        f'<p style="margin:0 0 .8rem"><strong>{pending} controls</strong> still have no interpretation. '
        "Drafted from the body text you uploaded: what this control defends against, in plain words, what to do at each level, what serves as evidence.</p>"
        '<p class="hint">Uses the model and API key you configured, one call per control, billed to your quota. '
        'Everything drafted is marked "AI draft"; do not treat it as final until you have confirmed it.</p>'
        '<p style="margin:1rem 0 0"><button type="submit">Draft these '
        f"{pending} controls</button></p>"
        "</form>"
    )


def control(
    view, fields: dict, state: str | None, mappings: list[dict], body: str = "",
    nav: str = "", editable: bool = False, signer: str = "", signed_at: str = "",
    framework_name: str = "", chat: list | None = None,
    inherited_from: str = "", superseded: list[dict] | None = None,
    body_label: str = "Your imported text",
) -> str:
    """`fields` 是 QueryAPI.interpretation() 的原样输出：{字段: {value, basis}}。

    `editable` 只对用户自己导入的框架为真。内置框架的解读是我们要发布的内容，
    由 fr draft 起草、人工评审、进 git，不在用户的按钮上改。
    """
    from framework_reader.interpret.render import FIELD_LABELS

    cid = escape(view.id)
    # 进得去也得出得来。改字段页和重写页都有「不改了」回条款页，
    # 唯独条款页自己是死路——换框架要回首页，回本框架得有自己的出口。
    #
    # 标**框架名**不标编号：编号已经在顶栏面包屑里，再印一遍没有信息量。
    parts = [
        f'<p class="back"><a class="back" href="/f/{escape(view.framework_id)}">'
        f'← Back to {escape(framework_name or view.framework_id)}</a></p>',
        f'<h2>{escape(view.label)}</h2>',
    ]

    if state == "confirmed" and signer:
        parts.append(
            f'<p class="signed">Confirmed · signed by {escape(signer)}'
            + (f' · {escape(signed_at)}' if signed_at else "")
            + "</p>"
        )
    elif state and any(
        (fields.get(n) or {}).get("basis") == "practitioner"
        and (fields.get(n) or {}).get("value") not in (None, "", [], {})
        for n, _ in FIELD_LABELS
    ):
        # 七个字段里已经有人写的了，再挂「AI 初稿」就是往反方向撒谎。
        parts.append(
            '<p class="draft">[Unconfirmed · each field below is marked with who wrote it]</p>'
        )
    elif state:
        parts.append(f'<p class="draft">[AI draft, not yet confirmed · state={escape(state)}]</p>')

    if inherited_from:
        parts.append(
            f'<p class="draft">[Inherited from {escape(inherited_from)}, '
            "not yet re-confirmed for the new control]</p>"
        )
    if superseded:
        gone = "; ".join(
            f'<a href="/c/{escape(s["control_id"])}">'
            f'{escape(s["control_id"].split(":", 1)[-1])}</a> {escape(s["label"])}'
            f" ({_relation_label(s['relation'])})"
            for s in superseded
        )
        parts.append(
            '<p class="note">This control has been superseded. Where it went: ' + gone + "."
            f'See the <a href="/f/{escape(view.framework_id)}/supersession">'
            "Supersession</a> page for the full picture.</p>"
        )

    # 正文块对所有条款都渲染：内置条款 body 是空的，而「贴一段进去」
    # 正是给它补起草依据的入口（覆盖层，官方基准不动）。
    if body:
        # 「改」登录就能点——内置条款贴的正文存覆盖层，同解读字段一套哲学。
        edit = (f' <a class="edit" href="/c/{cid}/edit-body">Edit</a>'
                if editable else "")
        parts.append(
            f'<div class="doc"><h4>{escape(body_label)}' + edit + '</h4>'
            f'<p class="own">{escape(body)}</p></div>'
        )
    elif editable:
        # 「没有正文」说的是条款原文：ISO 的 label 是自写中文短标题
        # （原文受版权不进库），800-53 的 label 是控制标题——页面上
        # 明明有标题有解读，不说清「内置的只是标题」，这句话就像在胡说。
        parts.append(
            '<p class="empty">This control has no body text yet: built-in entries are titles only. '
            "From the standard or policy document you have on hand, "
            f'<a href="/c/{cid}/edit-body">paste in a passage</a>, '
            "so AI drafting and chat have something to stand on.</p>"
        )

    written = any(
        (fields.get(n) or {}).get("value") not in (None, "", [], {})
        for n, _ in FIELD_LABELS
    )
    if editable and not written:
        # 一个字都还没有的时候，两条路都要说出来：让模型起草，或者自己写。
        parts.append(
            '<p class="empty" style="padding:1rem 0">This control has no interpretation yet. Go to the '
            f'<a href="/f/{escape(view.framework_id)}">framework page</a> and click "Draft interpretations" '
            "to have the model write one from your body text, or write it yourself below.</p>"
        )
    if written or editable:
        parts.append(f'<div class="doc">{_fields(cid, fields, editable)}</div>')
    elif body:
        parts.append(
            '<p class="empty">This control has no interpretation yet. Go to the '
            f'<a href="/f/{escape(view.framework_id)}">framework page</a> and click "Draft interpretations", '
            "drafting from the body text above.</p>"
        )
    else:
        parts.append(
            '<p class="empty">This control has no interpretation yet. '
            'Run <code>fr draft</code> to draft the whole framework.</p>'
        )

    # 「做的事」收成一摞，等下整个放进右栏。**左边只留读的东西**——
    # 条款很长时，动作按钮跟着滚到看不见的地方，等于每次都要翻回去找。
    doing = []
    if editable and _has_blank_field(fields) and may("interpretation:draft"):
        doing.append(_fill_blanks_invite(cid, written))
    if (editable and written and state != "confirmed"
            and may("interpretation:confirm")):
        doing.append(
            f'<form action="/c/{cid}/confirm" method="post" class="claim">'
            '<p style="margin:0 0 .8rem">Do you stand behind the words above? Confirming records that you signed it, and when. '
            "Any later change voids this sign-off.</p>"
            '<p style="margin:0"><button type="submit">I confirm this control</button></p>'
            "</form>"
        )

    if mappings:
        items = "".join(
            f'<li><code>{escape(m["short"])}</code> {escape(m["label"])}</li>'
            for m in mappings
        )
        parts.append(
            f'<div class="doc noai"><h4>Mappings to other frameworks (official)</h4>'
            f"<ul>{items}</ul></div>"
        )
    if editable:
        # **只在自己导入的框架上开。** 内置框架的正文是 Tier C/D 受版权原文，
        # 一个字不许出网——`editable` 就是「这是你自己的东西」那个判据。
        doing.append(_clause_chat(cid, chat or []))

    if doing:
        # 两栏：左边读的东西，右边做的事。右栏 sticky 常驻——
        # 条款几十屏长时，滚到哪儿都能直接动手。窄屏叠回下面（见 CSS）。
        parts = [
            f"<style>{_SPLIT_CSS}</style>",
            '<div class="split"><div class="reading">',
            *parts,
            '</div><aside class="doing"><div class="stuck">',
            *doing,
            "</div></aside></div>",
        ]
    if editable:
        parts.append(_selection_popup(cid))
    return page(view.id, "".join(parts), crumb=escape(view.framework_id),
                crumb_href=f"/f/{escape(view.framework_id)}", nav=nav,
                wide=bool(doing))


def _clause_chat(control_id: str, turns: list) -> str:
    """条款页上的对话框。

    **模型的建议要人点头才写库。** 提议后面挂一个「确定，改」，
    点了才写——模型说的话永远不会自己进库。

    对话跟着条款走，同一个组织里别人看得到：签字的人要能看到
    「这句话当初是怎么来的」，那比任何审计记录都管用。
    """
    from framework_reader.interpret.render import FIELD_LABELS

    labels = dict(FIELD_LABELS)
    lines = []
    for turn in turns:
        who = escape(turn.actor or "you") if turn.role == "user" else "AI"
        klass = "said mine" if turn.role == "user" else "said"
        lines.append(
            f'<div class="{klass}"><span class="who">{who}</span>'
            f'<p>{escape(turn.text)}</p>'
        )
        if turn.proposal:
            changed = ", ".join(
                labels.get(u["field"], u["field"]) for u in turn.proposal)
            if turn.applied:
                lines.append(
                    f'<p class="hint">Applied this change: {escape(changed)} '
                    '(marked "AI draft", check it yourself before sign-off)</p>')
            else:
                lines.append(
                    f'<form method="post" action="/c/{control_id}/chat/'
                    f'{escape(turn.turn_id)}/apply">'
                    f'<p class="hint">It wants to change: <strong>{escape(changed)}</strong> '
                    "<button type=\"submit\" class=\"ghost\">Apply</button>"
                    "</p></form>")
        lines.append("</div>")

    return (
        f"<style>{_CHAT_CSS}</style>"
        '<div class="doc chat"><h4>Ask AI</h4><div class="thread">'
        + ("".join(lines) if lines else
           '<p class="empty">Nothing asked yet. Ask it to rewrite a field ("make "How to implement" more specific, we use Okta"), or just ask a question ("what evidence does this control usually need").</p>')
        + "</div>"
        + f'<form method="post" action="/c/{control_id}/chat">'
        '<textarea name="message" rows="2" placeholder="What do you want to ask, or have it change?"></textarea>'
        '<p class="hint">Its edits are <strong>suggestions only</strong>: nothing is written until you click. '
        "Each question is one model call and spends the organization's money.</p>"
        '<p style="margin:.8rem 0 0"><button type="submit">Send</button></p>'
        "</form></div>"
    )


_CHAT_CSS = """
.said{border-left:2px solid var(--rule);padding:.1rem 0 .1rem .9rem;
  margin:0 0 .9rem}
.said.mine{border-left-color:var(--accent)}
.said .who{font-family:var(--mono);font-size:.75rem;color:var(--muted)}
.said p{margin:.2rem 0;white-space:pre-wrap}
.said form{background:none;border:0;padding:0}
"""


_BASIS_MARK = {
    "practitioner": ('<span class="mark mine">You wrote this</span>', ""),
    "inferred": ('<span class="mark">AI draft</span>', ""),
    "quote": ('<span class="mark">Quoted from source</span>', ""),
}


def _has_blank_field(fields: dict) -> bool:
    from framework_reader.interpret.render import FIELD_LABELS

    return any(
        (fields.get(n) or {}).get("value") in (None, "", [], {}) for n, _ in FIELD_LABELS
    )


def _fill_blanks_invite(control_id: str, written: bool) -> str:
    """只补这一条的空字段。整框架起草要为几十条付钱，只想试一条的人得有入口。"""
    what = ("fill in the empty fields above" if written
            else "draft all seven fields for this control")
    return (
        f'<form action="/c/{control_id}/draft" method="post" class="claim">'
        f'<p style="margin:0 0 .8rem">Have AI {what}. '
        "<strong>Fields you already wrote will not be touched</strong>, including AI drafts you reviewed and accepted.</p>"
        '<p class="hint">Drafts from the body text you imported, uses the model and key you configured, and bills only this control.</p>'
        '<p style="margin:1rem 0 0"><button type="submit">'
        f'{"Fill the blanks" if written else "Draft this control"}</button></p>'
        "</form>"
    )


def rewrite_field_page(view, field: str, label: str, current, error: str = "",
                       nav: str = "") -> str:
    """提要求让 AI 重写一个字段。"""
    cid = escape(view.id)
    return page(f"Rewrite {label}", (
        f'<h2>Have AI rewrite "{escape(label)}"</h2>'
        f'<p class="note">{escape(view.label)}</p>'
        + (f'<p class="err">{escape(error)}</p>' if error else "")
        + '<div class="doc"><h4>Current text</h4>' + _value_html(current) + "</div>"
        + f'<form action="/c/{cid}/rewrite/{escape(field)}" method="post">'
        '<label for="instruction">Your instruction</label>'
        '<textarea id="instruction" name="instruction" rows="4" '
        'placeholder="e.g. be more specific and name the systems we actually use; or: too long, cut it to two sentences"'
        "></textarea>"
        '<p class="hint">The rewritten text is still marked "AI draft": <strong>the instruction came from you, the words come from the model</strong>. For it to count as yours, use "Edit" and write it yourself.</p>'
        '<p style="margin:1rem 0 0"><button type="submit">Have AI rewrite</button> '
        f'<a href="/c/{cid}" style="margin-left:.8rem">Discard</a></p>'
        "</form>"
    ), crumb=escape(view.framework_id),
       crumb_href=f"/f/{escape(view.framework_id)}", nav=nav)


def _fields(control_id: str, fields: dict, editable: bool) -> str:
    """逐字段渲染，逐字段标出处。

    整条一句「AI 初稿」看不出用户改过哪几句——而「哪几句是你自己的话」正是
    这份材料敢不敢交出去的分界线。
    """
    from framework_reader.interpret.render import FIELD_LABELS

    out = []
    for name, label in FIELD_LABELS:
        data = fields.get(name) or {}
        value = data.get("value")
        empty = value in (None, "", [], {})
        if empty and not editable:
            continue          # 留空的字段不出现，不显示 None/null
        mark = _BASIS_MARK.get(data.get("basis", ""), ("", ""))[0] if not empty else ""
        edit = (
            f'<a class="edit" href="/c/{control_id}/edit/{name}">'
            f'{"Edit" if not empty else "Write"}</a>'
            if editable and may("interpretation:write") else ""
        )
        # 空字段没有可改写的东西——那是「写」，不是「重写」。
        rewrite = (
            f'<a class="edit" href="/c/{control_id}/rewrite/{name}">Have AI rewrite</a>'
            if editable and not empty and may("interpretation:draft") else ""
        )
        out.append(f"<h4>{escape(label)}{mark}{edit}{rewrite}</h4>")
        out.append(_value_html(value))
    return "".join(out)


def _value_html(value) -> str:
    if value in (None, "", [], {}):
        return '<p class="empty" style="padding:0">(empty)</p>'
    if isinstance(value, dict):
        items = "".join(
            f"<li>Level {escape(str(k))}: {escape(str(v))}</li>"
            for k, v in sorted(value.items())
        )
        return f"<ol>{items}</ol>"
    if isinstance(value, list):
        return "<ul>" + "".join(f"<li>{escape(str(v))}</li>" for v in value) + "</ul>"
    return f"<p>{escape(str(value))}</p>"


# 三档是字典，追问是清单，其余是一段话。表单得照着形状来，
# 否则用户改完一次，practice 就从三档塌成一句话。
RUNGS = "practice"
LINES = ("auditor_asks",)


def edit_field(view, field: str, label: str, value, nav: str = "") -> str:
    cid = escape(view.id)
    head = (
        f'<h2>Edit "{escape(label)}"</h2>'
        f'<p class="note">{escape(view.label)}</p>'
    )
    if field == RUNGS:
        current = value if isinstance(value, dict) else {}
        boxes = "".join(
            f'<label for="v{n}">Level {n}</label>'
            f'<textarea id="v{n}" name="v{n}" rows="3">'
            f'{escape(str(current.get(str(n), "")))}</textarea>'
            for n in (1, 2, 3)
        )
        hint = ('The three levels are the lookup for "what to do next": at Level 1, your next step is the exact words of Level 2.')
    else:
        if isinstance(value, list):
            text = "\n".join(str(v) for v in value)
        elif value in (None, "", [], {}):
            text = ""
        else:
            text = str(value)
        boxes = (
            f'<label for="value">Body text</label>'
            f'<textarea id="value" name="value" rows="8">{escape(text)}</textarea>'
        )
        hint = ("One item per line." if field in LINES else
                "Leave empty to clear this field.")
    return page(f"Edit {label}", (
        head
        + f'<form action="/c/{cid}/edit/{escape(field)}" method="post">'
        + boxes
        + f'<p class="hint">{hint}Once saved, this field counts as written by you and is no longer marked "AI draft".</p>'
        + '<p style="margin:1rem 0 0"><button type="submit">Save</button> '
        + f'<a href="/c/{cid}" style="margin-left:.8rem">Discard</a></p>'
        + "</form>"
    ), crumb=escape(view.framework_id),
       crumb_href=f"/f/{escape(view.framework_id)}", nav=nav)


def edit_body(view, body: str, *, note: str = "") -> str:
    """改用户自己条款的正文。一个表单两个按钮：保存直接写库；
    「让 AI 改一版」只把提议稿回显在这个框里，写库仍靠「保存」——
    和字段重写同一道闸。AI 改写以框里的当前内容为基础。"""
    cid = escape(view.id)
    back = f"/c/{cid}"
    head = (
        '<h2>Edit body text</h2>'
        f'<p class="note">{escape(view.label)} · {escape(view.framework_id)}. '
        "For built-in controls the pasted text is stored in your own database and the official baseline is untouched; saving an empty box restores the default. "
        "After an edit, the existing interpretation still reflects the old text; "
        "re-draft to pick up the new body.</p>"
    )
    if note:
        head += f'<p class="note">{escape(note)}</p>'
    return page(
        f"Edit body text {cid}",
        f'<p><a class="back" href="{back}">← Back to control</a></p>'
        + head
        + f'<form action="{back}/edit-body" method="post">'
        + f'<textarea name="body" rows="12">{escape(body)}</textarea>'
        + f'<label for="instruction">Have AI help (optional): how should it change? '
        'e.g. "merge the two paragraphs, use policy wording" or "fix typos only"</label>'
        + '<input type="text" name="instruction" id="instruction" '
        'placeholder="Describe the change in one line; leave empty to skip AI">'
        + '<p class="hint">AI produces a proposal only, shown in the box above; nothing is written until you click "Save". '
        "Each rewrite is one model call.</p>"
        + '<p style="margin:1rem 0 0"><button type="submit">Save</button> '
        + f'<button type="submit" formaction="{back}/edit-body/ai" '
        'style="margin-left:.8rem">Have AI revise</button> '
        + f'<a href="{back}" style="margin-left:.8rem">Discard</a></p>'
        + "</form>",
        crumb=escape(view.framework_id),
        crumb_href=f"/f/{escape(view.framework_id)}")


def draft_status(title: str, back_url: str, job, nav: str = "", crumb: str = "") -> str:
    """起草进度。跑着的时候自己刷新——否则用户只能盯着一个不动的页面猜。"""
    head = f"<h2>{escape(title)} · Draft interpretations</h2>"
    back = f'<p style="margin:1.4rem 0 0"><a href="{escape(back_url)}">Back</a></p>'

    if job.running:
        return page(title, (
            '<meta http-equiv="refresh" content="3">' + head
            + f'<p class="note">Drafting {job.total} controls, one model call each: keep this page open. '
            "It refreshes itself every 3 seconds.</p>"
            + '<p class="empty">Drafting...</p>' + back
        ), crumb=escape(crumb), nav=nav)

    if job.status == "error":
        return page(title, (
            head + '<p class="err">The draft could not start: ' + escape(job.error) + "</p>"
            + '<p class="note">The most common cause is a missing model key: drafting uses the provider you configured. '
            'See which environment variable the <code>drafter</code> line in <code>content/llm_providers.yaml</code> names, set it, then restart <code>fr serve</code> and try again.</p>' + back
        ), crumb=escape(crumb), nav=nav)

    parts = [head, f'<p class="note">Drafted {job.written} controls. '
             'All are marked "AI draft"; do not treat them as final until you have confirmed them.</p>']
    if job.failed:
        items = "".join(
            f'<li><code>{escape(_short_id(cid))}</code> {escape(reason[:140])}</li>'
            for cid, reason in job.failed
        )
        parts.append(
            f'<div class="doc"><h4>{len(job.failed)} failed</h4><ul>{items}</ul></div>'
            '<p class="note">Clicking "Draft" again retries only these; what was written will not run again.</p>'
        )
    parts.append(back)
    return page(title, "".join(parts), crumb=escape(crumb), nav=nav)


def _short_id(control_id: str) -> str:
    return control_id.split(":", 1)[-1]

_AUTH_CSS = """
.auth{max-width:26rem;margin:3rem auto}
.auth h2{margin:0 0 1.2rem}
input[type=password]{width:100%;padding:.55rem .7rem;font:inherit;font-size:.9rem;
  background:var(--sunk);color:var(--ink);border:1px solid transparent;
  border-radius:10px;transition:border-color .2s ease}
input[type=password]:focus{outline:none;border-color:var(--accent)}
.sso{display:block;text-align:center;padding:.6rem 1.2rem;text-decoration:none;
  background:var(--accent);color:#fff;font-size:.95rem;font-weight:500;
  border-radius:980px;transition:opacity .2s ease}
.sso:hover{opacity:.85;text-decoration:none}
"""

_ASSESS_CSS = """
.arow{background:var(--surface);border:1px solid var(--rule);padding:1rem 1.1rem;
  margin:0 0 .8rem}
.arow.done{border-left:3px solid var(--accent)}
.arow h3{margin:0 0 .5rem;font-size:.98rem;color:var(--ink);font-weight:600}
.arow h3 code{font-family:var(--mono);font-size:.82rem;color:var(--accent);
  margin-right:.5rem}
.arow .rungs{list-style:none;margin:0 0 .8rem;padding:0;font-size:.86rem}
.arow .rungs li{padding-left:.8rem;border-left:2px solid var(--accent-soft);
  margin:0 0 .3rem;max-width:62ch;color:var(--muted)}
.arow .pick{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center}
.arow .pick label{display:inline-flex;gap:.3rem;align-items:center;margin:0;
  font-size:.85rem;color:var(--body);border:1px solid var(--rule);
  padding:.3rem .6rem;cursor:pointer;background:var(--ground)}
.arow .pick input{margin:0}
.arow .pick input:checked + span{color:var(--accent);font-weight:600}
.arow .pick .note{flex:1 1 16rem;min-width:0}
.arow .cur{font-size:.8rem;color:var(--muted);margin:.5rem 0 0}
.subnav{display:flex;gap:.6rem;flex-wrap:wrap;margin:0 0 1.5rem}
.subnav a{font-size:.85rem;padding:.35rem .8rem;border:1px solid var(--rule);
  text-decoration:none;color:var(--body);background:var(--surface)}
.subnav a:hover{border-color:var(--accent)}
.gap{white-space:pre-wrap;font-family:var(--han);font-size:.92rem;
  background:var(--surface);border:1px solid var(--rule);padding:1.2rem 1.3rem;
  line-height:1.75;overflow-x:auto}
.soawrap{overflow-x:auto}
.soa{font-size:.85rem}
.soa td{white-space:normal}
.pending{color:var(--ask)}
"""


MATURITY_CHOICES = (("0", "Not done"), ("1", "Level 1"), ("2", "Level 2"), ("3", "Level 3"), ("n", "Not applicable"))
SOA_CHOICES = (("0", "not started"), ("1", "in progress"), ("2", "implemented"),
               ("3", "implemented by a third party"), ("n", "Not applicable"))


def assess(view, rows: list[dict], maturity: bool, nav: str = "") -> str:
    choices = MATURITY_CHOICES if maturity else SOA_CHOICES
    question = ("What level are you at now?" if maturity
                else "Implementation status?")
    blocks = []
    for row in rows:
        rungs = ""
        if maturity and row["practice"]:
            rungs = '<ul class="rungs">' + "".join(
                f"<li>Level {escape(str(k))}: {escape(str(v))}</li>"
                for k, v in sorted(row["practice"].items())
            ) + "</ul>"
        picks = "".join(
            f'<label><input type="radio" name="answer" value="{value}"'
            f'{" checked" if row["answer"] == value else ""}>'
            f"<span>{escape(label)}</span></label>"
            for value, label in choices
        )
        current = (
            f'<p class="cur">Recorded: {escape(row["current"])}</p>' if row["current"] else ""
        )
        blocks.append(
            f'<div class="arow{" done" if row["answer"] else ""}" id="{escape(row["short"])}">'
            f'<h3><code>{escape(row["short"])}</code>{escape(row["label"])}</h3>'
            f"{rungs}"
            f'<form method="post" action="/f/{escape(view.id)}/assess">'
            f'<input type="hidden" name="control_id" value="{escape(row["id"])}">'
            f'<div class="pick"><span style="font-size:.85rem;color:var(--muted)">'
            f"{escape(question)}</span>{picks}"
            f'<input class="note" type="text" name="note" placeholder="Current state / where the evidence is"'
            f' value="{escape(row["note"])}">'
            f'<button type="submit">Record</button></div></form>{current}</div>'
        )
    done = sum(1 for r in rows if r["answer"])
    body = (
        f"<h2>{escape(view.name)} · Self-assessment</h2>"
        + _subnav(escape(view.id), "/assess")
        + f'<p class="note">Assessed {done}/{len(rows)} controls. '
        + ("Frameworks with three levels ask for a level; frameworks without interpretations ask applicability." if maturity
           else "This framework has no interpretations yet, so it asks the Statement of Applicability. After you run <code>fr draft</code> it switches to levels automatically.")
        + "</p>"
        + "".join(blocks)
    )
    return page(f"{view.name} · Self-assessment", body, crumb=escape(view.id), nav=nav)


def gap(view, text: str, nav: str = "", to_assess: int = 0,
        changes: list[dict] | None = None, plan: int | None = None) -> str:
    """`to_assess` 非 0 表示一条自评都还没有，这一页此刻没有内容可给。

    **空态不能照搬 CLI 那句话。** `render_gap` 的空态写的是「先跑 fr assess」——
    在终端里那是对的答案，渲到网页上就成了一句把人赶去开终端的指令，
    而正确答案是上面子导航里的「自评」。部署形态已经是一个组织多个用户
    （见 2026-08-23 网页服务化设计），那些人没有终端。

    `changes` 是复评对比（AssessStore.changes() 的输出），`plan` 是报告里
    还没立项的条数——有差距而没人跟进，报告就只是一张快照。
    """
    fid = escape(view.id)
    if to_assess:
        inner = (
            '<div class="empty-gap"><p><strong>No self-assessment yet.</strong>'
            " The gap report comes from the self-assessment: record where you stand today, control by control, and this page will tell you what is missing, what to fix first, and what counts as evidence.</p>"
            f'<p><a class="cta" href="/f/{fid}/assess">Assess these {to_assess} controls</a></p>'
            "</div>"
        )
    else:
        inner = f'<div class="gap">{escape(text)}</div>'
    body = (
        f"<h2>{escape(view.name)} · Gap report</h2>"
        + _subnav(fid, "/gap")
        + _review_changes(changes)
        + inner
        + _plan_block(fid, plan)
    )
    return page(f"{view.name} · Gap report", body, crumb=fid,
                crumb_href=f"/f/{fid}", nav=nav)


def _review_changes(changes: list[dict] | None) -> str:
    """复评对比。历史表是今天才开始记的，多数库第一次打开是空的——
    空着不渲染，别让一块「暂无数据」占着报告的头部。"""
    if not changes:
        return ""
    rows = "".join(
        f"<tr><td class=\"c\"><a href=\"/c/{escape(c['control_id'])}\">"
        f"{escape(c['control_id'].split(':', 1)[-1])}</a></td>"
        f"<td>{escape(c['label'])}</td>"
        f'<td><s>{escape(c["from"])}</s> → <strong>{escape(c["to"])}</strong></td>'
        f"<td>{escape(c["at"][:10])}</td></tr>"
        for c in changes
    )
    return (
        '<div class="empty-gap"><p><strong>Re-assessment comparison</strong>: '
        "controls whose level changed between the two \u300cRecord\u300d submissions:</p>"
        f'<table>{rows}</table>'
        "<p class=\"hint\">The full gap as of this moment follows below. Unverified changes do not count; "
        "every line here comes from two independent self-assessments.</p></div>"
    )


def _plan_block(fid: str, plan: int | None) -> str:
    """差距和整改之间就差一步：立项。report 里没有待提升条目时不渲染。"""
    if plan is None:
        return ""
    if plan == 0:
        return ('<p class="note">Every gap in the report is tracked in the ledger. '
                f'<a href="/f/{fid}/remediation">Open the remediation ledger</a>.</p>')
    return (
        f'<form method="post" action="/f/{fid}/remediation/plan" '
        'style="margin:1.2rem 0 0">'
        f"<button type=\"submit\">Track these {plan} gaps in the ledger</button>"
        f'<span class="hint" style="margin-left:.8rem">Owner and due date are filled in on the ledger: '
        "remediation nobody owns does not happen by itself.</span></form>"
    )


def remediation(view, rows: list[dict], nav: str = "") -> str:
    """整改台账。一行一张表单，负责人/期限/状态/备注当场改完当场存。

    排序是「有期限的在前、紧的先做」，由 store.all() 定——这里不再排一遍，
    两个排序总有一天会排得不一样。
    """
    fid = escape(view.id)
    blocks = "".join(_remediation_row(fid, r) for r in rows) if rows else (
        '<p class="empty">The ledger is empty. Click \u300cTrack these gaps\u300d in the gap report, '
        "or add entries below by control number.</p>")
    body = (
        f"<h2>{escape(view.name)} · Remediation</h2>"
        + _subnav(fid, "/remediation")
        + '<p class="note">Status is flipped by hand and does not follow the self-assessment. '
          "Until you re-assess, \u300cDone\u300d is only the owner\'s word.</p>"
        + '<form method="post" action="/f/' + fid + '/remediation" class="seek">'
        + '<input type="text" name="ref" placeholder="Add by control number, e.g. 4.1"'
        ' aria-label="Control number">'
        + '<button type="submit">Add</button></form>'
        + blocks
    )
    return page(f"{view.name} · Remediation", body, crumb=fid, crumb_href=f"/f/{fid}",
                nav=nav)


def _remediation_row(fid: str, r: dict) -> str:
    picks = "".join(
        f'<label><input type="radio" name="state" value="{state}"'
        f'{" checked" if r["state"] == state else ""}>'
        f"<span>{escape(label)}</span></label>"
        for state, label in STATE_LABELS.items()
    )
    current = (f"Now: {escape(r['current'])}." if r["current"] != "Not assessed yet" else "")
    next_step = (
        f'<p class="hint">Next step: {escape(r["next_step"])}</p>'
        if r["next_step"] else "")
    return (
        f'<div class="arow" id="{escape(r["short"])}">'
        f'<h3><code>{escape(r["short"])}</code>{escape(r["label"])}'
        f'<span class="mark mine">{STATE_LABELS[r["state"]]}</span></h3>'
        f"<p class=\"hint\">{current}{_due_hint(r['due'], r['updated_at'])}</p>"
        f"{next_step}"
        f'<form method="post" action="/f/{fid}/remediation">'
        f'<input type="hidden" name="control_id" value="{escape(r["id"])}">'
        f'<div class="pick"><span style="font-size:.85rem;color:var(--muted)">'
        f'Status</span>{picks}'
        f'<input class="note" type="text" name="owner" placeholder="Owner"'
        f' value="{escape(r["owner"])}">'
        f'<input class="note" type="date" name="due" value="{escape(r["due"])}"'
        f' aria-label="Due date">'
        f'<input class="note" type="text" name="note" placeholder="Progress / notes"'
        f' value="{escape(r["note"])}">'
        f'<button type="submit">Save</button></div></form>'
        f'<form class="tiny" method="post" action="/f/{fid}/remediation/remove" '
        f'style="margin:.4rem 0 0">'
        f'<input type="hidden" name="control_id" value="{escape(r["id"])}">'
        f'<button type="submit">Stop tracking</button></form></div>'
    )


def _due_hint(due: str, updated_at: str) -> str:
    parts = []
    if due:
        parts.append(f"Due {escape(due)}")
    if updated_at:
        parts.append(f"Updated {escape(updated_at)[:10]}")
    return "; ".join(parts) + "." if parts else ""


def review(item: dict | None, *, remaining: int, total: int, nav: str = "") -> str:
    """审阅队列：一次一条初稿。签字仍是逐条的（批量确认不能有），
    这里省掉的只是「找下一条」——左右键翻页，确认完原地进下一条。"""
    if item is None:
        body = (
            "<h2>Review queue</h2>"
            + '<div class="empty-gap"><p><strong>The queue is empty.</strong>'
              " Every interpretation has been signed off. New drafts will line up here after the next drafting run.</p>"
              '<p><a class="cta" href="/frameworks">Draft on the frameworks page</a></p></div>'
        )
        return page("Review queue", body, nav=nav)

    from framework_reader.interpret.render import FIELD_LABELS

    fields = []
    for name, label in FIELD_LABELS:
        value = (item["fields"].get(name) or {}).get("value")
        if value in (None, "", [], {}):
            continue
        if isinstance(value, dict):
            rendered = "".join(
                f"<li>Level {escape(str(k))}: {escape(str(v))}</li>"
                for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
            )
            fields.append(f"<p><strong>{escape(label)}</strong></p><ul>{rendered}</ul>")
        elif isinstance(value, list):
            rendered = "".join(f"<li>{escape(str(v))}</li>" for v in value)
            fields.append(f"<p><strong>{escape(label)}</strong></p><ul>{rendered}</ul>")
        else:
            fields.append(f"<p><strong>{escape(label)}</strong>"
                          f"{escape(str(value))}</p>")

    may_confirm = may("interpretation:confirm")
    confirm_btn = (
        '<button type="submit" name="next" value="1">Confirm and next</button>'
        if may_confirm else "")
    prev = f"/review?before={quote(item['id'])}"
    skip = f"/review?after={quote(item['id'])}"
    body = (
        "<h2>Review queue</h2>"
        + f'<p class="note"><strong>{remaining}</strong> / {total} left to confirm. '
          'Read before you sign: skip what you cannot follow and open the control page later.</p>'
        + '<div class="arow">'
        + f'<h3><code>{escape(item["short"])}</code>{escape(item["label"])}'
        + f'<span class="mark">{escape(item["framework"])}</span></h3>'
        + (f'<p class="draft">[AI draft, not yet confirmed]</p>'
           if item["state"] != "confirmed" else "")
        + "".join(fields)
        + (f'<h3>{escape(item["body_label"])}</h3><blockquote>{escape(item["body"])}</blockquote>'
           if item["body"] else "")
        + '<div class="pick">'
        + f'<a class="topnav" href="{prev}" id="review-prev">← Previous</a>'
        + (f'<form method="post" action="/c/{escape(item["id"])}/confirm" '
           f'style="display:inline">'
           f'<input type="hidden" name="next" value="1">{confirm_btn}</form>'
           if may_confirm else "")
        + f'<a class="topnav" href="{skip}" id="review-skip">Skip →</a>'
        + f'<a class="topnav" href="/c/{escape(item["id"])}">Open control page</a>'
        + "</div></div>"
        + """<script>
document.addEventListener('keydown', function (e) {
  if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
  var id = e.key === 'ArrowLeft' ? 'review-prev'
        : e.key === 'ArrowRight' ? 'review-skip' : null;
  if (id) { var a = document.getElementById(id); if (a) a.click(); }
});
</script>"""
    )
    return page("Review queue", body, nav=nav)


def soa(view, rows: list[dict], nav: str = "") -> str:
    body_rows = "".join(
        "<tr>"
        f'<td class="c">{escape(r["short"])}</td><td>{escape(r["label"])}</td>'
        f'<td class="{"pending" if r["applicable"] == "TBD" else ""}">'
        f'{escape(r["applicable"])}</td>'
        f'<td>{escape(r["reason"])}</td><td>{escape(r["status"])}</td>'
        f'<td>{escape(r["note"])}</td></tr>'
        for r in rows
    )
    pending = sum(1 for r in rows if r["applicable"] == "TBD")
    body = (
        f"<h2>{escape(view.name)} · Statement of Applicability</h2>"
        + _subnav(escape(view.id), "/soa")
        + f'<p class="note">{len(rows)} controls, <span class="pending">{pending} still TBD</span>. '
        f'Unfilled rows are listed too: a SoA that silently omits controls is worse than an incomplete one. '
        f'<a href="/f/{escape(view.id)}/soa.csv">Download CSV</a></p>'
        '<table class="soa"><tr>'
        "<td><strong>Control</strong></td><td><strong>Name</strong></td>"
        "<td><strong>Applicability</strong></td><td><strong>Reason if N/A</strong></td>"
        "<td><strong>Implementation status</strong></td><td><strong>Notes / evidence</strong></td></tr>"
        f"{body_rows}</table>"
    )
    return page(f"{view.name} · Statement of Applicability", body, crumb=escape(view.id), nav=nav)


# ---------- 登录 / 邀请 ----------

def login(error: str = "", next_url: str = "/", entra: bool = False) -> str:
    sso = ""
    if entra:
        # SSO 在上、口令在下：接了 Entra 的组织里，口令是例外不是常态。
        sso = (
            f'<p style="margin:0 0 1.4rem"><a class="sso" '
            f'href="/auth/entra?next={escape(next_url)}">Sign in with your company account</a></p>'
            '<p class="crumb" style="text-align:center;margin:0 0 1.4rem">'
            "or continue with an email passphrase</p>"
        )
    body = (
        '<div class="auth"><h2>Sign in</h2>'
        + (f'<p class="err">{escape(error)}</p>' if error else "")
        + sso
        + f'<form action="/login" method="post">'
        f'<input type="hidden" name="next" value="{escape(next_url)}">'
        '<label for="email">Email</label>'
        '<input type="text" id="email" name="email" autocomplete="username" required>'
        '<div style="height:.9rem"></div>'
        '<label for="password">Passphrase</label>'
        '<input type="password" id="password" name="password" '
        'autocomplete="current-password" required>'
        '<p style="margin:1.2rem 0 0"><button type="submit">Sign in</button></p>'
        '<p class="hint">No account? Ask an administrator to send you an invite link with <code>fr account invite</code>.</p>'
        "</form></div>"
    )
    return page("Sign in", body, bare=True)


def invite(token: str, email: str, role: str, error: str = "") -> str:
    body = (
        '<div class="auth"><h2>Set your passphrase</h2>'
        f'<p class="note">{escape(email)} · role <code>{escape(role)}</code></p>'
        + (f'<p class="err">{escape(error)}</p>' if error else "")
        + f'<form action="/invite/{escape(token)}" method="post">'
        '<label for="display_name">Display name</label>'
        '<input type="text" id="display_name" name="display_name">'
        '<div style="height:.9rem"></div>'
        '<label for="password">Set passphrase (at least 12 characters)</label>'
        '<input type="password" id="password" name="password" '
        'autocomplete="new-password" required>'
        '<div style="height:.9rem"></div>'
        '<label for="again">Type it again</label>'
        '<input type="password" id="again" name="again" '
        'autocomplete="new-password" required>'
        '<p style="margin:1.2rem 0 0"><button type="submit">Set passphrase and sign in</button></p>'
        "</form></div>"
    )
    return page("Set passphrase", body, bare=True)


def refused(title: str, message: str, hint: str = "") -> str:
    return page(title, (
        f"<h2>{escape(title)}</h2>"
        f'<p class="err">{escape(message)}</p>'
        + (f'<p class="note">{escape(hint)}</p>' if hint else "")
        + '<p><a href="/">Back to home</a></p>'
    ))


# ---------- 成员与审计 ----------

_MEMBER_CSS = """
.mtable td{vertical-align:middle}
.chips{display:flex;gap:.35rem;flex-wrap:wrap}
.chips form{background:none;border:0;padding:0;display:contents}
.chip{font-family:var(--mono);font-size:.72rem;padding:.22rem .5rem;
  border:1px solid var(--rule);background:var(--ground);color:var(--muted);
  border-radius:2px;cursor:pointer}
.chip.on{border-color:var(--accent);color:#fff;background:var(--accent);font-weight:600}
.chip.flat{cursor:default}
.gone{color:var(--muted);text-decoration:line-through}
.switch{margin:2.5rem 0 0;background:var(--surface);border:1px solid var(--rule);
  padding:1.1rem 1.2rem}
.switch button{font-size:.8rem;padding:.35rem .8rem;background:var(--ground);
  color:var(--body);border:1px solid var(--rule)}
.link{font-family:var(--mono);font-size:.8rem;word-break:break-all;
  background:var(--sunk);border-left:3px solid var(--accent);padding:.8rem 1rem;
  margin:0 0 1.2rem}
.audit{font-family:var(--mono);font-size:.78rem}
.audit td{white-space:nowrap}
.audit td.d{white-space:normal;font-family:var(--han);font-size:.85rem}
"""

ROLE_WHAT = {
    "admin": "Manage accounts, configure models, delete frameworks, read the audit log",
    "author": "Import frameworks, draft interpretations (costs money), edit fields, record self-assessments",
    "approver": "Confirm interpretations by signing",
    "viewer": "Browse and export",
}

# 界面上读中文，但**英文标识符不能藏起来**：CLI 用的是它
# （`fr account grant 谁 author`），纯中文会让人在终端里对不上号。
ROLE_NAME = {
    "admin": "Admin",
    "author": "Editor",
    "approver": "Approver",
    "viewer": "Viewer",
}


def _role_chips(row: dict, editable: bool) -> str:
    from framework_reader.identity import ROLES

    if not editable:
        if not row["roles"]:
            return '<span class="chip flat">(no roles)</span>'
        return '<div class="chips">' + "".join(
            f'<span class="chip flat on" title="{r}">{ROLE_NAME[r]}</span>'
            for r in ROLES if r in row["roles"]
        ) + "</div>"
    buttons = "".join(
        f'<button class="chip{" on" if on else ""}" type="submit" '
        f'name="{"revoke" if on else "grant"}" value="{r}" '
        f'title="{r}: {escape(ROLE_WHAT[r])}">{ROLE_NAME[r]}</button>'
        for r in ROLES for on in [r in row["roles"]]
    )
    return (f'<div class="chips"><form method="post" '
            f'action="/members/{escape(row["id"])}/role">{buttons}</form></div>')


def _first_admin(error: str = "", *, nav: str = "") -> str:
    """一个账号都没有时，成员页就是这张表单。

    名单和邀请块这时都是空的，摆出来只会让人以为坏了。建完这一个，
    门就锁上——之后进来的人靠邀请。
    """
    body = (
        f"<style>{_MEMBER_CSS}</style>"
        "<h2>Create the first administrator</h2>"
        + (f'<p class="err">{escape(error)}</p>' if error else "")
        + '<p class="note">There are <strong>no accounts yet</strong>, so anyone who opens this address can use it. '
        "Fine for running it yourself on this machine; not fine once others use it. "
        "The moment the first administrator exists, the door locks: from then on people get in by invitation, "
        "and what each person can do is decided by their roles.</p>"
        '<p class="note">Administrators manage accounts, configure models and read the audit log. '
        "They do <strong>not</strong> include drafting or confirming: drafting spends the organization\'s money, "
        "confirming is a professional judgement. To do the work yourself, come back to this page and add "
        "\u300cEditor\u300d to your own account.</p>"
        '<form method="post" action="/members/bootstrap">'
        '<div class="row">'
        '<div><label for="email">Email</label>'
        '<input type="text" id="email" name="email" required></div>'
        '<div><label for="display_name">Display name</label>'
        '<input type="text" id="display_name" name="display_name"></div>'
        "</div>"
        '<div class="row">'
        '<div><label for="password">Passphrase</label>'
        '<input type="password" id="password" name="password" required></div>'
        '<div><label for="again">Type it again</label>'
        '<input type="password" id="again" name="again" required></div>'
        "</div>"
        '<button type="submit">Create the administrator and lock the door</button>'
        '<p class="hint">At least 12 characters. This passphrase is the key to all your compliance material, '
        "and there is no password recovery: if it is lost, the identity database must be wiped and set up again.</p>"
        "</form>"
    )
    return page("Members", body, nav=nav)


def members(rows: list[dict], *, can_manage: bool, self_grant_allowed: bool,
            invite_link: str = "", error: str = "", entra: bool = False,
            bootstrap: bool = False, nav: str = "") -> str:
    from framework_reader.identity import ROLES

    if bootstrap:
        return _first_admin(error, nav=nav)

    body_rows = []
    for row in rows:
        status = (
            f'<form method="post" action="/members/{escape(row["id"])}/status">'
            f'<button class="chip" type="submit" name="status" '
            f'value="{"active" if not row["active"] else "disabled"}">'
            f'{"Re-enable" if not row["active"] else "Disable"}</button></form>'
            if can_manage else ("" if row["active"] else "Disabled")
        )
        body_rows.append(
            "<tr>"
            f'<td class="{"" if row["active"] else "gone"}">{escape(row["email"])}'
            + (f'<br><span class="crumb">{escape(row["display_name"])}</span>'
               if row["display_name"] else "")
            + "</td>"
            f'<td>{_role_chips(row, can_manage)}</td>'
            f"<td>{status}</td></tr>"
        )

    invite = ""
    if can_manage:
        from framework_reader.identity import DEFAULT_ROLE

        # 默认选中的是**只读**，不是列表里的第一个（那是 admin）。
        # 下拉框的默认值和「新账号默认 viewer」是同一条规矩：
        # 默认值决定了点快了会发生什么，而点快了是常态。
        options = "".join(
            f'<option value="{r}"{" selected" if r == DEFAULT_ROLE else ""}>'
            f"{ROLE_NAME[r]} ({r}): {escape(ROLE_WHAT[r])}</option>"
            for r in ROLES
        )
        invite = (
            "<h2>Invite people</h2>"
            + (f'<div class="link">{escape(invite_link)}</div>'
               '<p class="note">This link is shown only once; the database keeps only its hash. '
               'Valid for seven days and void after one use.</p>' if invite_link else "")
            + '<form method="post" action="/members/invite">'
            '<div class="row">'
            '<div><label for="email">Email</label>'
            '<input type="text" id="email" name="email" required></div>'
            '<div><label for="role">Role</label>'
            f'<select id="role" name="role" style="width:100%;padding:.5rem .6rem;'
            f'font:inherit;font-size:.9rem">{options}</select></div></div>'
            "<button type=\"submit\">Send invitation</button>"
            '<p class="hint">Invitation links are not emailed: copy and send it yourself.</p>'
            "</form>"
        )

    switch = ""
    if can_manage:
        switch = (
            '<div class="switch">'
            "<strong>Admins cannot grant themselves roles</strong>"
            f'<span class="crumb" style="margin-left:.6rem">'
            f'{"lock off" if self_grant_allowed else "lock on"}</span>'
            '<p class="note" style="margin:.6rem 0 .9rem">'
            "Administrators may grant roles to others; raising your own permissions needs another administrator to agree. "
            "Privilege escalation is the first step of every incident, and single-administrator organizations would be blocked by it, "
            "so this switch exists. Flipping it either way goes to the audit log.</p>"
            '<form method="post" action="/members/self-grant">'
            f'<button type="submit" name="allowed" '
            f'value="{"0" if self_grant_allowed else "1"}">'
            f'{"Turn the lock back on" if self_grant_allowed else "Turn this lock off"}</button>'
            "</form></div>"
        )

    body = (
        f"<style>{_MEMBER_CSS}</style>"
        "<h2>Members</h2>"
        + (f'<p class="err">{escape(error)}</p>' if error else "")
        + '<p class="note">Roles are <strong>additive</strong>, not a tree: a person\'s permissions are the union of their roles. '
        "Administrators do not include drafting or confirming: drafting spends the organization\'s money, "
        "confirming is a professional judgement.</p>"
        # 不写出来，管理员会以为自己在这一页改的生效了。设计 §5.4
        + ('<p class="err">This deployment uses <strong>Entra ID</strong>: for accounts that sign in via SSO, roles come from Entra App Roles and '
           '<strong>changes made on this page are overwritten at their next sign-in</strong>. '
           "For a lasting change, edit the assignment in Entra.</p>" if entra else "")
        + '<table class="mtable"><tr><td><strong>Account</strong></td>'
        "<td><strong>Roles</strong></td><td><strong>Status</strong></td></tr>"
        + "".join(body_rows) + "</table>"
        + invite + switch
    )
    return page("Members", body, nav=nav)


def audit(entries: list[dict], nav: str = "") -> str:
    rows = "".join(
        "<tr>"
        f'<td>{escape(e["at"][:19].replace("T", " "))}</td>'
        f'<td>{escape(e["event"])}</td>'
        f'<td>{escape(e["actor"] or "-")}</td>'
        f'<td class="d">{escape(e["detail"])}</td></tr>'
        for e in entries
    )
    body = (
        f"<style>{_MEMBER_CSS}</style>"
        "<h2>Audit log</h2>"
        '<p class="note">Append-only, never edited or deleted. Role changes, confirmations, model configuration, sign-in outcomes. '
        'A compliance product is not credible without this page.</p>'
        + ('<div class="soawrap"><table class="audit"><tr>'
           "<td><strong>Time</strong></td><td><strong>Event</strong></td>"
           "<td><strong>Who</strong></td><td><strong>What</strong></td></tr>"
           f"{rows}</table></div>" if entries else
           '<p class="empty">No entries yet.</p>')
    )
    return page("Audit log", body, nav=nav)


# ---------- 模型与 key ----------

ROLE_WHAT_FOR = {
    "drafter": "Drafts interpretations and rewrites fields. <strong>This role is the product itself</strong>, "
               "swapping it swaps the product",
    "questioner": "Asks questions during interviews (idle in the B pipeline)",
    "extractor": "Extracts structure from the author\'s own words (idle in the B pipeline)",
}


def settings(*, bootstrap: bool = False, nav: str = "") -> str:
    """把配置类的入口收在一处。

    在这之前它们是顶栏上并排的三个链接，而**「模型与 key」那一个从来没显示过**：
    它被 `logged_in()` 挡着，而那个判断的本意是「本机单人没有成员这回事」——
    对成员成立，对模型不成立。本机单人恰恰是最需要配自己 key 的那种用法。

    每一块按权限单独判。一样都看不到的人（viewer 只有 member:read，
    而成员那块要 logged_in）会看到一句话，不是一个空页面：空页面让人以为坏了。
    """
    cards = []
    if may("model:read"):
        cards.append((
            "/models", "Models and keys",
            "Which provider and model drafts interpretations, the API keys, and the three spending limits "
            "(per person per hour / per organization per month / concurrent jobs)."))
    # 一个账号都没有时**更**要挂这个入口：那正是建第一个管理员的时刻。
    # 原先这里挡着 `logged_in()`，于是本机跑 `fr serve` 的人在界面上
    # 根本找不到用户管理，只能去终端跑 `fr account invite`。
    if may("member:read"):
        cards.append((
            "/members", "Members and roles",
            "Create the first administrator and the door locks: after that people get in by invitation."
            if bootstrap else
            "Who can draft, who can sign, who can only look. Send invitations, disable accounts, "
            "and the switch for the no-self-grant lock."))
    if may("member:manage"):
        # 这两块是系统配置，只有管理员看得到入口；子页路由同样要
        # member:manage——看见入口和做得了事是同一条权限。
        cards.append((
            "/settings/sso", "Single sign-on (Entra ID)",
            "Let people sign in with their company accounts (OIDC + PKCE). "
            "Configure the tenant, the app registration and the redirect URI here; "
            "saved settings take precedence over environment variables."))
        cards.append((
            "/settings/branding", "Custom logo",
            "Replace the workbench name in the top bar with your organization's logo. "
            "PNG / JPEG / WebP / GIF / SVG up to 512 KB; shown on every page including sign-in."))
    if may("audit:read"):
        cards.append((
            "/audit", "Audit log",
            "Who did what and when: sign-in outcomes, role changes, confirmations, model endpoint changes."))
    if may("framework:import"):
        cards.append((
            "/settings/backup", "Backup",
            "Download a snapshot of the user database: imported frameworks, edited interpretations, self-assessments, documents. "
            "The identity database is not included."))

    body = (
        f"<style>{_SETTINGS_CSS}</style>"
        "<h2>Settings</h2>"
        + ("".join(
            f'<a class="scard" href="{href}"><h3>{escape(title)}</h3>'
            f"<p>{escape(what)}</p></a>"
            for href, title, what in cards)
           if cards else
           '<p class="empty">Nothing here you can change; configuration is an administrator\'s job.</p>')
    )
    return page("Settings", body, nav=nav)


def sso_settings(saved: dict | None, *, from_env: bool = False,
                 report: dict | None = None, nav: str = "") -> str:
    """Entra ID 单点登录的配置页。保存且启用的配置优先于环境变量。

    `report` 是「Test connection」的结果：problems 是逐项体检清单，
    discovery_error 是发现文档拉取失败的原因，issuer 是对方自报的门牌。
    """
    status = "Not configured - only email passcode sign-in works."
    if saved and saved.get("enabled"):
        source = "saved in Settings" if saved.get("tenant_id") else "enabled (incomplete)"
        status = f"Active - {source}."
    elif from_env:
        status = "Active - from environment variables. A saved configuration below takes precedence."

    problem_rows = "".join(
        f'<p class="warn">✗ {escape(p)}</p>' for p in (report or {}).get("problems", []))
    if (report or {}).get("discovery_error"):
        problem_rows += (f'<p class="warn">✗ Discovery document: '
                         f'{escape(report["discovery_error"])}</p>')
    if report and not problem_rows:
        problem_rows = (f'<p class="note">✓ Discovery document reached. Issuer: '
                        f'<code>{escape(report.get("issuer", ""))}</code></p>'
                        '<p class="hint">Still worth one real sign-in with a company account, '
                        "then check the roles in Members.</p>")

    body = (
        "<h2>Single sign-on (Entra ID)</h2>"
        f'<p class="note">{escape(status)}</p>'
        + problem_rows
        + '<form method="post" action="/settings/sso">'
        + '<div class="row">'
        + '<div><label for="tenant_id">Directory (tenant) ID</label>'
        + f'<input id="tenant_id" type="text" name="tenant_id" value="{escape((saved or {}).get("tenant_id", ""))}"></div>'
        + '<div><label for="client_id">Application (client) ID</label>'
        + f'<input id="client_id" type="text" name="client_id" value="{escape((saved or {}).get("client_id", ""))}"></div>'
        + "</div>"
        + '<label for="client_secret">Client secret</label>'
        + '<input id="client_secret" type="password" name="client_secret" autocomplete="new-password"'
        + ('placeholder="saved - leave blank to keep it"' if (saved or {}).get("has_secret") else "")
        + '>'
        + '<label for="redirect_uri">Redirect URI</label>'
        + f'<input id="redirect_uri" type="text" name="redirect_uri" value="{escape((saved or {}).get("redirect_uri", ""))}">'
        + '<p class="hint">Must match the app registration exactly, and should end with '
        "<code>/auth/entra/callback</code>. When it starts with https://, session cookies are marked Secure.</p>"
        + '<label for="authority">Authority (advanced)</label>'
        + f'<input id="authority" type="text" name="authority" value="{escape((saved or {}).get("authority", "") or "https://login.microsoftonline.com")}">'
        + '<p style="margin:1rem 0 0"><label style="display:inline;font-size:.9rem;color:var(--body)">'
        + f'<input type="checkbox" name="enabled"{" checked" if (saved or {}).get("enabled", True) else ""}> '
        "Enabled - offer company-account sign-in on the login page</label></p>"
        + '<p style="margin:1.2rem 0 0"><button type="submit">Save</button> '
        + '<button type="submit" formaction="/settings/sso/check" formnovalidate>Test connection</button>'
        + ('<button type="submit" formaction="/settings/sso/disable" style="margin-left:.8rem">'
           "Remove saved configuration</button>" if saved else "")
        + "</p></form>"
        + '<p class="hint">The secret is stored encrypted (same master key as the model API keys). '
        "In the Entra app registration: single tenant, App Role values exactly "
        "admin / author / approver / viewer, and user assignment on.</p>"
    )
    return page("Single sign-on", body, nav=nav)


def branding_settings(logo: dict | None, *, error: str = "", nav: str = "") -> str:
    """自定义 logo：传一张图，顶栏的品牌名换成它，登录页同样生效。"""
    preview = ""
    if logo:
        preview = (f'<p><img class="brandlogo" src="/branding/logo?v={logo["version"]}" '
                   f'alt="Current logo" style="height:3rem"></p>')
    body = (
        "<h2>Custom logo</h2>"
        + (f'<p class="err">{escape(error)}</p>' if error else "")
        + (preview or '<p class="empty">No custom logo - the top bar shows the workbench name.</p>')
        + '<form method="post" action="/settings/branding" enctype="multipart/form-data">'
        + '<label for="file">Logo image</label>'
        + '<input id="file" type="file" name="file" accept=".png,.jpg,.jpeg,.webp,.gif,.svg">'
        + '<p class="hint">PNG, JPEG, WebP, GIF or SVG, up to 512 KB. SVG is sanitized on '
        "upload (scripts, event handlers and external references are stripped) and served "
        "with a script-blocking policy. A square image around 128 px looks best at the "
        "top-bar size.</p>"
        + '<p style="margin:1.2rem 0 0"><button type="submit">Upload</button>'
        + ('<button type="submit" formaction="/settings/branding/remove" '
           'style="margin-left:.8rem">Remove logo</button>' if logo else "")
        + "</p></form>"
    )
    return page("Custom logo", body, nav=nav)


def backup(frameworks: list[dict] | None = None, *, nav: str = "") -> str:
    """点一下下载 sqlite；有解读的框架另外各下一份 PDF。"""
    body = [
        "<h2>Back up the user database</h2>",
        '<p class="note">What you download is a consistent snapshot, not the live file. '
        "It contains: imported frameworks and control bodies, interpretations edited on the web, self-assessments, document chunks, and control-page conversations.</p>",
        '<p class="note"><strong>Not</strong> the identity database: passphrase hashes, sessions and model keys are not in this file. '
        "The built-in content package is not either; that one can be rebuilt.</p>",
        '<p class="note">To restore after a failure: stop <code>fr serve</code>, put this file at '
        "<code>~/.framework_reader/user.sqlite</code> (move the current one aside first).</p>",
        '<form method="post" action="/settings/backup">'
        '<p style="margin:0"><button type="submit">Download sqlite</button></p>'
        "</form>",
    ]
    pdf_ready = [f for f in (frameworks or []) if f.get("with_interp")]
    body.append("<h2>Frameworks with interpretations</h2>")
    if not pdf_ready:
        body.append('<p class="note">No frameworks with interpretations to export yet.</p>')
    else:
        body.append(
            '<p class="note">Each PDF includes only controls that have interpretations. '
            'AI drafts are marked as such. Derived mappings are excluded.</p>'
        )
        rows = "".join(
            f'<tr><td>{escape(f["name"])}</td>'
            f'<td style="white-space:nowrap">{f["with_interp"]}/{f["controls"]}</td>'
            f'<td><form class="tiny" method="post" '
            f'action="/settings/backup/{escape(f["id"])}/pdf">'
            '<button type="submit">Download PDF</button></form></td></tr>'
            for f in pdf_ready
        )
        body.append(
            "<table><tr><td><strong>Framework</strong></td>"
            "<td><strong>Has interpretation</strong></td><td></td></tr>"
            f"{rows}</table>"
        )
    return page("Backup", "".join(body), crumb="Settings", crumb_href="/settings", nav=nav)


_SETTINGS_CSS = """
.scard{display:block;background:var(--surface);border:1px solid var(--rule);
  padding:1.1rem 1.2rem;margin:0 0 .9rem;text-decoration:none}
.scard:hover{border-color:var(--accent)}
.scard h3{margin:0 0 .35rem;font-size:1rem;color:var(--ink);font-weight:600}
.scard p{margin:0;font-size:.9rem;color:var(--muted)}
"""


def models(*, roles: dict, presets: list[dict], keys: dict, limits: dict,
           spent: int, can_write: bool, master_key: bool, custom: dict | None = None,
           catalogs: dict | None = None,
           focus: tuple[str, str, str] | None = None,
           error: str = "", notice: str = "", nav: str = "") -> str:
    """`keys` 里只有脱敏串。**明文与密文都不到这一层。**"""
    warn = ""
    if not master_key:
        warn = (
            '<p class="err"><strong>FR_SECRET_KEY is not configured yet.</strong>'
            " Until it is, I will not store a single API key in the database: silently kept plaintext would make you believe it is encrypted when it is not. "
            "Run <code>fr secret new</code> on the server to generate a key, inject it as an environment variable and restart.</p>")

    # ---- 角色 ----
    catalogs = catalogs or {}

    def _datalist(list_id: str, values, labels: dict | None = None) -> str:
        """`<option>` 的正文是候选项的**副标题**，填进框里的只有 value。

        自定义端点靠它标出来——不标的话，自己加的内网网关和二十家预设
        混在一列里分不出谁是谁。而把「（自定义）」并进 value，
        选一下就把这四个字填进了表单。
        """
        labels = labels or {}
        return (f'<datalist id="{escape(list_id)}">'
                + "".join(
                    f'<option value="{escape(v)}">{escape(labels[v])}</option>'
                    if v in labels else f'<option value="{escape(v)}">'
                    for v in values)
                + "</datalist>")

    def _model_field(role: str, provider: str, current_model: str,
                     grab_focus: bool = False) -> str:
        """一个输入框，背后挂目录。**目录是便利，不是唯一入口**——
        新模型上线永远早于任何目录，自定义端点与内网网关也未必有这个接口。

        原来这里是「下拉选一个」加「或者手填」两个控件，值还要在服务端二选一。
        `datalist` 天生就是这两件事的同一个控件：能选，也能填。
        顺带解决了原生 `<select>` 展开时用系统菜单字号那个问题——
        datalist 的候选由浏览器画在页面内，字号跟着 input 走。

        没拉过 / 拉失败 / 目录为空时，只挂一句原因，**不挂一个空目录**——
        点开什么都没有，比没得点更像坏了。
        """
        cached = catalogs.get(provider)
        options = ""
        list_attr = ""
        if cached is None:
            hint = ("Once this provider\'s key is saved, the available models are fetched once automatically.")
        elif cached["error"]:
            hint = escape(cached["error"])
        elif cached["models"]:
            list_id = f"models-{role}"
            options = _datalist(list_id, cached["models"])
            list_attr = f' list="{escape(list_id)}"'
            hint = (f'Fetched from {escape(provider)} at '
                    f'{escape(cached["fetched_at"][:16])}: open to pick, or type your own')
        else:
            hint = f"{escape(provider)}\'s catalog is empty; type the model name."

        refresh = ""
        if can_write and cached is not None:
            refresh = (
                f'<button type="submit" form="refresh-{escape(provider)}" '
                'class="linky">Refresh</button>')
        # 刚在这一块里动过手：把焦点放这儿。浏览器会把它滚进视野——
        # 提交后滚动条被打回顶端的话，上面那条反馈就又看不见了，
        # 而各家浏览器的滚动恢复行为不归我管。零 JS 的办法只有这一个。
        focus_attr = " autofocus" if grab_focus else ""
        return (
            f"<label>Model name</label>"
            f'<input type="text" name="model" class="pick"{list_attr}{focus_attr}'
            f' value="{escape(current_model)}" placeholder="Open to pick, or type your own">'
            f"{options}"
            f'<p class="hint">{hint} {refresh}</p>'
        )

    def _provider_picker(current: str) -> str:
        """厂商是**封闭集合**，所以用 `<select>`。

        原来这里和模型名一样是 `<input list>`：可 datalist 的候选**按当前值
        过滤**——框里已经有「minimax」时点开，就只剩它自己，于是「必须先把
        现有的删掉才能换一家」。那不是 datalist 的毛病，是控件选错了：
        datalist 是给**开放集合**做建议用的，而厂商填错了服务端当场退回
        （`_known_providers()`），这就是封闭集合的定义。

        模型名相反——新模型永远早于任何目录，自定义端点也未必有那个接口——
        所以那边继续用 datalist，能选也能填。

        代价是 macOS 的原生 select 展开时用系统菜单字号，CSS 碰不到
        （见 2026-08-25 那次改动）。那是美观，这是能不能用，不是一回事。
        """
        options = []
        if not current:
            options.append(
                '<option value="" selected disabled>Pick a provider</option>')
        elif current not in set(provider_ids):
            # 预设改了名、或者自定义端点被删了。**不能让 select 悄悄换一家**：
            # select 总会提交点什么，把认不出的值扔掉等于替人改了配置，
            # 而他可能只是想看看这一页。
            options.append(
                f'<option value="{escape(current)}" selected>'
                f"{escape(current)} (stale, not in the list)</option>")
        for pid in provider_ids:
            # 正文只放编号（加一个自定义标记）。塞整句说明进去，浏览器会按
            # 最长那条撑开弹出层——说明在下面那张一览表里，那儿放得下。
            label = pid + (" (custom endpoint)" if pid in provider_labels else "")
            picked = " selected" if pid == current else ""
            options.append(f'<option value="{escape(pid)}"{picked}>'
                           f"{escape(label)}</option>")
        return (f'<select name="provider" class="pick">'
                f'{"".join(options)}</select>')

    def _key_field(provider: str) -> str:
        """这家还没 key 时，就在这儿填。

        原来 key 要滚到下面「API key」那一栏、再选一次同一个厂商才能填——
        同一件事分两处做，中间还要重选一遍厂商。

        **key 是按厂商存的，不是按角色。** 在 drafter 这块填的 openai key，
        questioner 用的也是它。这句话必须写在界面上，否则看着像每个角色各存一把。
        """
        if not can_write or not provider or provider in keys:
            return ""
        return (
            '<div class="row"><div><label>No API key for this provider yet; enter it here</label>'
            '<input type="password" name="key" placeholder="Saves the key first, then fetches the model list">'
            f'<p class="hint">Keys are stored <strong>per provider</strong>, not per role: '
            f"the {escape(provider)} key entered here is what the other roles use too.</p></div>"
            "<div></div></div>"
        )

    # 候选里**只放编号**。把说明塞进候选项，浏览器会按最长那条撑开下拉——
    # 实测盖住半个屏幕，最长那条还被截成「不建议用作…」。
    # 说明去下面那张一览表，那里放得下整句，也更显眼。
    provider_ids = [p["id"] for p in presets]
    provider_labels = {p["id"]: "custom endpoint" for p in presets if p.get("custom")}
    role_rows = []
    for name, what in ROLE_WHAT_FOR.items():
        # `roles` 给的是**实际生效**的值（网页上没配的回落到 YAML 预设）。
        # 只显示「这里配过的」，会让没配过的角色显示成一片空白——
        # 而这一页要回答的问题是「现在到底谁在收我们的钱」。
        current = roles.get(name, {})
        provider = current.get("provider", "")
        model = current.get("model", "")
        # 刚在这一块里动过手（配 key、或者测了一下）：停在**新**厂商上，
        # 别弹回旧的——弹回去等于让人再选一遍，这个改动就白做了。
        #
        # 第三个元素是模型名：配完 key 时它是空的（那一刻还没选模型），
        # 测完时它是刚验过的那个——测通了下一步就是点保存，
        # 这时候把框清空等于逼人再填一遍自己刚验过的字。
        if focus and focus[0] == name:
            provider, model = focus[1], focus[2]

        # **反馈落在按下去的那一块里。** 只渲在页面顶端的话，按钮在页面中部的人
        # 提交完什么都看不见——浏览器可能保留滚动位置，那条字在视野上方一千像素。
        # 这一页已经因为同一个毛病被误读过一次（配 key 那次说「保存没反应」）。
        said = ""
        if focus and focus[0] == name:
            if error:
                said = f'<p class="err">{escape(error)}</p>'
            elif notice:
                said = f'<p class="signed">{escape(notice)}</p>'

        source = ("set on this page" if current.get("overridden")
                  else "from the built-in preset content/llm_providers.yaml")
        form = ""
        if can_write:
            form = (
                f'<form method="post" action="/models/role">'
                f'<input type="hidden" name="role" value="{escape(name)}">'
                '<div class="row">'
                f'<div><label>Provider</label>'
                f"{_provider_picker(provider)}"
                '<p class="hint">One of the listed providers only. '
                "Your own internal gateway goes under \u300ccustom endpoints\u300d below.</p></div>"
                f'<div>{_model_field(name, provider, model, bool(said))}</div>'
                "</div>"
                f"{_key_field(provider)}"
                # 「测一下」和「保存」共用这张表单，所以测的就是此刻框里的那组，
                # 不是库里存着的那组。formaction 覆盖 action，不需要第二张表单。
                '<button type="submit" class="ghost" '
                'formaction="/models/role/test">Test</button>'
                "<button type=\"submit\">Save</button>"
                '<p class="hint">Run \u300cTest\u300d first: it sends one minimal real request and can tell a wrong key, a model name this provider does not know, and a connection failure apart. '
                "Save only once the test passes.</p></form>"
                # 「刷新」按钮借这张隐藏表单提交——它不能嵌在上面那张表单里。
                f'<form id="refresh-{escape(provider)}" method="post" '
                f'action="/models/catalog/refresh" style="display:none">'
                f'<input type="hidden" name="provider" value="{escape(provider)}">'
                "</form>"
            )
        role_rows.append(
            f'<div class="mrow"><h3>{escape(name)}</h3>'
            f'<p class="note">{what}</p>{said}'
            f'<p class="cur">Now: {escape(provider) or "(none)"} · '
            f'<code>{escape(model)}</code> · {source}</p>{form}</div>'
        )

    # ---- key ----
    key_rows = "".join(
        f"<tr><td>{escape(provider)}</td>"
        f'<td><code>{escape(row["masked"])}</code></td>'
        f'<td>{escape(row["set_by"] or "-")}</td>'
        f'<td>{escape((row["set_at"] or "")[:10])}</td>'
        + (f'<td><form method="post" action="/models/key">'
           f'<input type="hidden" name="provider" value="{escape(provider)}">'
           f'<button class="chip" type="submit" name="clear" value="1">Clear'
           f"</button></form></td>" if can_write else "<td></td>")
        + "</tr>"
        for provider, row in sorted(keys.items())
    )
    key_form = ""
    if can_write:
        key_form = (
            '<form method="post" action="/models/key"><div class="row">'
            '<div><label>Provider</label>'
            f"{_provider_picker('')}"
            '<p class="hint">One of the listed providers only.</p></div>'
            '<div><label>API key</label>'
            '<input type="password" name="key" autocomplete="off"></div>'
            "</div><button type=\"submit\">Save this key</button>"
            '<p class="hint">Only the last four characters are shown afterwards. To change it, enter a new one; '
            "the old is overwritten.</p></form>"
        )

    # ---- 闸 ----
    limit_form = ""
    if can_write:
        limit_form = (
            '<form method="post" action="/models/limits"><div class="row">'
            f'<div><label>Per person per hour (controls)</label><input type="text" '
            f'name="draft_cap_hour" value="{limits["draft_cap_hour"]}"></div>'
            f'<div><label>Organization per month (controls)</label><input type="text" '
            f'name="draft_cap_month" value="{limits["draft_cap_month"]}"></div>'
            "</div><div class=\"row\">"
            f'<div><label>Concurrent draft jobs</label><input type="text" '
            f'name="draft_max_jobs" value="{limits["draft_max_jobs"]}"></div>'
            "<div></div></div>"
            "<button type=\"submit\">Save limits</button>"
            '<p class="hint">To stop drafting entirely, revoke that person\'s author role: limits are not an on/off switch.</p></form>'
        )

    # 2026-08-26：「厂商一览」那张 20 行的表按要求整块拿掉。
    # 连带没了的是每家的 note（含 MiniMax 那句「起草质量实测不合格」），
    # 以及「我们验过」（预设属性）与「你的 key 此刻拉不拉得通」（运行时事实）
    # 那两列。presets 里的 note / verified 现在页面上没有出口。

    # ---- 自定义端点 ----
    custom = custom or {}
    rows = "".join(
        f"<tr><td><code>{escape(pid)}</code></td>"
        f"<td><code>{escape(row['base_url'])}</code></td>"
        f"<td>{escape(row['default_model'])}</td>"
        f"<td>{escape(row['added_by'] or '')}</td>"
        + (f'<td><form method="post" action="/models/provider/delete">'
           f'<input type="hidden" name="provider" value="{escape(pid)}">'
           f'<button type="submit" class="danger">Delete</button></form></td>'
           if can_write else "<td></td>")
        + "</tr>"
        for pid, row in sorted(custom.items())
    )
    custom_form = ""
    if can_write:
        custom_form = (
            '<form method="post" action="/models/provider"><div class="row">'
            '<div><label>ID (lowercase letters, digits and dashes)</label>'
            '<input type="text" name="provider" placeholder="corp-gw"></div>'
            '<div><label>Default model name</label>'
            '<input type="text" name="default_model" placeholder="qwen2.5-72b"></div>'
            "</div>"
            '<p><label>Endpoint URL (OpenAI-compatible)</label>'
            '<input type="text" name="base_url" placeholder="https://gw.example.com/v1">'
            "</p><button type=\"submit\">Add this endpoint</button>"
            '<p class="hint">Then set its key under "API key" above, and point <code>drafter</code> at it under "which model each role uses".</p></form>'
        )
    custom_block = (
        "<h2>Custom endpoints</h2>"
        '<p class="note">Presets cannot keep up with the market and cannot know your own gateway. '
        "Any <strong>OpenAI-compatible</strong> endpoint works here: a company internal gateway, your own Azure deployment, a local vLLM / Ollama."
        "<br><strong>URL rules</strong>: <code>https://</code> is unrestricted; "
        "<code>http://</code> is allowed only on internal networks (loopback, 10.x, 172.16-31.x, 192.168.x); on the public internet your key and framework text travel in cleartext."
        "<br>Changing the endpoint is <strong>changing where your data goes</strong>, and every change goes to the audit log.</p>"
        + (('<table class="mtable"><tr><td><strong>ID</strong></td>'
            "<td><strong>URL</strong></td><td><strong>Default model</strong></td>"
            "<td><strong>Added by</strong></td><td></td></tr>"
            f"{rows}</table>") if rows else
           '<p class="empty">No custom endpoints yet.</p>')
        + custom_form
    )

    body = (
        f"<style>{_MEMBER_CSS}{_MODEL_CSS}</style>"
        # 厂商候选现在由每块自己的 <select> 带着——它要标出「哪一家是选中的」，
        # 共用一份就做不到。模型目录那边仍然各自一份 datalist（每家不一样）。
        + "<h2>Models and keys</h2>"
        + warn
        # 有 focus 就说明这句话已经渲在那一块里了。两处各印一遍同样的字，
        # 正是第一个 bug 的成因：提交前后画面看着一模一样。
        + (f'<p class="err">{escape(error)}</p>' if error and not focus else "")
        + (f'<p class="signed">{escape(notice)}</p>' if notice and not focus else "")
        + '<p class="note">Drafting and rewriting spend the organization\'s money. This page decides <strong>who gets paid</strong> '
        "(whose endpoint receives your control text) and <strong>how much at most</strong>.</p>"
        + "<h2>Which model each role uses</h2>" + "".join(role_rows)
        + "<h2>API key</h2>"
        + ('<table class="mtable"><tr><td><strong>Provider</strong></td>'
           "<td><strong>Key</strong></td><td><strong>Set by</strong></td>"
           "<td><strong>When</strong></td><td></td></tr>"
           f"{key_rows}</table>" if key_rows else
           '<p class="empty">No keys stored yet; drafting falls back to environment variables on the server.</p>')
        + key_form
        + custom_block
        + "<h2>Spending limits</h2>"
        + f'<p class="note">The organization has drafted <strong>{spent}</strong> controls this month, '
        f'limit {limits["draft_cap_month"]}. '
        "The accounting is in controls, not in currency: we keep no live price list per provider, and "
        "converting would only produce an illusion of precision.</p>"
        + limit_form
    )
    return page("Models and keys", body, nav=nav)


_MODEL_CSS = """
.mrow{background:var(--surface);border:1px solid var(--rule);padding:1.1rem 1.2rem;
  margin:0 0 .9rem}
.mrow h3{margin:0 0 .3rem;font-size:1rem;color:var(--ink);font-weight:600;
  font-family:var(--mono)}
.mrow .cur{font-size:.85rem;color:var(--muted);margin:.5rem 0 .9rem}
.mrow form{background:none;border:0;padding:0}
.linky{background:none;border:0;padding:0;color:var(--accent);
  font:inherit;font-size:.85rem;cursor:pointer;text-decoration:underline}
select.pick{width:100%;padding:.5rem .6rem;font:inherit;font-size:.9rem;
  background:var(--ground);color:var(--ink);border:1px solid var(--rule);
  border-radius:2px}
/* datalist 的输入框长得和普通文本框一模一样，没人知道它能点开。
   浏览器自带一个下拉按钮，只是默认只在悬停/聚焦时才显形——
   让它一直亮着就够了。自己再画一个箭头会和它撞在一起（实测两个叠着）。 */
input.pick[list]::-webkit-calendar-picker-indicator{opacity:.5;cursor:pointer}
input.pick[list]:hover::-webkit-calendar-picker-indicator,
input.pick[list]:focus::-webkit-calendar-picker-indicator{opacity:.9}
/* 「测一下」压在「保存」下面一档：两个挨着的实心按钮会让人分不清哪个是主动作。 */
.mrow button.ghost{background:none;color:var(--accent);
  border:1px solid var(--rule);margin-right:.5rem}
"""


# ---------- 配套文档 ----------

_DOC_CSS = """
.docrow{background:var(--surface);border:1px solid var(--rule);padding:1rem 1.1rem;
  margin:0 0 .7rem;display:flex;gap:1rem;align-items:baseline;flex-wrap:wrap}
.docrow h3{margin:0;font-size:.98rem;color:var(--ink);font-weight:600}
.docrow .meta{font-size:.8rem;color:var(--muted);font-variant-numeric:tabular-nums}
.docrow form{background:none;border:0;padding:0;margin-left:auto}
.seg{background:var(--surface);border:1px solid var(--rule);padding:.9rem 1.1rem;
  margin:0 0 .7rem}
.seg h4{margin:0 0 .4rem;font-family:var(--mono);font-size:.75rem;color:var(--accent)}
.seg p{margin:0;white-space:pre-wrap;font-size:.9rem}
"""


def documents(docs: list, *, can_write: bool, error: str = "", nav: str = "") -> str:
    rows = "".join(
        f'<div class="docrow"><h3>{escape(d.title)}</h3>'
        f'<span class="meta">{d.chars} chars · {d.chunks} chunks · '
        f'{escape(d.uploaded_by or "-")} · {escape(d.uploaded_at[:10])}</span>'
        f'<a class="topnav" href="/documents/{escape(d.id)}">View the chunks</a>'
        + (f'<form method="post" action="/documents/{escape(d.id)}/delete">'
           '<button class="chip" type="submit">Delete</button></form>'
           if can_write else "")
        + "</div>"
        for d in docs
    )
    form = ""
    if can_write:
        form = (
            '<h2>Upload a document</h2>'
            '<form method="post" action="/documents" enctype="multipart/form-data">'
            '<label for="file">File (.docx / .txt / .md)</label>'
            '<input type="file" id="file" name="file" required>'
            '<div style="height:.9rem"></div>'
            '<label for="title">Display name (optional, defaults to the filename)</label>'
            '<input type="text" id="title" name="title">'
            '<p style="margin:1.2rem 0 0"><button type="submit">Upload</button></p>'
            '<p class="hint">Only .docx / .txt / .md are accepted. For PDFs, export the text first: '
            "chunks cut from a PDF come out scrambled, and a model grounded on them will invent around the garbage.</p>"
            "</form>"
        )
    body = (
        f"<style>{_MEMBER_CSS}{_DOC_CSS}</style>"
        "<h2>Documents</h2>"
        + (f'<p class="err">{escape(error)}</p>' if error else "")
        + '<p class="note">What the drafter writes is <strong>generic</strong> guidance. '
        "How your organization actually operates is written in your own policies: whether logs are kept six months or a year is a line in your documents, "
        "not something the model can guess. Upload them, and when drafting your own frameworks the relevant chunks are sent to the model as well.</p>"
        '<div class="callout">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="1.8" stroke-linecap="round" aria-hidden="true">'
        '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>'
        '<p>Two things to weigh: this text <strong>is sent to the model provider you configured</strong>; '
        "and purchased standard texts (ISO, PCI and the like) are <strong>not accepted here</strong>. "
        "That is someone else\'s copyright, and on our servers it becomes our problem.</p></div>"
        + (rows if rows else '<p class="empty">No documents uploaded yet. '
           "Without them, drafts stay generic.</p>")
        + form
    )
    return page("Documents", body, nav=nav)


def document(doc, sections: list, nav: str = "") -> str:
    """把切出来的段原样显示。**「模型到底看到了什么」不能只有我们知道。**"""
    blocks = "".join(
        f'<div class="seg"><h4>{escape(heading or f"Chunk {i + 1}")}</h4>'
        f"<p>{escape(text)}</p></div>"
        for i, (heading, text) in enumerate(sections)
    )
    body = (
        f"<style>{_MEMBER_CSS}{_DOC_CSS}</style>"
        f"<h2>{escape(doc.title)}</h2>"
        f'<p class="note">{escape(doc.filename)} · {doc.chars} chars · '
        f"split into {len(sections)} chunks. The unit of retrieval is the chunk: at drafting time only the relevant chunks "
        "are sent out, not the whole document.</p>"
        '<p><a href="/documents">Back to Documents</a></p>'
        + blocks
    )
    return page(doc.title, body, nav=nav)


# 这两类是「已自动处理」的结果通知，不是失败——渲染时不给 ⚠。
_AUTO_DONE = {"catalog", "snapped"}


def import_preview(draft, bodies: list[str], nav: str = "") -> str:
    """确认前不写库。见 2026-08-25 AI 导入设计 §5.2

    **正文只读。** 它是从你的原文逐字截的——能在这儿改，就等于把「模型不许
    改写正文」那条保证从前门放进来，而且事后没人分得清哪些字是原文、
    哪些是当时顺手改的。

    切歪的主要形式是多切了一刀（一条被劈成两条），「↑合并」把两段行号
    接起来就按回去了，正文自动重算。少切一刀没法在这儿拆，但它罕见得多。
    """
    did = escape(draft.draft_id)
    rows = []
    for index, (span, body) in enumerate(zip(draft.spans, bodies)):
        key = str(index)
        # 空标题多半是切歪了，但也可能是真条款——不替人决定，只是不默认勾上。
        checked = "" if key in draft.dropped or not span.label else " checked"
        merge = ("" if index == 0 else
                 '<button class="linky" type="submit" name="merge" '
                 f'value="{index}">↑Merge</button>')
        rows.append(
            '<div class="prow">'
            f'<label class="pick"><input type="checkbox" name="keep" '
            f'value="{key}"{checked}> Import this control</label>'
            f'<input type="text" name="ref-{key}" value="{escape(span.ref)}" '
            'placeholder="ID" size="10">'
            f'<input type="text" name="label-{key}" value="{escape(span.label)}" '
            'placeholder="Title" size="28">'
            # 「谁写的要能看出来」——和条款页那套「AI 初稿」一个规矩。
            # 编号和标题可以由 AI 起（否则条款存不进库），但人要一眼看得出。
            + ('<span class="mark">Named by AI</span>'
               if "derived" in (span.ref_from, span.label_from) else "")
            + f'{merge}'
            + (f'<p class="pbody">{escape(body)}</p>'
               f'<p class="hint">Source lines {span.start}-{span.end}</p>'
               if span.end >= span.start else
               # 父条款截到第一个子条款之前就是空的——它本来只是个分组标题。
               # 显示一片空白，人会以为是 bug。
               '<p class="hint">This control has no body text of its own: '
               "it is a group heading, and the text lives in its child controls.</p>")
            + "</div>")
    warns = "".join(
        f'<p class="warn">⚠ {escape(p.detail)}</p>'
        for p in draft.problems if p.kind not in _AUTO_DONE)
    # catalog / snapped 是处理结果不是失败：编号冲突自动改了、行号整体
    # 偏移对齐了。混进 ⚠ 串里，「拆开了 91 条」会被读成出了 91 个错。
    auto = "".join(
        f'<p class="note">{escape(p.detail)}</p>'
        for p in draft.problems if p.kind in _AUTO_DONE)
    action = (
        '<p style="margin:0 0 1.2rem">'
        '<button type="submit" name="confirm" value="1">Import checked controls</button> '
        f'<a href="/import/{did}/discard" style="margin-left:.8rem">'
        "Discard this import</a></p>"
        if rows else "")
    body = (
        f"<style>{_IMPORT_CSS}</style>"
        f"<h2>{escape(draft.name)}</h2>"
        f'<p class="note">Cut into {len(draft.spans)} controls. '
        "<strong>Nothing is written until you confirm.</strong> The text is cut verbatim from your original "
        "and cannot be edited here; to change it, fix the source and upload again.</p>"
        + warns
        + auto
        + f'<form method="post" action="/import/{did}/confirm">'
        + (_with_csrf("", CHROME.get()[0]) if CHROME.get()[0] else "")
        + action
        + ("".join(rows) if rows
           else '<p class="empty">No controls were cut from this document. '
                "It may not be policy text (minutes, a bare spreadsheet), or its controls have no "
                "recognisable IDs and titles.</p>")
        + '<p style="margin:1.4rem 0 0">'
        + ('<button type="submit" name="confirm" value="1">Import checked controls</button> '
           if rows else "")
        + f'<a href="/import/{did}/discard" style="margin-left:.8rem">'
        "Discard this import</a></p></form>"
    )
    return page(f"{draft.name} · Confirm import", body, crumb="Import", nav=nav)


_IMPORT_CSS = """
.prow{background:var(--surface);border:1px solid var(--rule);
  padding:.9rem 1rem;margin:0 0 .7rem}
.prow input[type=text]{width:auto;display:inline-block;margin-right:.5rem}
.prow .pick{display:inline-block;margin-right:.8rem;font-size:.85rem}
.pbody{white-space:pre-wrap;margin:.6rem 0 .2rem;color:var(--body)}
.warn{color:var(--ask);font-size:.9rem;margin:.3rem 0}
"""


def import_progress(job, nav: str = "") -> str:
    """切分进度。见 2026-08-25 AI 导入设计 §5.3

    跑着的时候这一页每 3 秒自己刷新——零 JS，和起草那边一个做法。
    否则人只能盯着一个不动的页面猜是在跑还是挂了。
    """
    head = f"<h2>{escape(job.framework_id)} · Splitting</h2>"
    back = '<p style="margin:1.4rem 0 0"><a href="/import">Back to the import page</a></p>'

    if job.status == "error":
        return page("Splitting did not finish", (
            head + f'<p class="err">Splitting did not finish: {escape(job.error)}</p>'
            + '<p class="note">Calls already made have been billed, but no draft was produced. '
            'The most common cause is a changed model endpoint or key. Go to "Settings → Models and keys" and click "Test" to find out.</p>' + back
        ), crumb="Import", nav=nav)

    percent = int(job.done / job.total * 100) if job.total else 0
    scale = (f"This document is split into {job.total} chunks, one model call each." if job.total > 1
             else "This document splits in a single call.")
    return page("Splitting", (
        '<meta http-equiv="refresh" content="3">'
        + f"<style>{_PROGRESS_CSS}</style>"
        + head
        + f'<p class="note">{scale} Keep this page open: '
        "it refreshes itself every 3 seconds and jumps to the confirmation page when done.</p>"
        + f'<div class="bar"><div class="fill" style="width:{percent}%"></div></div>'
        # `done` 数的是**已完成**的块。写「第 0 块」读着像还没开始，
        # 而这会儿它已经在跑第一块了。
        + f'<p class="hint">Finished {job.done} / {job.total} chunks</p>'
        + back
    ), crumb="Import", nav=nav)


_PROGRESS_CSS = """
.bar{background:var(--sunk);border:1px solid var(--rule);height:1.1rem;
  margin:1.2rem 0 .4rem;overflow:hidden}
.fill{background:var(--accent);height:100%;transition:width .3s}
"""



def framework_delete(found, cost: dict, error: str = "", nav: str = "") -> str:
    """删框架的确认页。**要输一遍编号。**

    它会连着毁掉这个框架下所有的自评和签字——那可能是几十小时的工作，
    而删掉的东西找不回来。所以这一步是故意麻烦的。
    """
    fid = escape(found.id)
    body = (
        f'<h2>Delete "{escape(found.name)}"?</h2>'
        + (f'<p class="err">{escape(error)}</p>' if error else "")
        + '<p class="note">This also deletes:</p><ul>'
        + f'<li><strong>{cost["controls"]}</strong> controls</li>'
        + f'<li><strong>{cost["assessments"]}</strong> self-assessments '
        "(the level you recorded control by control)</li>"
        + f'<li><strong>{cost["interpretations"]}</strong> interpretations</li>'
        + f'<li><strong>{cost["confirmations"]}</strong> sign-off records</li>'
        + "</ul>"
        + '<p class="err">What is deleted cannot be recovered. '
        "Re-importing the same file will not bring the self-assessments back: those answers are stored by control ID, "
        "and this step clears them too. Keeping them would be worse: same IDs, answers from the previous file.</p>"
        + f'<form method="post" action="/f/{fid}/delete">'
        + f'<label for="confirm">Type the ID exactly: <code>{fid}</code></label>'
        + '<input type="text" id="confirm" name="confirm" autocomplete="off">'
        + '<p style="margin:1.2rem 0 0"><button type="submit">Delete it</button> '
        + f'<a href="/mine" style="margin-left:.8rem">Keep it</a></p></form>'
    )
    return page(f"Delete {found.id}", body, crumb="My imports", nav=nav)


def _selection_popup(control_id: str) -> str:
    """选中一段话，就地弹一个小聊天框。

    **这一页原本是零 JS 的**（下拉用 datalist、进度条用 meta refresh、
    滚动定位用 autofocus）。选中文字绕不开 `window.getSelection()`，
    所以这里破了那条约束——但界限划得很死：

    **JS 只负责「问」和「显示」，每一次写库仍然走普通表单 POST。**
    写库那条路上挂着预检、审计、和「点头才写」那道闸；让 JS 去写库，
    等于把这三样搬进浏览器。所以浮窗里那个「确定，改」是个真表单，
    点了整页刷新。

    **默认允许，只挡禁区。** 早先的规则是「选区必须整个落在 `.chatty` 里」——
    那是默认拒绝：从标题拖到正文、跨两个字段、跨段落，任何一种都不弹，
    而人本来就是那么选的。

    这一页上真正不能发出去的只有 `.noai`（官方映射那一块，内容来自内置
    内容包，是别的框架的受版权条款标题）。其余全是这家公司自己的东西。
    所以规则改成：**选区碰到 `.noai` 就不弹，别的都弹。**
    """
    return (
        '<div id="pop" hidden><span class="q"></span>'
        '<textarea rows="2" placeholder="Ask about this selection"></textarea>'
        '<p class="row"><button type="button" class="go">Ask AI</button>'
        '<button type="button" class="x">Close</button></p>'
        '<div class="ans"></div></div>'
        f"<style>{_POPUP_CSS}</style>"
        f"<script>{_POPUP_JS.replace('__CID__', control_id)}</script>"
    )


_POPUP_CSS = """
#pop{position:absolute;z-index:20;width:22rem;background:var(--surface);
  border:1px solid var(--accent);box-shadow:0 6px 24px rgba(0,0,0,.18);
  padding:.8rem .9rem;font-size:.9rem}
#pop .q{display:block;font-size:.8rem;color:var(--muted);
  border-left:2px solid var(--rule);padding-left:.5rem;margin-bottom:.5rem;
  max-height:4.5rem;overflow:auto}
#pop textarea{width:100%;font:inherit;font-size:.9rem}
#pop .row{margin:.5rem 0 0;display:flex;gap:.5rem}
#pop .ans{margin-top:.6rem;white-space:pre-wrap}
#pop .ans:empty{margin:0}
"""

# 原生 DOM。不引框架、不加构建步骤、不联网拿脚本——
# 「任何页面都不许引用外部主机」那条守卫还在，这段也不该破它。
_POPUP_JS = """
(function () {
  var pop = document.getElementById('pop');
  var quote = '', busy = false;
  var q = pop.querySelector('.q'), box = pop.querySelector('textarea');
  var ans = pop.querySelector('.ans');
  var cid = encodeURIComponent('__CID__');

  function hide() { pop.hidden = true; ans.textContent = ''; box.value = ''; }
  pop.querySelector('.x').onclick = hide;

  document.addEventListener('mouseup', function (e) {
    if (pop.contains(e.target)) return;
    var sel = window.getSelection();
    var text = sel ? String(sel).trim() : '';
    if (!text) { hide(); return; }
    // 默认允许，只挡禁区：选区碰到 .noai 就不弹。那一块的内容来自内置
    // 内容包（别的框架的受版权条款标题），发出去就碰到了那条红线。
    var range = sel.getRangeAt(0);
    var blocked = false;
    var zones = document.querySelectorAll('.noai');
    for (var i = 0; i < zones.length; i++) {
      if (range.intersectsNode(zones[i])) { blocked = true; break; }
    }
    if (blocked) { hide(); return; }
    quote = text.slice(0, 600);
    q.textContent = '"' + quote.slice(0, 120) +
      (quote.length > 120 ? '\u2026' : '') + '"';
    var r = sel.getRangeAt(0).getBoundingClientRect();
    pop.style.top = (r.bottom + window.scrollY + 8) + 'px';
    pop.style.left = Math.max(8, Math.min(
      r.left + window.scrollX, window.innerWidth - 380)) + 'px';
    pop.hidden = false;
    ans.textContent = '';
    box.focus();
  });

  pop.querySelector('.go').onclick = function () {
    if (busy || !box.value.trim()) return;
    busy = true;
    ans.textContent = 'Asking\u2026';
    var body = new URLSearchParams();
    body.append('message', box.value);
    body.append('quote', quote);
    fetch('/c/' + cid + '/chat.json', { method: 'POST', body: body })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        busy = false;
        ans.textContent = d.reply || '(no reply)';
        if (d.turn_id && d.fields && d.fields.length) {
          // 写库仍然走表单。JS 只负责问和显示。
          var f = document.createElement('form');
          f.method = 'post';
          f.action = '/c/' + cid + '/chat/' + d.turn_id + '/apply';
          var p = document.createElement('p');
          p.className = 'hint';
          p.appendChild(document.createTextNode(
            'It wants to change: ' + d.fields.join(', ')));
          var b = document.createElement('button');
          b.type = 'submit';
          b.textContent = 'Apply';
          p.appendChild(b);
          f.appendChild(p);
          ans.appendChild(f);
        }
      })
      .catch(function () {
        busy = false;
        ans.textContent = 'The question did not go through. Try again.';
      });
  };
})();
"""


# 两栏与右栏常驻。**纯 CSS，不加 JS**——这一页的 JS 只为选区那件事破例过一次，
# 布局用 sticky 就够，不该再欠一笔。
_SPLIT_CSS = """
/* 全站容器是 56rem——单栏长文那是合适的宽度。两栏挤在里面，左边就剩
   三十几 rem，正文被压成窄条而按钮占了半个屏幕。
   
   放宽到刚好够：正文行宽本来就被 .doc p{max-width:62ch} 限着（那是条
   好规矩，长行难读），再宽出去的部分全是死空白。64rem 让左栏正好
   容下 62ch，右栏 19rem，中间不留空档。 */
.wrap.wide{max-width:64rem}
/* 不写 align-items:start——那会让右栏只有内容那么高，而 sticky 只能在
   父元素的高度内粘住：滚过那一段它就跟着走了。默认的 stretch 让右栏
   和左栏一样高，右栏才能一路粘到底。 */
.split{display:grid;grid-template-columns:minmax(0,1fr) 19rem;gap:2.5rem}
.split .reading{min-width:0}
.doing .stuck{position:sticky;top:0;height:100vh;box-sizing:border-box;
  display:flex;flex-direction:column;justify-content:center;
  gap:.9rem;padding:1rem 0}
/* 对话历史会越聊越长。面板内部滚，输入框始终贴底、始终看得见。 */
.doing .chat{display:flex;flex-direction:column;min-height:0}
.doing .chat .thread{overflow-y:auto;min-height:0;flex:1}
.doing .claim,.doing .chat{margin:0}
.doing .claim{padding:.9rem 1rem}
/* 窄屏（笔记本分屏、平板）两栏硬挤在一起，两边都没法看。叠回去。 */
@media (max-width:60rem){
  .split{grid-template-columns:minmax(0,1fr)}
  .doing .stuck{position:static;height:auto;display:block}
  .doing .stuck > *{margin-bottom:.9rem}
}
"""
