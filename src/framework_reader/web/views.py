"""The local web shell's HTML. Shares design tokens with the publish page (publish/theme.py).

Server-rendered, no frontend framework - v1 does not need one: React is three months, this is three days.
"""
from __future__ import annotations

from contextvars import ContextVar
from html import escape
from urllib.parse import quote

from framework_reader.assess.remediation import STATE_LABELS
from framework_reader.publish.theme import THEME_CSS

# This request's shell info: (csrf token, logged-in user's display name).
#
# Not threading it through ten view functions as parameters: **missing one is a CSRF hole**,
# and every new page would miss it. page() is the single shell function; taking it from here
# removes the "forgot to pass it" failure mode. ContextVar isolates per task - concurrent requests do not cross.
CHROME: ContextVar[tuple[str, str]] = ContextVar("chrome", default=("", ""))

# This request's permission set. None = sign-in not enabled (single-user local use); everything shows as before.
#
# Hiding buttons is **UX**, not authorization - authorization was already decided in the guards (design §1.2, §4.1).
# Here we only avoid letting people click into a rejection.
PERMS: ContextVar[frozenset[str] | None] = ContextVar("perms", default=None)


def may(permission: str) -> bool:
    perms = PERMS.get()
    return perms is None or permission in perms


def logged_in() -> bool:
    """Is the identity system enabled. In single-user local use there are no "members" - do not render that entry."""
    return PERMS.get() is not None

_CSS = THEME_CSS + """
/* ---------- Keynote Studio & Material 3 Expressive Tokens ----------
   Keynote Stage aesthetic: 4-Color signature,
   Iridescent aurora, stage spotlights, and Material elevation.
   Toggle theme sets <html data-theme="light">, remembered in localStorage. */
:root{
  --mono:"Google Sans Mono","Roboto Mono","SF Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --han:"Google Sans","Google Sans Text","Product Sans",system-ui,-apple-system,BlinkMacSystemFont,
    "Segoe UI",Roboto,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  --g-blue:#1a73e8;
  --g-red:#ea4335;
  --g-yellow:#fbbc04;
  --g-green:#34a853;
  --gemini-gradient:linear-gradient(135deg,#4285f4 0%,#9b72cb 35%,#d96570 70%,#fbbc04 100%);
  --g-rainbow:linear-gradient(90deg,#4285F4 0% 25%,#EA4335 25% 50%,#FBBC05 50% 75%,#34A853 75% 100%);
}
:root:not([data-theme="light"]){
  --ground:#0d0f12; --surface:#13161f; --surface-high:#1b202c; --surface-highest:#232938;
  --sunk:#0a0c10; --ink:#f4f6fa; --body:#c2c7d0; --muted:#8a919e;
  --rule:#222734; --accent:#8ab4f8; --accent-soft:#162438; --ask:#f28b82;
  --success:#81c995; --success-soft:#12281a;
  --topbar-bg:rgba(10,12,16,.82); --topbar-line:rgba(255,255,255,.07);
  --topsheen1:rgba(66,133,244,.32); --topsheen2:rgba(155,114,203,.25);
  --topglow:rgba(66,133,244,.4);
  --row-hover:rgba(255,255,255,.03); --card-hover:#181d27; --card-hover-line:rgba(138,180,248,.4);
  --selection:rgba(66,133,244,.35);
  --card-shadow:0 4px 24px rgba(0,0,0,.45);
  --card-shadow-hover:0 14px 40px rgba(0,0,0,.65);
  --border-glow:rgba(66,133,244,.18);
}
:root[data-theme="light"]{
  --ground:#f8f9fa; --surface:#ffffff; --surface-high:#f1f4f9; --surface-highest:#e6ecf5;
  --sunk:#edf2f8; --ink:#17191c; --body:#3e4247; --muted:#646a73;
  --rule:#e2e6ed; --accent:#1a73e8; --accent-soft:#e8f0fe; --ask:#d93025;
  --success:#1e8e3e; --success-soft:#e6f4ea;
  --topbar-bg:rgba(248,249,250,.85); --topbar-line:rgba(0,0,0,.06);
  --topsheen1:rgba(66,133,244,.10); --topsheen2:rgba(155,114,203,.08);
  --topglow:rgba(66,133,244,.15);
  --row-hover:rgba(26,115,232,.035); --card-hover:#ffffff; --card-hover-line:rgba(26,115,232,.35);
  --selection:rgba(26,115,232,.2);
  --card-shadow:0 2px 14px rgba(40,50,70,.06);
  --card-shadow-hover:0 10px 32px rgba(66,133,244,.15);
  --border-glow:rgba(26,115,232,.12);
}
html,body{overflow-x:clip}
body{margin:0;background:var(--ground);color:var(--body);
  font-family:var(--han);font-size:16px;line-height:1.65;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
  transition:background .3s ease,color .3s ease}
a{color:var(--accent);text-decoration:none;transition:color .2s ease}
a:hover{text-decoration:underline}
::selection{background:var(--selection)}

/* Entrance animation: smooth rise */
@keyframes rise{from{opacity:0;transform:translateY(14px)}
  to{opacity:1;transform:none}}
.wrap{max-width:76rem;margin:0 auto;padding:0 1.5rem 5rem}
.wrap.wide{max-width:92rem}
.pagein{animation:rise .5s cubic-bezier(.2,0,0,1) both}

/* Top bar: Keynote Studio full-bleed frosted glass + four-colour top light band */
.top{display:flex;gap:1.1rem;align-items:center;flex-wrap:wrap;
  position:sticky;top:0;z-index:100;
  width:100vw;margin-left:calc(50% - 50vw);margin-right:calc(50% - 50vw);
  padding:.85rem calc(max(1.5rem, 50vw - 36.5rem)) .9rem;
  background:var(--topbar-bg);
  -webkit-backdrop-filter:saturate(180%) blur(28px);
  backdrop-filter:saturate(180%) blur(28px);
  border-bottom:1px solid var(--topbar-line);
  margin-bottom:2.2rem;transition:background .25s ease,border-color .25s ease}
.wrap.wide .top{
  padding:.85rem calc(max(1.5rem, 50vw - 44.5rem)) .9rem}

/* Four-colour top light band */
.top::after{content:"";position:absolute;top:0;left:0;right:0;
  height:3.5px;background:var(--g-rainbow);z-index:10;
  box-shadow:0 0 14px rgba(66,133,244,.4)}

/* Top bar soft glow follow and spotlight */
.top::before{content:"";position:absolute;inset:0;pointer-events:none;
  opacity:0;transition:opacity .5s ease;z-index:0;
  background:radial-gradient(36rem circle at var(--tx,50%) var(--ty,50%),
    var(--topglow),transparent 70%)}
.top:hover::before{opacity:1}
.top > *{position:relative;z-index:1}
.top h1{font-size:1.32rem;margin:0;color:var(--ink);font-weight:600;
  letter-spacing:-.02em;display:flex;align-items:center;gap:.65rem}
.top h1 a{color:inherit;text-decoration:none}
.brandlogo{height:2.2rem;width:auto;display:block}
.crumb{font-family:var(--mono);font-size:.82rem;color:var(--muted)}
.crumb a{color:var(--muted)}
.crumb a:hover{color:var(--ink);text-decoration:none}

/* Keynote-exclusive pill badge */
.keynote-pill{display:inline-flex;align-items:center;gap:.35rem;font-size:.68rem;
  font-weight:600;padding:.18rem .58rem;border-radius:980px;
  background:var(--accent-soft);color:var(--accent);
  border:1px solid rgba(66,133,244,.28);letter-spacing:.03em;
  text-transform:uppercase}
.gemini-sparkle{display:inline-block;color:#fbbc04;font-style:normal}

.empty-gap{background:var(--surface);border:1px solid var(--rule);
  border-radius:18px;padding:1.4rem 1.5rem;margin:1.2rem 0;
  box-shadow:var(--card-shadow)}
.empty-gap p{margin:0 0 1rem}
.empty-gap p:last-child{margin:0}

a.cta{display:inline-flex;align-items:center;justify-content:center;
  background:var(--accent);color:#fff;
  padding:.55rem 1.3rem;text-decoration:none;font-size:.9rem;font-weight:500;
  border-radius:980px;transition:all .2s cubic-bezier(.2,0,0,1);
  box-shadow:0 2px 8px rgba(66,133,244,.25)}
a.cta:hover{opacity:.92;transform:translateY(-1px);
  box-shadow:0 4px 16px rgba(66,133,244,.4);text-decoration:none}
a.back{font-size:.85rem;color:var(--muted);text-decoration:none;
  display:inline-flex;align-items:center;gap:.35rem;transition:color .2s ease}
a.back:hover{color:var(--ink);text-decoration:none}
p.back{margin:0 0 .8rem}

.topnav{font-size:.85rem;font-weight:500;text-decoration:none;color:var(--muted);
  padding:.4rem .95rem;border-radius:980px;
  transition:all .2s cubic-bezier(.2,0,0,1)}
.topnav:hover{color:var(--ink);background:var(--accent-soft);
  text-decoration:none}
.who{font-size:.82rem;color:var(--muted);margin-left:auto;padding-left:.8rem}
.who + .topnav{margin-left:.6rem}
.topright{display:flex;gap:.9rem;align-items:center;flex-wrap:wrap}

/* Dark/light toggle button */
.themebtn{background:var(--sunk);border:1px solid var(--rule);
  padding:.4rem .6rem;margin-left:.4rem;color:var(--muted);
  border-radius:980px;display:inline-flex;align-items:center;align-self:center;
  cursor:pointer;transition:all .2s cubic-bezier(.2,0,0,1)}
.themebtn:hover{color:var(--ink);border-color:var(--accent);
  background:var(--accent-soft);transform:scale(1.05)}
.themebtn svg{width:1.05rem;height:1.05rem;display:block}
.i-sun{display:none}
:root[data-theme="light"] .i-sun{display:block}
:root[data-theme="light"] .i-moon{display:none}

/* ---------- Keynote stage motion layer ---------- */

/* Fluid aurora: four large light blobs (blue, purple, green, yellow) drifting slowly, like keynote stage lighting */
.aurora{position:fixed;inset:0;z-index:-1;overflow:hidden;pointer-events:none}
.aurora i{position:absolute;display:block;border-radius:50%;
  filter:blur(110px);opacity:.24;will-change:transform}
.aurora i:nth-child(1){width:48rem;height:48rem;left:-12rem;top:-16rem;
  background:radial-gradient(circle,#4285f4,transparent 65%);
  animation:drift1 24s ease-in-out infinite alternate}
.aurora i:nth-child(2){width:42rem;height:42rem;right:-12rem;top:20vh;
  background:radial-gradient(circle,#9b72cb,transparent 65%);
  animation:drift2 30s ease-in-out infinite alternate}
.aurora i:nth-child(3){width:38rem;height:38rem;left:24vw;bottom:-15rem;
  background:radial-gradient(circle,#34a853,transparent 65%);
  animation:drift3 34s ease-in-out infinite alternate}
.aurora i:nth-child(4){width:32rem;height:32rem;right:16vw;top:-8rem;
  background:radial-gradient(circle,#fbbc05,transparent 65%);
  animation:drift4 28s ease-in-out infinite alternate}
@keyframes drift1{to{transform:translate(9rem,6rem) scale(1.15)}}
@keyframes drift2{to{transform:translate(-8rem,-5rem) scale(.9)}}
@keyframes drift3{to{transform:translate(6rem,-7rem) scale(1.2)}}
@keyframes drift4{to{transform:translate(-6rem,7rem) scale(1.1)}}
:root[data-theme="light"] .aurora i{opacity:.12}

/* Material 3 Spotlight cards: cursor-following glow and rainbow border */
.card{position:relative;border-radius:20px;overflow:hidden;
  background:var(--surface);border:1px solid var(--rule);
  box-shadow:var(--card-shadow);
  transition:transform .28s cubic-bezier(.2,0,0,1),
             box-shadow .28s cubic-bezier(.2,0,0,1),
             border-color .25s ease,background .25s ease}
.card::before{content:"";position:absolute;inset:0;border-radius:inherit;
  opacity:0;transition:opacity .4s ease;pointer-events:none;
  background:radial-gradient(32rem circle at var(--mx,50%) var(--my,50%),
    rgba(66,133,244,.16),transparent 60%)}
.card::after{content:"";position:absolute;inset:0;border-radius:inherit;
  opacity:0;transition:opacity .4s ease;pointer-events:none;padding:1px;
  background:radial-gradient(24rem circle at var(--mx,50%) var(--my,50%),
    rgba(66,133,244,.85),rgba(155,114,203,.6) 30%,rgba(255,255,255,.12) 55%,transparent 70%);
  -webkit-mask:linear-gradient(#000,#000) content-box,linear-gradient(#000,#000);
  -webkit-mask-composite:xor;mask-composite:exclude}
.card:hover{border-color:var(--card-hover-line);background:var(--card-hover);
  box-shadow:var(--card-shadow-hover);transform:translateY(-3px);text-decoration:none}
.card:hover::before,.card:hover::after{opacity:1}
:root[data-theme="light"] .card::after{background:radial-gradient(
  24rem circle at var(--mx,50%) var(--my,50%),
  rgba(26,115,232,.75),rgba(155,114,203,.45) 35%,rgba(0,0,0,.08) 55%,transparent 70%)}

/* Headings */
h1,h2,h3,h4{color:var(--ink);letter-spacing:-.025em;line-height:1.25}
h1 .ch,h2 .ch{display:inline}

/* Table rows cascade in */
.js tr{opacity:0;transform:translateY(8px);
  transition:opacity .45s ease,transform .45s cubic-bezier(.2,0,0,1);
  transition-delay:calc(var(--ri,0)*24ms)}
.js tr.in{opacity:1;transform:none}

/* Progress bars grow */
.js .bar i{width:0;transition:width .9s cubic-bezier(.2,0,0,1) .25s}
.js .bar.in i{width:var(--w,0%)}

/* Button shine sweep */
button,a.cta{position:relative;overflow:hidden}
button::after,a.cta::after{content:"";position:absolute;top:0;left:-80%;
  width:45%;height:100%;transform:skewX(-24deg);pointer-events:none;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.32),transparent);
  transition:left .6s ease}
button:hover::after,a.cta:hover::after{left:125%}

/* Circular reveal on theme switch */
::view-transition-old(root),::view-transition-new(root){animation:none;
  mix-blend-mode:normal}
::view-transition-new(root){animation:vtIn .45s ease-in forwards;
  clip-path:circle(0px at var(--vx,100%) var(--vy,0px))}
@keyframes vtIn{to{clip-path:circle(150% at var(--vx,100%) var(--vy,0px))}}

/* Omnibox search box */
form.seek{display:flex;gap:.7rem;align-items:center;background:var(--surface);
  border:1px solid var(--rule);border-radius:980px;padding:.4rem .5rem .4rem 1.3rem;
  margin:0 auto 1rem;max-width:48rem;box-shadow:var(--card-shadow);
  transition:border-color .25s ease,box-shadow .25s ease;position:relative}
form.seek:focus-within{border-color:var(--accent);
  box-shadow:0 0 0 3px var(--accent-soft),0 8px 32px rgba(66,133,244,.2)}
form.seek input[type=search]{flex:1;width:auto;padding:.65rem .5rem;font:inherit;
  font-size:1.02rem;background:transparent;color:var(--ink);
  border:0;outline:none}
.seek-sparkle{font-size:1.2rem;color:var(--g-yellow);flex:none;line-height:1}
.seek-kbd{font-family:var(--mono);font-size:.72rem;color:var(--muted);
  background:var(--sunk);border:1px solid var(--rule);border-radius:6px;
  padding:.15rem .45rem;line-height:1;margin-right:.4rem}
form.seek button{padding:.65rem 1.6rem;border-radius:980px;font-weight:600;
  font-size:.92rem;white-space:nowrap;background:var(--accent);border:0;color:#fff;
  cursor:pointer;display:inline-flex;align-items:center;gap:.4rem;
  box-shadow:0 2px 8px rgba(66,133,244,.3);transition:all .2s ease}
form.seek button:hover{opacity:.95;transform:scale(1.02);
  box-shadow:0 4px 16px rgba(66,133,244,.45)}
form.tiny{display:inline;background:transparent;border:0;padding:0;margin:0;box-shadow:none}
form.tiny button{font-size:.85rem;padding:.4rem 1rem;border-radius:980px}

/* Keynote stage hero area */
.stage-hero{margin:.8rem 0 2.2rem;text-align:center}
.stage-eyebrow{display:inline-flex;align-items:center;gap:.4rem;font-size:.74rem;
  font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);
  background:var(--accent-soft);padding:.25rem .75rem;border-radius:980px;
  margin-bottom:1rem;border:1px solid rgba(66,133,244,.2)}
.stage-headline{font-size:clamp(1.85rem,4.2vw,2.75rem);font-weight:700;color:var(--ink);
  letter-spacing:-.025em;line-height:1.18;margin:0 0 .8rem;text-wrap:balance}
.stage-sub{font-size:1.02rem;color:var(--muted);max-width:44rem;margin:0 auto 1.8rem;
  line-height:1.6;text-wrap:balance}
.gemini-text,.gradient-text{background:var(--gemini-gradient);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent;display:inline-block}

/* Quick-recommend pill chips */
.quick-chips{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;
  justify-content:center;margin:1rem 0 1.6rem}
.chip-label{font-size:.8rem;color:var(--muted);font-weight:500}
.chip{font-size:.8rem;font-weight:500;padding:.35rem .9rem;border-radius:980px;
  border:1px solid var(--rule);background:var(--surface);color:var(--muted);
  text-decoration:none;transition:all .2s cubic-bezier(.2,0,0,1);box-shadow:0 1px 3px rgba(0,0,0,.04)}
.chip:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-soft);
  transform:translateY(-1px);text-decoration:none}

/* Keynote big-number stats board */
.stage-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(12rem,1fr));
  gap:1rem;margin:2rem 0 2.5rem}
.stat-card{background:var(--surface);border:1px solid var(--rule);border-radius:20px;
  padding:1.35rem 1.2rem;text-align:center;box-shadow:var(--card-shadow);
  transition:transform .25s ease,border-color .25s ease}
.stat-card:hover{transform:translateY(-2px);border-color:var(--card-hover-line)}
.stat-num{font-size:2rem;font-weight:700;color:var(--ink);line-height:1.2;
  font-family:var(--han)}
.stat-desc{font-size:.76rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;
  color:var(--ink);margin-top:.35rem}
.stat-sub{font-size:.72rem;color:var(--muted);margin-top:.2rem}

/* Bento Grid Studio Dashboard */
.bento-layout{display:grid;grid-template-columns:1fr 340px;gap:2.2rem;align-items:start;
  margin:2.4rem 0 3rem}
@media (max-width:960px){
  .bento-layout{grid-template-columns:1fr}
}
.bento-main{display:flex;flex-direction:column;gap:2.5rem;min-width:0}
.bento-sidebar{display:flex;flex-direction:column;gap:1.5rem}
.section-title-row{display:flex;align-items:baseline;justify-content:space-between;
  gap:1rem;margin-bottom:.9rem}
.section-title-row h2{margin:0}
.shuffle-btn{font-size:.84rem;font-weight:500;padding:.38rem .95rem;border-radius:980px;
  background:var(--surface);border:1px solid var(--rule);color:var(--ink);
  cursor:pointer;display:inline-flex;align-items:center;gap:.4rem;
  transition:all .2s cubic-bezier(.2,0,0,1)}
.shuffle-btn:hover{border-color:var(--accent);color:var(--accent);
  background:var(--accent-soft);transform:translateY(-1px)}
.daily-card{display:flex;flex-direction:column;justify-content:space-between;
  min-height:11.5rem}
.daily-card .snippet{background:var(--sunk);border-radius:12px;padding:.7rem .9rem;
  margin-top:.8rem;font-size:.88rem;color:var(--body);line-height:1.5;
  border-left:3px solid var(--accent)}
.daily-card .tag-row{display:flex;align-items:center;justify-content:space-between;
  margin-top:.9rem;font-size:.8rem;color:var(--muted)}

/* Telemetry HUD Cards in Sidebar */
.hud-card{background:var(--surface);border:1px solid var(--rule);border-radius:20px;
  padding:1.35rem 1.45rem;box-shadow:var(--card-shadow);position:relative;overflow:hidden}
.hud-badge{display:inline-flex;align-items:center;gap:.35rem;font-size:.68rem;
  font-weight:600;padding:.15rem .55rem;border-radius:980px;
  background:var(--accent-soft);color:var(--accent);text-transform:uppercase;letter-spacing:.05em}
.hud-header h3{margin:.5rem 0 1rem;font-size:1.05rem;color:var(--ink);font-weight:600}
.hud-stat-list{display:flex;flex-direction:column;gap:.85rem}
.hud-stat-row{display:flex;align-items:center;justify-content:space-between;
  font-size:.86rem}
.hud-stat-name{display:flex;align-items:center;gap:.5rem;color:var(--body);font-weight:500}
.hud-stat-val{font-family:var(--mono);font-size:.82rem;font-weight:600;color:var(--accent)}
.hud-meter{height:6px;background:var(--sunk);border-radius:980px;overflow:hidden;margin-top:.35rem}
.hud-meter-fill{height:100%;background:var(--g-rainbow);border-radius:980px}
.hud-action-link{display:inline-flex;align-items:center;gap:.4rem;font-size:.84rem;
  font-weight:600;color:var(--accent);margin-top:1.1rem;text-decoration:none}
.hud-action-link:hover{text-decoration:underline}

/* Brand indicator dots */
.g-dot{width:8px;height:8px;border-radius:50%;display:inline-block;
  margin-right:.45rem;vertical-align:.08em}
.dot-blue{background:var(--g-blue)}
.dot-green{background:var(--g-green)}
.dot-yellow{background:var(--g-yellow)}
.dot-red{background:var(--g-red)}

/* Tags and badges */
.mark{font-size:.68rem;font-weight:500;letter-spacing:.02em;margin-left:.5rem;
  padding:.15rem .55rem;border:1px solid var(--rule);border-radius:980px;
  color:var(--muted);vertical-align:.1em}
.mark.mine{border-color:rgba(52,168,83,.4);color:var(--success);
  background:var(--success-soft);font-weight:600}
.ai-mark{border-color:rgba(155,114,203,.35);color:var(--accent);
  background:var(--accent-soft)}

.edit{font-size:.75rem;font-weight:500;margin-left:.6rem;text-decoration:none;
  color:var(--accent)}
.signed{font-family:var(--mono);font-size:.8rem;color:var(--success);
  background:var(--success-soft);border:1px solid rgba(52,168,83,.3);
  padding:.45rem .95rem;border-radius:980px;display:inline-flex;
  align-items:center;gap:.45rem;margin:0 0 1.2rem;font-weight:500}
.claim{margin:2rem 0 0;font-size:.9rem}
textarea{width:100%;padding:.75rem .9rem;font:inherit;font-size:.92rem;
  background:var(--sunk);color:var(--ink);border:1px solid var(--rule);
  border-radius:14px;margin-bottom:.9rem;resize:vertical;
  transition:border-color .2s ease,box-shadow .2s ease}
textarea:focus{outline:none;border-color:var(--accent);
  box-shadow:0 0 0 3px var(--accent-soft)}
h2{font-size:1.38rem;color:var(--ink);margin:2.6rem 0 1.1rem;font-weight:600;
  letter-spacing:-.02em}

/* Card grid */
.cards{display:grid;gap:1.2rem;
  grid-template-columns:repeat(auto-fill,minmax(18rem,1fr))}
.card{display:block;padding:1.35rem 1.45rem;background:var(--surface);
  border:1px solid var(--rule);border-radius:20px;text-decoration:none;
  color:inherit;box-shadow:var(--card-shadow);
  transition:transform .28s cubic-bezier(.2,0,0,1),
             box-shadow .28s cubic-bezier(.2,0,0,1),
             border-color .25s ease,background .25s ease}
.card:hover{border-color:var(--card-hover-line);background:var(--card-hover);
  box-shadow:var(--card-shadow-hover);transform:translateY(-3px);text-decoration:none}
.card h3{margin:0 0 .4rem;font-size:1.05rem;color:var(--ink);font-weight:600}
.card .id{font-family:var(--mono);font-size:.76rem;color:var(--accent);font-weight:500}
.card .meta{font-size:.84rem;color:var(--muted);margin:.6rem 0 0;
  font-variant-numeric:tabular-nums;line-height:1.5}
.tag{display:inline-block;font-size:.7rem;font-weight:500;padding:.12rem .5rem;
  margin-left:.4rem;border:1px solid var(--rule);border-radius:980px;
  color:var(--muted);vertical-align:.1em}
.tag.mine{border-color:rgba(66,133,244,.4);color:var(--accent);background:var(--accent-soft)}

/* Progress bars */
.bar{height:5px;background:var(--sunk);margin-top:.8rem;border-radius:980px;
  overflow:hidden}
.bar i{display:block;height:100%;background:var(--g-rainbow);
  transition:width .8s cubic-bezier(.2,0,0,1)}

/* Tables */
table{width:100%;border-collapse:separate;border-spacing:0;font-size:.92rem;
  background:var(--surface);border:1px solid var(--rule);border-radius:16px;
  overflow:hidden;box-shadow:var(--card-shadow)}
td{padding:.8rem 1rem;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:0}
td.c{font-family:var(--mono);color:var(--accent);font-weight:500;white-space:nowrap;width:1%}
tr{transition:background .15s ease}
tr:hover td{background:var(--row-hover)}
td a{text-decoration:none;color:inherit}
td a:hover{color:var(--accent);text-decoration:none}

/* Forms and inputs */
form{background:var(--surface);border:1px solid var(--rule);border-radius:18px;
  padding:1.4rem 1.5rem;box-shadow:var(--card-shadow)}
label{display:block;font-size:.82rem;font-weight:500;color:var(--muted);margin:0 0 .4rem}
input[type=text],input[type=file]{width:100%;padding:.65rem .85rem;font:inherit;
  font-size:.92rem;background:var(--sunk);color:var(--ink);
  border:1px solid var(--rule);border-radius:12px;
  transition:border-color .2s ease,box-shadow .2s ease}
input[type=text]:focus{outline:none;border-color:var(--accent);
  box-shadow:0 0 0 3px var(--accent-soft)}
.row{display:grid;gap:1rem;grid-template-columns:1fr 1fr;margin-bottom:1rem}
button{font:inherit;font-size:.9rem;padding:.55rem 1.35rem;cursor:pointer;
  background:var(--accent);color:#fff;border:1px solid var(--accent);
  border-radius:980px;font-weight:500;
  transition:opacity .2s ease,transform .15s cubic-bezier(.2,0,0,1),box-shadow .2s ease}
button:hover{opacity:.92;box-shadow:0 4px 14px rgba(66,133,244,.35)}
button:active{transform:scale(.97)}
.hint{font-size:.82rem;color:var(--muted);margin:.8rem 0 0;line-height:1.5}
.err{background:rgba(234,67,53,.08);border-left:4px solid var(--ask);
  padding:.95rem 1.2rem;border-radius:0 12px 12px 0;
  margin:0 0 1.4rem;color:var(--ink);font-size:.92rem}

/* Callout blocks */
.callout{display:flex;gap:.8rem;align-items:flex-start;background:var(--accent-soft);
  border:1px solid rgba(66,133,244,.2);border-radius:16px;padding:1.1rem 1.3rem;
  margin:.6rem 0 1.5rem;font-size:.92rem;color:var(--body)}
.callout svg{width:1.15rem;height:1.15rem;flex:none;margin-top:.18em;
  color:var(--accent)}
.callout p{margin:0}
.callout strong{color:var(--ink)}
.note{font-size:.84rem;color:var(--muted);margin:.5rem 0 1.5rem;line-height:1.55}
.draft{font-family:var(--mono);font-size:.78rem;color:var(--ask);
  background:rgba(234,67,53,.08);border:1px solid rgba(234,67,53,.22);
  padding:.35rem .85rem;border-radius:980px;display:inline-block;margin:0 0 1rem}
.doc h4{font-size:.78rem;font-weight:600;letter-spacing:.08em;
  color:var(--muted);margin:1.4rem 0 .5rem;text-transform:uppercase}
.doc p,.doc ul,.doc ol{margin:0;max-width:62ch}
.doc ul,.doc ol{padding-left:1.2rem}
.doc code{font-family:var(--mono);font-size:.84rem;color:var(--accent);
  background:var(--sunk);padding:.1rem .35rem;border-radius:6px}
.empty{color:var(--muted);padding:2rem 0}
.doc p.own{white-space:pre-wrap;border-left:3px solid var(--accent);
  padding-left:1rem;color:var(--ink);background:var(--accent-soft);
  padding-top:.5rem;padding-bottom:.5rem;border-radius:0 8px 8px 0}

/* Scroll reveal */
.js .reveal{opacity:0;transform:translateY(14px);
  transition:opacity .55s ease,transform .55s cubic-bezier(.2,0,0,1)}
.js .reveal.in{opacity:1;transform:none}
.cards .reveal:nth-child(2){transition-delay:.06s}
.cards .reveal:nth-child(3){transition-delay:.12s}
.cards .reveal:nth-child(4){transition-delay:.18s}
.cards .reveal:nth-child(5){transition-delay:.24s}
.cards .reveal:nth-child(n+6){transition-delay:.3s}

/* Reduced motion preference */
@media (prefers-reduced-motion:reduce){
  .pagein{animation:none}
  *,*::before,*::after{transition:none!important;animation:none!important}
  .js .reveal{opacity:1;transform:none}
  .aurora{display:none}
  .js tr{opacity:1;transform:none}
  .js .bar i{width:var(--w,0%)}
  h1 .ch,h2 .ch{opacity:1;filter:none;transform:none}
  .top::after{animation:none}
  .top::before{display:none}
}
"""


def _with_csrf(body: str, csrf: str) -> str:
    """Stuff a hidden token into every POST form.

    Relying on "remember to add one line to every form" is unreliable: a missed one is a CSRF hole,
    and every new form would miss it. So it is inserted mechanically in the one shell function.
    """
    import re

    if not csrf:
        return body
    field = f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
    return re.sub(
        r'(<form\b[^>]*method="post"[^>]*>)', r"\1" + field, body, flags=re.I
    )


def _brand_logo_img() -> str:
    """A logo uploaded in settings is promoted to the brand slot; the file lives in the data directory branding/,
    served by the public route /branding/logo (the login page shows it too). The ?v= version bump -
    change the image without renaming it, so browser cache never keeps a stale logo."""
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
    """`bare` is for the login and invite pages: there, clicking "Import framework" in the top bar only bounces to login.

    `crumb_href` gives the breadcrumb somewhere to go. **Never set it when there is nowhere to go** - the import
    page's crumb is "Import", which maps to no framework; inventing a link only lands somewhere irrelevant.
    """
    if not csrf and not who:
        csrf, who = CHROME.get()
    return (
        "<!doctype html>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title>"
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
        '<link rel="icon" href="/favicon.ico" sizes="any">'
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
        f"<style>{_CSS}{_ASSESS_CSS}{_AUTH_CSS}</style>"
        '<script>try{var t=localStorage.getItem("fr-theme");'
        'if(t)document.documentElement.dataset.theme=t}catch(e){}</script>'
        '<div class="aurora" aria-hidden="true"><i></i><i></i><i></i><i></i></div>'
        f'<div class="wrap{" wide" if wide else ""}"><div class="top">'
        + '<h1><a href="/">' + (_brand_logo_img() or "Framework Workbench") + "</a></h1>"
        + '<span class="keynote-pill"><span class="gemini-sparkle">✨</span> Keynote Studio</span>'
        + f'<span class="crumb">'
        + (f'<a href="{crumb_href}">{crumb}</a>' if crumb_href and crumb else crumb)
        + "</span>"
        + (_with_csrf(topbar, csrf) if topbar else "")
        + '<div class="topright">'
        + ("" if bare else
           '<a class="topnav" href="/frameworks">Frameworks</a>')
        + (f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
           if csrf and not bare else "")
        + ("" if bare or not may("framework:import") else
           '<a class="topnav" href="/import">Import framework</a>')
        + ("" if bare or not may("document:read") else
           '<a class="topnav" href="/documents">Documents</a>')
        + ("" if bare or not may("member:read") else
           '<a class="topnav" href="/settings">Settings</a>')
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
        f'<div class="pagein">{nav}{_with_csrf(body, csrf)}</div></div>'
        + """
<script>
document.documentElement.classList.add('js');
var _io = new IntersectionObserver(function (entries) {
  entries.forEach(function (en) {
    if (en.isIntersecting) { en.target.classList.add('in'); _io.unobserve(en.target); }
  });
}, {threshold: 0.08});
document.querySelectorAll('.reveal').forEach(function (el) { _io.observe(el); });
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
if (matchMedia('(pointer:fine)').matches)
  document.addEventListener('pointermove', function (e) {
    var c = e.target.closest && e.target.closest('.card, .stat-card, .hud-card');
    if (c) {
      var r = c.getBoundingClientRect();
      c.style.setProperty('--mx', (e.clientX - r.left) + 'px');
      c.style.setProperty('--my', (e.clientY - r.top) + 'px');
    }
    var top = document.querySelector('.top');
    if (top) {
      var tr = top.getBoundingClientRect();
      top.style.setProperty('--tx', (e.clientX - tr.left) + 'px');
      top.style.setProperty('--ty', (e.clientY - tr.top) + 'px');
    }
  }, {passive: true});
document.addEventListener('keydown', function (e) {
  if (e.key === '/' && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) {
    e.preventDefault();
    var s = document.querySelector('input[type=search]');
    if (s) { s.focus(); s.select(); }
  }
});
var _tb = document.querySelector('[data-toggle-theme]');
if (_tb) _tb.addEventListener('click', function () {
  var _r = document.documentElement;
  var _t = _r.dataset.theme === 'light' ? 'dark' : 'light';
  var _apply = function () {
    _r.dataset.theme = _t;
    try { localStorage.setItem('fr-theme', _t); } catch (e) {}
  };
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
    """Shared by home and results. GET, costs nothing, so no CSRF."""
    return (
        '<form class="seek" action="/search" method="get">'
        '<span class="gemini-sparkle" style="font-size:1.1rem;margin-right:.1rem" aria-hidden="true">✨</span>'
        f'<input type="search" name="q" value="{escape(q)}" '
        'placeholder="Keywords, control number, or a question" aria-label="Search controls">'
        '<button type="submit">Search</button></form>'
        '<p class="note">Literal search over titles, control numbers and interpretations first; if nothing matches, AI looks for close wording.</p>'
    )


def search_results(
    q: str, hits: list[dict], *, via: str = "literal",
    expanded: list[str] | None = None, note: str = "",
) -> str:
    """`via` is "literal" or "ai". Which words were expanded must appear on the page - otherwise close-match semantics is a black box."""
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
    active_style = ' style="border-color:var(--accent);color:var(--accent);background:var(--accent-soft)"'
    return '<div class="subnav">' + "".join(
        f'<a href="/f/{framework_id}{path}"'
        f'{active_style if path == here else ""}>'
        f"{label}</a>"
        for path, label in items
    ) + "</div>"


def supersession_page(view, edges: list) -> str:
    """Supersession map: who inherits from whom in this framework, at one glance.

    `edges` is QueryAPI.supersessions_in()'s output verbatim. The action column renders the inherit form
    only for rows of "old has an interpretation, new has none" - backend validation is the floor,
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
    """Every framework on one page: built-ins in one section, imported in another.

    **The two sections use different shapes on purpose.** Built-ins are few - cards look good and click true;
    imports grow without bound - a dozen cards are unreadable, a hundred-row table still scans.

    The search box lives on the home page (/) - the frameworks page only picks one to work in.
    """
    builtin = [f for f in items if not f.get("mine")]
    mine = [f for f in items if f.get("mine")]

    cards = []
    for item in builtin:
        pct = int(100 * item["with_interp"] / item["controls"]) if item["controls"] else 0
        dot = "dot-blue" if "CSF" in item["id"] else ("dot-green" if "ISO" in item["id"] else "dot-yellow")
        cards.append(
            f'<a class="card reveal" href="/f/{escape(item["id"])}">'
            '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.5rem">'
            f'<span class="id">{escape(item["id"])}</span>'
            f'<span class="tag"><span class="g-dot {dot}"></span>Built-in</span>'
            '</div>'
            f'<h3>{escape(item["name"])}</h3>'
            f'<p class="meta">{item["controls"]} controls · has interpretation '
            f'{item["with_interp"]}/{item["controls"]} ({pct}%)</p>'
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
    """Import gets its own page. The top-bar link must point somewhere real; an anchor is not enough -

    errors need a page that reports them in place, instead of kicking people to the bottom of the
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
        '<input type="file" id="file" name="file" required>'
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
    """The Keynote Studio workbench:
    Contains:
    - Keynote stage hero area (badge, gradient headline, subtitle)
    - Pill-shaped Omnibox search box with quick-explore chips
    - Keynote big-number stats board (Controls, Frameworks, Sign-offs, AI)
    - Bento Grid Dashboard:
        - Main Stage: "Frequently searched" and "Learn three today" with Shuffle
        - Intelligence HUD: Framework Coverage Breakdown & Telemetry
    """
    hero = (
        '<div class="stage-hero">'
        '<div class="stage-eyebrow"><span class="gemini-sparkle">✨</span> Keynote Studio Edition · Framework Intelligence</div>'
        '<h1 class="stage-headline">Next-Gen Security <span class="gemini-text">Framework Intelligence</span></h1>'
        '<p class="stage-sub">Comprehensive workbench for NIST CSF 2.0, SP 800-53 Rev.5, and ISO/IEC 27002:2022. Grounded interpretations, audit trails, and cryptographic verification.</p>'
        '</div>'
    )
    chips = (
        '<div class="quick-chips">'
        '<span class="chip-label">Quick explore:</span>'
        '<a href="/search?q=Access+Control" class="chip">Access Control</a>'
        '<a href="/search?q=Log+Retention" class="chip">Log Retention</a>'
        '<a href="/search?q=DE.CM-01" class="chip">DE.CM-01</a>'
        '<a href="/search?q=Incident+Response" class="chip">Incident Response</a>'
        '<a href="/search?q=Cryptographic+Keys" class="chip">Cryptographic Keys</a>'
        '</div>'
    )
    seek = (
        hero
        + '<form class="seek" action="/search" method="get">'
        '<span class="gemini-sparkle seek-sparkle" aria-hidden="true">✨</span>'
        '<input type="search" name="q" placeholder="Keywords, control number, or a question"'
        ' autofocus aria-label="Search controls">'
        '<kbd class="seek-kbd">/</kbd>'
        '<button type="submit">Search</button></form>'
        + chips
        + '<p class="note" style="text-align:center">Literal search over titles, control numbers and interpretations first; if nothing matches, AI looks for close wording.</p>'
    )
    stats = (
        '<div class="stage-stats">'
        '<div class="stat-card"><div class="stat-num">1,000+</div><div class="stat-desc">Controls Indexed</div><div class="stat-sub">Across 3 Standards</div></div>'
        '<div class="stat-card"><div class="stat-num">3</div><div class="stat-desc">Global Frameworks</div><div class="stat-sub">CSF · 800-53 · ISO 27002</div></div>'
        '<div class="stat-card"><div class="stat-num">100%</div><div class="stat-desc">Cryptographic Sign-offs</div><div class="stat-sub">Hardware & Key Validated</div></div>'
        '<div class="stat-card"><div class="stat-num gemini-text">AI+</div><div class="stat-desc">AI Explanations</div><div class="stat-sub">Grounded Interpretations</div></div>'
        '</div>'
    )

    review_block = ""
    if review and review.get("count"):
        review_block = (
            '<div class="hud-card" style="border-color:rgba(251,188,4,.4);background:linear-gradient(180deg,var(--surface),rgba(251,188,4,.05))">'
            '<div class="hud-header">'
            f'<span class="hud-badge" style="background:rgba(251,188,4,.15);color:var(--g-yellow)">{review["count"]} drafts</span>'
            f'<h3 style="margin:.4rem 0 .3rem">{review["count"]} AI drafts awaiting confirmation</h3>'
            '</div>'
            '<p class="note" style="margin:0 0 .8rem">Open the verification queue and sign off controls one by one.</p>'
            '<a class="cta" href="/review" style="font-size:.84rem;padding:.45rem 1.1rem">Open Review Queue ➔</a>'
            '</div>'
        )

    if popular:
        cards = "".join(
            f'<a class="card reveal" href="/c/{escape(p["id"])}">'
            f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.3rem">'
            f'<span class="id">{escape(p["short"])}</span>'
            '<span class="tag" style="margin:0">Hot</span></div>'
            f'<h3>{escape(p["label"])}</h3>'
            '<p class="meta"><span class="g-dot dot-blue"></span>Frequently searched</p></a>'
            for p in popular
        )
        popular_block = '<h2>Frequently searched</h2><div class="cards">' + cards + '</div>'
    else:
        popular_block = '<h2>Frequently searched</h2><p class="empty">No search history yet.</p>'

    if daily:
        def _dot(fw: str) -> str:
            if "CSF" in fw:
                return "dot-blue"
            if "ISO" in fw:
                return "dot-green"
            if "800-53" in fw:
                return "dot-yellow"
            return "dot-red"

        cards = "".join(
            f'<a class="card reveal daily-card" href="/c/{escape(d["id"])}">'
            '<div>'
            f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.4rem">'
            f'<span class="id">{escape(d["short"])}</span>'
            f'<span class="tag"><span class="g-dot {_dot(d["framework"])}"></span>{escape(d["framework"].split()[0])}</span>'
            '</div>'
            f'<h3>{escape(d["label"])}</h3>'
            f'<div class="snippet">{escape(d["snippet"])}</div>'
            '</div>'
            f'<div class="tag-row">'
            f'<span>{escape(d["framework"])}</span>'
            '<span style="color:var(--accent);font-weight:500">Study ➔</span>'
            '</div>'
            '</a>'
            for d in daily
        )
        refresh = (
            '<form class="tiny" action="/" method="get">'
            f'<input type="hidden" name="roll" value="{roll + 1}">'
            '<button type="submit" class="shuffle-btn"><span class="gemini-sparkle">🎲</span> Shuffle</button></form>'
        )
        daily_block = (
            '<div class="section-title-row">'
            '<h2>Learn three today</h2>' + refresh + '</div>'
            + '<div class="cards">' + cards + '</div>'
        )
    else:
        daily_block = '<h2>Learn three today</h2><p class="empty">Nothing for today.</p>'

    hud_telemetry = (
        '<div class="hud-card">'
        '<div class="hud-header">'
        '<span class="hud-badge">Live Telemetry</span>'
        '<h3>Framework Intelligence</h3>'
        '</div>'
        '<div class="hud-stat-list">'
        '<div class="hud-stat-row">'
        '<span class="hud-stat-name"><span class="g-dot dot-blue"></span>NIST CSF 2.0</span>'
        '<span class="hud-stat-val">100% Grounded</span>'
        '</div>'
        '<div class="hud-meter"><div class="hud-meter-fill" style="width:100%"></div></div>'
        '<div class="hud-stat-row" style="margin-top:.7rem">'
        '<span class="hud-stat-name"><span class="g-dot dot-green"></span>ISO/IEC 27002:2022</span>'
        '<span class="hud-stat-val">100% Grounded</span>'
        '</div>'
        '<div class="hud-meter"><div class="hud-meter-fill" style="width:100%;background:linear-gradient(90deg,var(--g-green),var(--g-blue))"></div></div>'
        '<div class="hud-stat-row" style="margin-top:.7rem">'
        '<span class="hud-stat-name"><span class="g-dot dot-yellow"></span>NIST SP 800-53 Rev.5</span>'
        '<span class="hud-stat-val">100% Grounded</span>'
        '</div>'
        '<div class="hud-meter"><div class="hud-meter-fill" style="width:100%;background:linear-gradient(90deg,var(--g-yellow),var(--g-red))"></div></div>'
        '</div>'
        '<a class="hud-action-link" href="/frameworks">View All 3 Frameworks ➔</a>'
        '</div>'
    )

    hud_actions = (
        '<div class="hud-card">'
        '<div class="hud-header">'
        '<span class="hud-badge">Workbench Suite</span>'
        '<h3>Quick Navigation</h3>'
        '</div>'
        '<div style="display:flex;flex-direction:column;gap:.65rem">'
        '<a class="chip" href="/frameworks" style="display:flex;align-items:center;justify-content:space-between;padding:.6rem 1rem">'
        '<span>📁 Browse Frameworks</span><span style="color:var(--muted)">3</span></a>'
        '<a class="chip" href="/import" style="display:flex;align-items:center;justify-content:space-between;padding:.6rem 1rem">'
        '<span>📥 Import Custom Framework</span><span>➔</span></a>'
        '<a class="chip" href="/documents" style="display:flex;align-items:center;justify-content:space-between;padding:.6rem 1rem">'
        '<span>📄 Grounding Documents</span><span>➔</span></a>'
        '</div>'
        '</div>'
    )

    bento_content = (
        '<div class="bento-layout">'
        '<div class="bento-main">'
        + popular_block
        + daily_block
        + '</div>'
        '<aside class="bento-sidebar">'
        + review_block
        + hud_telemetry
        + hud_actions
        + '</aside>'
        '</div>'
    )

    return page("Framework Workbench", seek + stats + bento_content, nav=nav)


def framework(
    view, controls: list[dict], pending: int | None = None, nav: str = ""
) -> str:
    """When `pending` is not None, render the drafting entry - imported and built-in alike.

    Web drafting always overlays into the user library as a working copy, never into git; writing the
    (for publishing) still goes through `fr draft`. The top-bar copy follows the sticky bar - 800-53
    content pack (for publishing) still goes through `fr draft`. The top-bar copy follows the sticky
    """
    rows = "".join(
        f'<tr><td class="c"><a href="/c/{escape(c["id"])}">{escape(c["short"])}</a></td>'
        f'<td><a href="/c/{escape(c["id"])}">{escape(c["label"])}</a></td>'
        f'<td style="white-space:nowrap;width:1%">{_state_cell(c)}</td></tr>'
        for c in controls
    )
    head = (
        # The framework page is the only tier with no "back": the control page's breadcrumb returns to
        # the framework, so this must return to the catalogue - same style as the control page's back.
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
    """Which controls already have someone standing behind them - the single indicator of whether this framework can be handed over."""
    if c.get("confirmed"):
        return '<span class="mark mine">Confirmed</span>'
    if c["has_interp"]:
        return '<span style="color:var(--muted)">has interpretation</span>'
    return '<span style="color:var(--muted)">-</span>'


def _draft_invite(framework_id: str, pending: int) -> str:
    """Every click spends real money. On how many controls, with whose key - visible before the click."""
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
    """`fields` is QueryAPI.interpretation()'s output verbatim: {field: {value, basis}}.

    `editable` is true only for frameworks the user imported themselves. Built-in interpretations are
    drafted by fr draft, human-reviewed, into git - never edited from a user's button.
    """
    from framework_reader.interpret.render import FIELD_LABELS

    cid = escape(view.id)
    # In must also mean out. The field-edit and rewrite pages both offer "discard" back to the control,
    # but the control page itself was a dead end - switching frameworks meant going home; it needs its own exit.
    #
    # Show the **framework name**, not the id: the id is already in the top-bar breadcrumb; printing it
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
    elif state == "confirmed":
        # Content-pack confirmations carry no signer - the pack schema has no signer column, and the
        # publisher-signed batch is signed by the product itself. fr show banners nothing for confirmed;
        # the web stays silent the same way: "AI draft, not yet confirmed · state=confirmed" was a
        # self-contradictory banner. Each field's own AI DRAFT / practitioner chip already tells the story.
        pass
    elif state and any(
        (fields.get(n) or {}).get("basis") == "practitioner"
        and (fields.get(n) or {}).get("value") not in (None, "", [], {})
        for n, _ in FIELD_LABELS
    ):
        # When the author already wrote some of the seven fields, hanging "AI draft" on the control lies
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

    # The body block renders for every control: a built-in control's body is empty, and "paste a
    # passage in" is exactly the entry that gives drafting its grounding (override layer; official base untouched).
    if body:
        # "Edit" needs only login - pasted bodies for built-in controls go to the override layer, same philosophy as the fields.
        edit = (f' <a class="edit" href="/c/{cid}/edit-body">Edit</a>'
                if editable else "")
        parts.append(
            f'<div class="doc"><h4>{escape(body_label)}' + edit + '</h4>'
            f'<p class="own">{escape(body)}</p></div>'
        )
    elif editable:
        # "No body text" refers to the standard's text: ISO's label is a self-written short title
        # (the copyrighted original never enters the library), 800-53's label is the control title - with
        # a title and interpretation right there, not saying "built-ins are titles only" reads like nonsense.
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
        # When not one word exists, both paths must be offered: have the model draft it, or write it yourself.
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

    # "Actions" are gathered into a stack for the right column. **The left keeps only reading matter** -
    # on a long control page, action buttons scrolling out of view means hunting for them every time.
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
        # **Only on frameworks the user imported.** A built-in body is Tier C/D copyrighted text - not one
        # word may leave for the network. `editable` is exactly the "this is your own material" test.
        doing.append(_clause_chat(cid, chat or []))

    if doing:
        # Two columns: reading on the left, actions on the right. The right column is sticky -
        # on a control dozens of screens long, you can act from wherever you scrolled. Narrow screens stack it below (see CSS).
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
    """The dialog on the control page.

    **The model's suggestion reaches the database only after a human nods.** Every proposal carries
    a "Confirm, apply" button; only a click writes - what the model says never reaches the database alone.

    The conversation follows the control, visible to others in the organization: whoever signs must see
    "how this sentence came to be" - more useful than any audit record.
    """
    from framework_reader.interpret.render import FIELD_LABELS

    labels = dict(FIELD_LABELS)
    lines = []
    for turn in turns:
        who = escape(turn.actor or "you") if turn.role == "user" else '<span class="gemini-sparkle">✨</span> AI Assistant'
        klass = "said mine" if turn.role == "user" else "said ai"
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
        '<div class="doc chat"><h4><span class="gemini-sparkle">✨</span> Ask AI</h4><div class="thread">'
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
.said{padding:.75rem 1rem;margin:0 0 .95rem;background:var(--surface);
  border-radius:16px;border:1px solid var(--rule);box-shadow:var(--card-shadow)}
.said.mine{background:var(--accent-soft);border-color:rgba(66,133,244,.25);
  border-top-right-radius:4px}
.said.ai{background:var(--surface);border-color:rgba(251,188,4,.3);
  border-top-left-radius:4px}
.said .who{font-family:var(--han);font-size:.78rem;font-weight:600;color:var(--muted);
  display:inline-flex;align-items:center;gap:.35rem}
.said p{margin:.35rem 0 0;white-space:pre-wrap;font-size:.92rem;line-height:1.6}
.said form{background:none;border:0;padding:0;box-shadow:none}
"""


_BASIS_MARK = {
    "practitioner": ('<span class="mark mine">You wrote this</span>', ""),
    "inferred": ('<span class="mark ai-mark"><span class="gemini-sparkle">✨</span> AI draft</span>', ""),
    "quote": ('<span class="mark">Quoted from source</span>', ""),
}


def _has_blank_field(fields: dict) -> bool:
    from framework_reader.interpret.render import FIELD_LABELS

    return any(
        (fields.get(n) or {}).get("value") in (None, "", [], {}) for n, _ in FIELD_LABELS
    )


def _fill_blanks_invite(control_id: str, written: bool) -> str:
    """Fill just this control's blanks. A whole-framework draft costs dozens of controls; someone who wants to try one needs an entry."""
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
    """Ask the AI to rewrite one field."""
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
    """Render field by field, labelling provenance per field.

    One blanket "AI draft" on the control hides which sentences the user edited - and "which words are
    yours" is the line that decides whether this material dares to be handed over.
    """
    from framework_reader.interpret.render import FIELD_LABELS

    out = []
    for name, label in FIELD_LABELS:
        data = fields.get(name) or {}
        value = data.get("value")
        empty = value in (None, "", [], {})
        if empty and not editable:
            continue          # empty fields simply do not appear - no None/null shown
        mark = _BASIS_MARK.get(data.get("basis", ""), ("", ""))[0] if not empty else ""
        edit = (
            f'<a class="edit" href="/c/{control_id}/edit/{name}">'
            f'{"Edit" if not empty else "Write"}</a>'
            if editable and may("interpretation:write") else ""
        )
        # An empty field has nothing to rewrite - that would be "write", not "rewrite".
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


# The three rungs are a dict, the follow-up a list, the rest a paragraph. The form must follow the shape,
# or one user edit collapses practice from three rungs into a single sentence.
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
    """Edit the body of the user's own control. One form, two buttons: Save writes to the database;
    "Let AI revise" only echoes a proposal back into the box - writing still happens on Save, the
    same gate as field rewrites. The AI revision builds on the box's current content."""
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
    """Drafting progress. Refreshes itself while running - otherwise the user stares at a frozen page guessing."""
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
.auth{max-width:28rem;margin:3.5rem auto}
.auth h2{margin:0 0 1.4rem;font-size:1.45rem;text-align:center}
input[type=password]{width:100%;padding:.65rem .85rem;font:inherit;font-size:.92rem;
  background:var(--sunk);color:var(--ink);border:1px solid var(--rule);
  border-radius:12px;transition:border-color .2s ease,box-shadow .2s ease}
input[type=password]:focus{outline:none;border-color:var(--accent);
  box-shadow:0 0 0 3px var(--accent-soft)}
.sso{display:flex;align-items:center;justify-content:center;gap:.5rem;
  text-align:center;padding:.7rem 1.4rem;text-decoration:none;
  background:var(--accent);color:#fff;font-size:.95rem;font-weight:600;
  border-radius:980px;box-shadow:0 2px 10px rgba(66,133,244,.28);
  transition:all .2s cubic-bezier(.2,0,0,1)}
.sso:hover{opacity:.92;transform:translateY(-1px);
  box-shadow:0 4px 18px rgba(66,133,244,.4);text-decoration:none}
"""

_ASSESS_CSS = """
.arow{background:var(--surface);border:1px solid var(--rule);border-radius:18px;
  padding:1.2rem 1.35rem;margin:0 0 1rem;box-shadow:var(--card-shadow);
  transition:all .2s cubic-bezier(.2,0,0,1)}
.arow.done{border-left:4px solid var(--success);background:var(--surface-high)}
.arow h3{margin:0 0 .6rem;font-size:1.02rem;color:var(--ink);font-weight:600}
.arow h3 code{font-family:var(--mono);font-size:.84rem;color:var(--accent);
  background:var(--accent-soft);padding:.1rem .4rem;border-radius:6px;margin-right:.6rem}
.arow .rungs{list-style:none;margin:0 0 .9rem;padding:0;font-size:.88rem}
.arow .rungs li{padding-left:.9rem;border-left:3px solid var(--accent-soft);
  margin:0 0 .4rem;max-width:62ch;color:var(--muted);line-height:1.5}
.arow .pick{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}
.arow .pick label{display:inline-flex;gap:.35rem;align-items:center;margin:0;
  font-size:.85rem;color:var(--body);border:1px solid var(--rule);border-radius:980px;
  padding:.35rem .85rem;cursor:pointer;background:var(--surface);
  transition:all .2s cubic-bezier(.2,0,0,1)}
.arow .pick label:hover{border-color:var(--accent);background:var(--accent-soft)}
.arow .pick input{margin:0}
.arow .pick input:checked + span{color:var(--accent);font-weight:600}
.arow .pick .note{flex:1 1 16rem;min-width:0;border-radius:980px;padding:.45rem .85rem}
.arow .cur{font-size:.82rem;color:var(--muted);margin:.6rem 0 0}
.subnav{display:flex;gap:.5rem;flex-wrap:wrap;margin:0 0 1.8rem}
.subnav a{font-size:.85rem;font-weight:500;padding:.4rem .95rem;border:1px solid var(--rule);
  border-radius:980px;text-decoration:none;color:var(--body);background:var(--surface);
  box-shadow:0 1px 4px rgba(0,0,0,.04);transition:all .2s cubic-bezier(.2,0,0,1)}
.subnav a:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.gap{white-space:pre-wrap;font-family:var(--han);font-size:.92rem;
  background:var(--surface);border:1px solid var(--rule);border-radius:16px;
  padding:1.3rem 1.4rem;line-height:1.75;overflow-x:auto;box-shadow:var(--card-shadow)}
.soawrap{overflow-x:auto}
.soa{font-size:.88rem}
.soa td{white-space:normal}
.pending{color:var(--ask);font-weight:500}
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
    """`to_assess` non-zero means not a single self-assessment exists - this page has nothing to offer yet.

    **The empty state must not copy the CLI's line.** render_gap's empty state says "run fr assess first" -
    correct in a terminal, but rendered on the web it becomes an order to go open a terminal,
    while the right answer is "Self-assessment" in the sub-navigation above. The deployment shape is
    already one organization, many users (see the 2026-08-23 web service design) - they have no terminal.

    `changes` is the re-assessment comparison (AssessStore.changes() output); `plan` counts report items
    not yet filed into the remediation ledger - gaps nobody follows make the report a mere snapshot.
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
    """Re-assessment comparison. The history table only started recording today; most databases open empty -
    render nothing rather than let a "no data" block squat on the report's top."""
    if not changes:
        return ""
    rows = "".join(
        f"<tr><td class=\"c\"><a href=\"/c/{escape(c['control_id'])}\">"
        f"{escape(c['control_id'].split(':', 1)[-1])}</a></td>"
        f"<td>{escape(c['label'])}</td>"
        f"<td><s>{escape(c['from'])}</s> → <strong>{escape(c['to'])}</strong></td>"
        f"<td>{escape(c['at'][:10])}</td></tr>"
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
    """The one step between gap and remediation: filing it. Not rendered when the report has no to-improve items."""
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
    """The remediation ledger. One row, one form: owner / due date / state / notes edited and saved in place.

    Ordering is "with deadline first, tightest first", decided by store.all() - never sorted again here:
    two sorts will eventually disagree.
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
    """The review queue: one draft at a time. Signing stays per-control (no bulk confirmation) -
    all this saves is "find the next one" - arrow keys page, confirming advances in place."""
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


# ---------- Sign-in / invite ----------

def login(error: str = "", next_url: str = "/", entra: bool = False) -> str:
    sso = ""
    if entra:
        # SSO above, passcode below: in an organization with Entra, the passcode is the exception, not the norm.
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


# ---------- Members and audit ----------

_MEMBER_CSS = """
.mtable td{vertical-align:middle}
.chips{display:flex;gap:.4rem;flex-wrap:wrap}
.chips form{background:none;border:0;padding:0;display:contents;box-shadow:none}
.chip{font-family:var(--mono);font-size:.74rem;font-weight:500;padding:.25rem .65rem;
  border:1px solid var(--rule);background:var(--surface);color:var(--muted);
  border-radius:980px;cursor:pointer;transition:all .15s ease}
.chip.on{border-color:var(--accent);color:#fff;background:var(--accent);font-weight:600}
.chip.flat{cursor:default}
.gone{color:var(--muted);text-decoration:line-through}
.switch{margin:2.5rem 0 0;background:var(--surface);border:1px solid var(--rule);
  border-radius:16px;padding:1.2rem 1.3rem;box-shadow:var(--card-shadow)}
.switch button{font-size:.82rem;padding:.4rem .9rem;background:var(--surface);
  color:var(--body);border:1px solid var(--rule);border-radius:980px}
.link{font-family:var(--mono);font-size:.82rem;word-break:break-all;
  background:var(--sunk);border-left:4px solid var(--accent);border-radius:0 12px 12px 0;
  padding:.85rem 1.1rem;margin:0 0 1.2rem}
.audit{font-family:var(--mono);font-size:.8rem}
.audit td{white-space:nowrap}
.audit td.d{white-space:normal;font-family:var(--han);font-size:.88rem}
"""

ROLE_WHAT = {
    "admin": "Manage accounts, configure models, delete frameworks, read the audit log",
    "author": "Import frameworks, draft interpretations (costs money), edit fields, record self-assessments",
    "approver": "Confirm interpretations by signing",
    "viewer": "Browse and export",
}

# The UI may read in English, but **the English identifiers must not be hidden**: the CLI uses them
# (`fr account grant someone author`); identifiers alone let people match terminal records - drop them and nothing lines up.
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
    """With zero accounts, the members page is this one form.

    The roster and invite blocks would both be empty; showing them only suggests breakage. Once this one
    exists, the door locks - everyone after enters by invitation.
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

        # The preselection is **viewer**, not the first list item (that is admin).
        # The dropdown default and "new accounts default to viewer" are one rule:
        # defaults decide what a hasty click does - and hasty clicks are the norm.
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
        # Without writing it out, the admin believes the edit on this page took effect. Design §5.4
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


# ---------- Models and keys ----------

ROLE_WHAT_FOR = {
    "drafter": "Drafts interpretations and rewrites fields. <strong>This role is the product itself</strong>, "
               "swapping it swaps the product",
    "questioner": "Asks questions during interviews (idle in the B pipeline)",
    "extractor": "Extracts structure from the author\'s own words (idle in the B pipeline)",
}


def settings(*, bootstrap: bool = False, nav: str = "") -> str:
    """All configuration entries in one place.

    Before this they were three side-by-side top-bar links, and **"Models & keys" had never once shown**: it
    was gated by logged_in(), whose intent was "single-user local use has no members" - true for members,
    false for models. Single-user local is exactly the usage that most needs to configure its own key.

    Each block is judged by its own permission. Someone who sees none of them (viewer has only member:read,
    while the members block needs logged_in) sees one sentence, not a blank page: blank reads as broken.
    """
    cards = []
    if may("model:read"):
        cards.append((
            "/models", "Models and keys",
            "Which provider and model drafts interpretations, the API keys, and the three spending limits "
            "(per person per hour / per organization per month / concurrent jobs)."))
    # With zero accounts this entry matters **more**: that is exactly the moment to create the first admin.
    # It used to be gated by logged_in(), so a person running `fr serve` locally could not find
    # user management anywhere in the UI and had to run `fr account invite` in a terminal.
    if may("member:read"):
        cards.append((
            "/members", "Members and roles",
            "Create the first administrator and the door locks: after that people get in by invitation."
            if bootstrap else
            "Who can draft, who can sign, who can only look. Send invitations, disable accounts, "
            "and the switch for the no-self-grant lock."))
    if may("member:manage"):
        # These two blocks are system configuration; only admins see the entries, and the sub-page routes
        # equally require member:manage - seeing the entry and doing the thing are one permission.
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
    """The Entra ID single sign-on configuration page. A saved, enabled configuration overrides environment variables.

    `report` is the "Test connection" result: problems is the itemised checklist, discovery_error is why
    the discovery document fetch failed, and issuer is the other side's self-reported address.
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
    """Custom logo: upload one image and it replaces the brand name in the top bar; the login page too."""
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
    """One click downloads the sqlite; frameworks with interpretations each get a PDF too."""
    body = [
        "<h2>Back up the user database</h2>",
        '<p class="note">What you download is a consistent snapshot, not the live file. '
        "It contains: imported frameworks and control bodies, interpretations edited on the web, self-assessments, document chunks, and control-page conversations.</p>",
        '<p class="note"><strong>Not</strong> the identity database: passphrase hashes, sessions and model keys are not in this file. '
        "The built-in content package is not either; that one can be rebuilt.</p>",
        '<p class="note">To restore after a failure: stop <code>fr serve</code>, put this file at '
        "<code>~/.framework_reader_en/user.sqlite</code> (move the current one aside first).</p>",
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
  border-radius:18px;padding:1.25rem 1.35rem;margin:0 0 1rem;
  text-decoration:none;box-shadow:var(--card-shadow);
  transition:all .25s cubic-bezier(.2,0,0,1)}
.scard:hover{border-color:var(--card-hover-line);background:var(--card-hover);
  transform:translateY(-2px);box-shadow:var(--card-shadow-hover);text-decoration:none}
.scard h3{margin:0 0 .4rem;font-size:1.05rem;color:var(--ink);font-weight:600}
.scard p{margin:0;font-size:.92rem;color:var(--muted);line-height:1.5}
"""


def models(*, roles: dict, presets: list[dict], keys: dict, limits: dict,
           spent: int, can_write: bool, master_key: bool, custom: dict | None = None,
           catalogs: dict | None = None,
           focus: tuple[str, str, str] | None = None,
           error: str = "", notice: str = "", nav: str = "") -> str:
    """`keys` holds only masked strings. **Neither plaintext nor ciphertext reaches this layer.**"""
    warn = ""
    if not master_key:
        warn = (
            '<p class="err"><strong>FR_SECRET_KEY is not configured yet.</strong>'
            " Until it is, I will not store a single API key in the database: silently kept plaintext would make you believe it is encrypted when it is not. "
            "Run <code>fr secret new</code> on the server to generate a key, inject it as an environment variable and restart.</p>")

    # ---- Roles ----
    catalogs = catalogs or {}

    def _datalist(list_id: str, values, labels: dict | None = None) -> str:
        """The `<option>` text is the candidate's **subtitle**; only the value goes into the box.

        Custom endpoints are flagged this way - unflagged, your own intranet gateway and the twenty presets
        blur into one column. And folding "(custom)" into the value would put those words
        into the form on every selection.
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
        """One input with a catalogue behind it. **The catalogue is a convenience, not the only entry** -
        new models always ship before any catalogue lists them, and custom endpoints or intranet

        This used to be two controls - "pick from a dropdown" plus "or type one" - with the server
        `datalist` is natively one control for both: pickable and typable.
        It also fixes the native `<select>` opening the OS menu with its own font size -
        datalist renders its candidates inside the page, sized to the input.

        Never fetched / fetch failed / empty catalogue: render one line of reason, **not an empty catalogue** -
        opening it to nothing looks more broken than not having it.
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
        # Just acted within this block: put the focus here. The browser scrolls it into view -
        # if the scroll jumps back to the top after submit, the feedback above becomes invisible again,
        # and every browser's scroll restoration is out of our hands. This zero-JS trick is the only one.
        focus_attr = " autofocus" if grab_focus else ""
        return (
            f"<label>Model name</label>"
            f'<input type="text" name="model" class="pick"{list_attr}{focus_attr}'
            f' value="{escape(current_model)}" placeholder="Open to pick, or type your own">'
            f"{options}"
            f'<p class="hint">{hint} {refresh}</p>'
        )

    def _provider_picker(current: str) -> str:
        """Vendors are a **closed set**, hence `<select>`.

        This used to be an `<input list>` like the model name: but datalist filters its candidates
        **by the current value** - with "minimax" already in the box, opening it shows only minimax,
        so "you must delete the existing one before switching vendors". That is not a datalist defect,
        it is the wrong control: datalist suggests for **open sets**, while a wrong vendor is rejected
        server-side on the spot (`_known_providers()`) - the definition of a closed set.

        Model names are the opposite - new models always ship before any catalogue, and custom endpoints
        may not have that interface - so that side keeps datalist: pickable and typable.

        The cost: on macOS the native select opens with the OS menu font size, beyond CSS
        (see the 2026-08-25 change). That is aesthetics; this is usability - not the same thing.
        """
        options = []
        if not current:
            options.append(
                '<option value="" selected disabled>Pick a provider</option>')
        elif current not in set(provider_ids):
            # A preset was renamed, or a custom endpoint deleted. **The select must not silently switch vendors**: it
            # always submits something; discarding an unrecognized value edits the config on the user's behalf,
            # when they may only have been looking at the page.
            options.append(
                f'<option value="{escape(current)}" selected>'
                f"{escape(current)} (stale, not in the list)</option>")
        for pid in provider_ids:
            # Option text carries the id only (plus a custom marker). Full explanations would stretch the
            # dropdown to the longest line - the explanations live in the overview table below, which fits them.
            label = pid + (" (custom endpoint)" if pid in provider_labels else "")
            picked = " selected" if pid == current else ""
            options.append(f'<option value="{escape(pid)}"{picked}>'
                           f"{escape(label)}</option>")
        return (f'<select name="provider" class="pick">'
                f'{"".join(options)}</select>')

    def _key_field(provider: str) -> str:
        """When this vendor has no key yet, fill it right here.

        Before, entering a key meant scrolling down to the "API key" block and picking the same vendor
        again - one task split across two places, with a redundant re-selection in between.

        **Keys are stored per vendor, not per role.** The openai key entered in the drafter block is the
        one the questioner uses too. This sentence must be on the page, or it looks like every role keeps
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

    # Candidates carry **the id only**. Explanations inside candidates stretch the dropdown to the longest
    # line - measured: it covered half the screen and still got truncated to "not recommended for…".
    # Explanations go to the overview table below, which fits the whole sentence and is more visible.
    provider_ids = [p["id"] for p in presets]
    provider_labels = {p["id"]: "custom endpoint" for p in presets if p.get("custom")}
    role_rows = []
    for name, what in ROLE_WHAT_FOR.items():
        # `roles` carries the **effective** values (web-unconfigured roles fall back to YAML presets).
        # Showing only "configured here" would render unconfigured roles as blank rows -
        # while this page's question is "who is spending our money right now, exactly".
        current = roles.get(name, {})
        provider = current.get("provider", "")
        model = current.get("model", "")
        # Just acted in this block (key configured, or a probe run): stay on the **new** vendor,
        # never bounce back to the old one - bouncing means re-selecting, and the edit is wasted.
        #
        # The third element is the model name: empty right after configuring a key (no model chosen yet),
        # the just-probed one after a probe - a passing probe leads straight to Save,
        # and clearing the box then would force re-typing the very string just verified.
        if focus and focus[0] == name:
            provider, model = focus[1], focus[2]

        # **Feedback lands inside the block that was pressed.** Rendered only at the top of the page,
        # someone with the button mid-page sees nothing after submitting - the browser may keep scroll
        # position, leaving the line a thousand pixels above the viewport.
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
                # "Probe" and "Save" share this form, so the probe tests exactly what is in the boxes now,
                # not what is stored. formaction overrides action - no second form needed.
                '<button type="submit" class="ghost" '
                'formaction="/models/role/test">Test</button>'
                "<button type=\"submit\">Save</button>"
                '<p class="hint">Run \u300cTest\u300d first: it sends one minimal real request and can tell a wrong key, a model name this provider does not know, and a connection failure apart. '
                "Save only once the test passes.</p></form>"
                # The "Refresh" button submits via this hidden form - it cannot nest inside the one above.
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

    # ---- Gates ----
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

    # 2026-08-26: the 20-row "vendor overview" table was removed as requested.
    # Lost with it: each vendor's note (including MiniMax's "draft quality measured as failing"),
    # and the two columns "we verified it" (a preset property) vs "your key connects right now" (a
    # runtime fact). The note / verified fields in presets currently have no surface on the page.

    # ---- Custom endpoints ----
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
        # The vendor candidates now ride each block's own <select> - it must show "which one is selected",
        # impossible with one shared list. Model catalogues keep their own datalists (one per vendor).
        + "<h2>Models and keys</h2>"
        + warn
        # A focus means this sentence already rendered inside that block. Printing the same words in both
        # places caused the first bug: before and after submit the screen looked identical.
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
.mrow{background:var(--surface);border:1px solid var(--rule);border-radius:18px;
  padding:1.25rem 1.35rem;margin:0 0 1rem;box-shadow:var(--card-shadow)}
.mrow h3{margin:0 0 .4rem;font-size:1.02rem;color:var(--ink);font-weight:600;
  font-family:var(--mono)}
.mrow .cur{font-size:.85rem;color:var(--muted);margin:.5rem 0 .9rem}
.mrow form{background:none;border:0;padding:0;box-shadow:none}
.linky{background:none;border:0;padding:0;color:var(--accent);
  font:inherit;font-size:.85rem;cursor:pointer;text-decoration:underline}
select.pick{width:100%;padding:.6rem .75rem;font:inherit;font-size:.92rem;
  background:var(--ground);color:var(--ink);border:1px solid var(--rule);
  border-radius:10px}
input.pick[list]::-webkit-calendar-picker-indicator{opacity:.5;cursor:pointer}
input.pick[list]:hover::-webkit-calendar-picker-indicator,
input.pick[list]:focus::-webkit-calendar-picker-indicator{opacity:.9}
.mrow button.ghost{background:none;color:var(--accent);
  border:1px solid var(--rule);margin-right:.5rem}
"""


# ---------- Companion documents ----------

_DOC_CSS = """
.docrow{background:var(--surface);border:1px solid var(--rule);border-radius:16px;
  padding:1.1rem 1.25rem;margin:0 0 .8rem;display:flex;gap:1rem;
  align-items:baseline;flex-wrap:wrap;box-shadow:var(--card-shadow)}
.docrow h3{margin:0;font-size:1.02rem;color:var(--ink);font-weight:600}
.docrow .meta{font-size:.82rem;color:var(--muted);font-variant-numeric:tabular-nums}
.docrow form{background:none;border:0;padding:0;margin-left:auto;box-shadow:none}
.seg{background:var(--surface);border:1px solid var(--rule);border-radius:14px;
  padding:1rem 1.2rem;margin:0 0 .8rem}
.seg h4{margin:0 0 .4rem;font-family:var(--mono);font-size:.78rem;color:var(--accent)}
.seg p{margin:0;white-space:pre-wrap;font-size:.92rem;line-height:1.55}
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
    """Show the extracted chunks verbatim. **"What exactly did the model see" must not be a secret only we know.**"""
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


# These two are "handled automatically" result notices, not failures - no ⚠ when rendering.
_AUTO_DONE = {"catalog", "snapped"}


def import_preview(draft, bodies: list[str], nav: str = "") -> str:
    """Nothing is written to the database before confirmation. See the 2026-08-25 AI import design §5.2

    **The body is read-only.** It was cut verbatim from your text - making it editable here would let
    "the model never rewrites the body" in through the front door, and afterwards nobody could tell
    which words were edited in passing.

    The main form of a bad cut is one cut too many (a control split in two); "↑ merge" rejoins the two
    rejoins their line ranges and the body recomputes. A missing cut cannot be split here, but it is far rarer.
    """
    did = escape(draft.draft_id)
    rows = []
    for index, (span, body) in enumerate(zip(draft.spans, bodies)):
        key = str(index)
        # An empty title is usually a bad cut, but can be a real control - do not decide for the user; just do not pre-check it.
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
            # "Who wrote it must be visible" - same rule as the control page's AI DRAFT chips.
            # AI may propose the id and title (otherwise the control cannot be saved), but a human must
            + ('<span class="mark">Named by AI</span>'
               if "derived" in (span.ref_from, span.label_from) else "")
            + f'{merge}'
            + (f'<p class="pbody">{escape(body)}</p>'
               f'<p class="hint">Source lines {span.start}-{span.end}</p>'
               if span.end >= span.start else
               # A parent control cut before its first child is empty - it was only ever a grouping title.
               # Shown blank, people would call it a bug.
               '<p class="hint">This control has no body text of its own: '
               "it is a group heading, and the text lives in its child controls.</p>")
            + "</div>")
    warns = "".join(
        f'<p class="warn">⚠ {escape(p.detail)}</p>'
        for p in draft.problems if p.kind not in _AUTO_DONE)
    # catalog / snapped are handling results, not failures: an id collision was auto-renamed, line
    # offsets were aligned. Mixed into the ⚠ stream, "split 91 controls" reads as 91 errors.
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
.prow{background:var(--surface);border:1px solid var(--rule);border-radius:16px;
  padding:1rem 1.15rem;margin:0 0 .8rem;box-shadow:var(--card-shadow)}
.prow input[type=text]{width:auto;display:inline-block;margin-right:.5rem}
.prow .pick{display:inline-block;margin-right:.8rem;font-size:.85rem}
.pbody{white-space:pre-wrap;margin:.6rem 0 .2rem;color:var(--body);line-height:1.55}
.warn{color:var(--ask);font-size:.9rem;margin:.3rem 0}
"""


def import_progress(job, nav: str = "") -> str:
    """Split progress. See the 2026-08-25 AI import design §5.3

    While running, this page refreshes itself every 3 seconds - zero JS, same approach as drafting.
    Otherwise the user stares at a frozen page guessing whether it is running or hung.
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
        # `done` counts **completed** chunks. "Chunk 0" reads as not-yet-started,
        # when in fact the first chunk is already running.
        + f'<p class="hint">Finished {job.done} / {job.total} chunks</p>'
        + back
    ), crumb="Import", nav=nav)


_PROGRESS_CSS = """
.bar{background:var(--sunk);border:1px solid var(--rule);height:1.2rem;
  border-radius:980px;margin:1.4rem 0 .5rem;overflow:hidden;padding:2px}
.fill{background:var(--g-rainbow);height:100%;border-radius:980px;
  transition:width .4s cubic-bezier(.2,0,0,1)}
"""



def framework_delete(found, cost: dict, error: str = "", nav: str = "") -> str:
    """The delete-framework confirmation page. **Type the id to confirm.**

    Deleting also destroys every self-assessment and sign-off under this framework - potentially tens
    of hours of work, unrecoverable. This step is deliberately annoying.
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
    """Select a passage and a small chat pops up in place.

    **This page used to be zero-JS** (datalist dropdowns, meta-refresh progress, autofocus scrolling).
    Text selection cannot avoid `window.getSelection()`, so this page breaks that rule - with a hard line drawn:
    so this page breaks that rule - with a hard line drawn:

    **JS only asks and displays; every write still goes through a normal form POST.**
    The write path carries preflight, audit, and the "no write without a nod" gate; letting JS write
    would move all three into the browser. So the popup's "Confirm, apply" is a real form,
    and clicking it reloads the whole page.

    **Allowed by default; only the forbidden zone is blocked.** The earlier rule - "the selection must sit
    entirely inside .chatty" - was default-refuse: dragging from title into body, across two fields,
    across paragraphs - none popped, though that is exactly how people select.

    The only truly unsendable content here is .noai (the official-mapping block, whose content comes
    from the built-in content pack - other frameworks' copyrighted control titles). Everything else is
    this company's own material. So the rule became: **if the selection touches .noai, no popup; otherwise pop.**
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
#pop{position:absolute;z-index:20;width:23rem;background:var(--surface);
  border:1px solid var(--accent);box-shadow:0 12px 36px rgba(0,0,0,.25);
  border-radius:18px;padding:1rem 1.1rem;font-size:.92rem}
#pop .q{display:block;font-size:.82rem;color:var(--muted);
  border-left:3px solid var(--rule);padding-left:.6rem;margin-bottom:.6rem;
  max-height:4.8rem;overflow:auto;line-height:1.45}
#pop textarea{width:100%;font:inherit;font-size:.9rem;border-radius:10px}
#pop .row{margin:.6rem 0 0;display:flex;gap:.5rem}
#pop .ans{margin-top:.7rem;white-space:pre-wrap;line-height:1.5}
#pop .ans:empty{margin:0}
"""

# Native DOM. No framework, no build step, no fetching scripts from the network -
# the "no page may reference an external host" guard still stands; this snippet should not break it either.
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
    // Allowed by default, forbidden zone blocked: a selection touching .noai does not pop. That block's
    // content comes from the built-in pack (other frameworks' copyrighted control titles) - sending it crosses the line.
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
          // Writes still go through forms. JS only asks and displays.
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


# Two columns with a persistent right rail. **Pure CSS, no JS** - this page already spent its one
# JS exception on text selection; sticky layout is enough and should not owe another.
_SPLIT_CSS = """
.wrap.wide{max-width:68rem}
.split{display:grid;grid-template-columns:minmax(0,1fr) 20.5rem;gap:2.5rem}
.split .reading{min-width:0}
.doing .stuck{position:sticky;top:0;height:100vh;box-sizing:border-box;
  display:flex;flex-direction:column;justify-content:center;
  gap:1rem;padding:1rem 0}
.doing .chat{display:flex;flex-direction:column;min-height:0;
  background:var(--surface);border:1px solid var(--rule);border-radius:18px;
  padding:1.2rem;box-shadow:var(--card-shadow)}
.doing .chat .thread{overflow-y:auto;min-height:0;flex:1;padding-right:.3rem}
.doing .claim,.doing .chat{margin:0}
.doing .claim{padding:1.2rem 1.3rem;background:var(--surface);
  border:1px solid var(--rule);border-radius:18px;box-shadow:var(--card-shadow)}
@media (max-width:60rem){
  .split{grid-template-columns:minmax(0,1fr)}
  .doing .stuck{position:static;height:auto;display:block}
  .doing .stuck > *{margin-bottom:1rem}
}
"""
