"""发布页的外壳。占位符用注释标记，不用 str.format——CSS 里全是花括号。"""

from framework_reader.publish.theme import THEME_CSS

PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CSF and ISO 27002 - control interpretations</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Spectral:ital,wght@0,400;0,600;1,400&display=swap">
<style>
<!--THEME-->
body{
  margin:0; background:var(--ground); color:var(--body);
  font-family:var(--han); font-size:16px; line-height:1.75;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:53rem;margin:0 auto;padding:0 1.25rem 6rem}

/* ---- 报头 ---- */
.masthead{padding:4.5rem 0 2rem;border-bottom:2px solid var(--ink)}
.eyebrow{
  font-family:var(--mono);font-size:.7rem;letter-spacing:.16em;
  text-transform:uppercase;color:var(--accent);margin:0 0 1rem
}
h1{
  font-family:var(--han);font-weight:600;font-size:clamp(2rem,5vw,3rem);
  line-height:1.15;color:var(--ink);margin:0;text-wrap:balance;letter-spacing:-.01em
}
.sub{margin:1rem 0 0;max-width:38rem;color:var(--muted)}
.count{font-family:var(--mono);color:var(--ink);font-weight:600}

/* ---- 前言 ---- */
.colophon{
  margin:2rem 0 0;padding:1.25rem 1.4rem;background:var(--sunk);
  border-left:3px solid var(--accent);font-size:.9rem;line-height:1.7
}
.colophon p{margin:0 0 .6rem}
.colophon p:last-child{margin:0}
.colophon strong{color:var(--ink)}

/* ---- 框架切换 ---- */
.tabs{display:flex;gap:.5rem;flex-wrap:wrap;margin:1.75rem 0 .9rem}
.tab{
  font:inherit;font-size:.95rem;padding:.5rem 1rem;cursor:pointer;
  color:var(--body);background:transparent;
  border:1px solid var(--rule);border-radius:980px;
  transition:all .2s cubic-bezier(.2,0,0,1)
}
.tab span{font-family:var(--mono);font-size:.72rem;color:var(--muted);margin-left:.4rem}
.tab:hover{border-color:var(--accent);background:var(--accent-soft)}
.tab[aria-pressed="true"]{
  background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600
}
.tab[aria-pressed="true"] span{color:rgba(255,255,255,.72)}
.tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.fwnote{margin:0;font-size:.84rem;color:var(--muted);max-width:44rem}
.fwnote strong{color:var(--ink)}

/* ---- 工具条 ---- */
.tools{
  position:sticky;top:0;z-index:5;background:var(--ground);
  padding:1rem 0;border-bottom:1px solid var(--rule);
  display:flex;gap:.75rem;flex-wrap:wrap;align-items:center
}
#q{
  flex:1 1 14rem;min-width:0;padding:.55rem .9rem;font:inherit;font-size:.9rem;
  color:var(--ink);background:var(--surface);
  border:1px solid var(--rule);border-radius:980px
}
#q:focus{outline:2px solid var(--accent);outline-offset:1px}
.chips{display:flex;gap:.4rem;flex-wrap:wrap}
.chip{
  font:inherit;font-size:.82rem;padding:.45rem .8rem;cursor:pointer;
  color:var(--body);background:var(--surface);
  border:1px solid var(--rule);border-radius:980px;
  transition:all .2s cubic-bezier(.2,0,0,1)
}
.chip span{font-family:var(--mono);font-size:.72rem;color:var(--muted);margin-left:.35rem}
.chip:hover{border-color:var(--accent);background:var(--accent-soft)}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
.chip[aria-pressed="true"] span{color:rgba(255,255,255,.72)}
.chip:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

/* ---- 条款 ---- */
.ctl{
  background:var(--surface);border:1px solid var(--rule);border-radius:18px;
  box-shadow:0 2px 10px rgba(0,0,0,.06);
  margin:1.5rem 0 0;padding:1.5rem 1.6rem
}
.ctl header{
  display:flex;gap:.9rem;align-items:baseline;flex-wrap:wrap;
  padding-bottom:.9rem;margin-bottom:1.1rem;border-bottom:1px solid var(--rule)
}
.cid{
  font-family:var(--mono);font-weight:600;font-size:1.05rem;color:var(--accent);
  font-variant-numeric:tabular-nums
}
.en{font-family:var(--serif);font-size:.95rem;color:var(--muted);flex:1 1 18rem}
.field{margin:0 0 1.35rem}
.field:last-child{margin-bottom:0}
.field h4{
  font-size:.75rem;font-weight:600;letter-spacing:.1em;color:var(--muted);
  margin:0 0 .45rem;text-transform:none
}
.field p{margin:0;max-width:62ch}
.field ul{margin:0;padding-left:1.1rem;max-width:62ch}
.field li{margin:0 0 .3rem}
.field li:last-child{margin:0}
.rungs{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.5rem}
.rungs li{
  display:flex;gap:.7rem;align-items:baseline;max-width:62ch;
  padding-left:.9rem;border-left:2px solid var(--accent-soft)
}
.rungs li:nth-child(2){border-left-color:color-mix(in srgb,var(--accent) 55%,var(--accent-soft))}
.rungs li:nth-child(3){border-left-color:var(--accent)}
.rung{
  font-family:var(--mono);font-size:.72rem;color:var(--accent);
  white-space:nowrap;flex:0 0 auto
}
.ctl [data-ask] h4{color:var(--ask)}
.mapping{padding-top:1.1rem;border-top:1px dashed var(--rule)}
.mapping ul{
  list-style:none;padding:0;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:.2rem .9rem;max-width:none
}
.mapping code{font-family:var(--mono);font-size:.8rem;color:var(--accent);margin-right:.4rem}
.mapping li{font-size:.85rem;color:var(--muted)}
.src{font-size:.78rem;color:var(--muted);margin-top:.7rem !important}
.empty{padding:3rem 0;color:var(--muted);text-align:center}
footer{margin-top:3rem;padding-top:1.5rem;border-top:1px solid var(--rule);
  font-size:.8rem;color:var(--muted)}
@media (max-width:640px){
  .ctl{padding:1.15rem 1.1rem}
  .masthead{padding-top:2.5rem}
}
@media (prefers-reduced-motion:reduce){*{transition:none !important}}
</style>

<div class="wrap">
<header class="masthead">
  <p class="eyebrow">NIST CSF 2.0 · ISO/IEC 27002:2022</p>
  <h1>Control interpretations</h1>
  <p class="sub"><span class="count"><!--TOTAL--></span> controls, each with seven fields:
  what it defends against, plain words, how to implement at three levels, what serves
  as evidence, common misconceptions, what auditors will probe, regional notes.
  Written for people preparing audit materials.</p>

  <div class="tabs"><!--TABS--></div>
  <!--NOTES-->

  <div class="colophon">
    <p><strong>Status</strong>: interpretations are drafted by AI, <strong>not yet
    confirmed control by control</strong>.
    Use them to understand the controls and prepare materials, <strong>do not hand
    them over as audit evidence as-is</strong>.</p>
    <p><strong>Source text</strong>: this page does not reproduce any copyrighted
    standard text. CSF 2.0 is published by NIST and in the public domain, so control
    numbers and English titles are reproduced as-is; standards that must be purchased,
    such as ISO and PCI, are not quoted here at all.</p>
    <p><strong>Mappings</strong>: the 800-53 mapping at the end of each control comes
    from NIST's official mapping files, traceable line by line. Derived mappings with
    unknown accuracy are never included.</p>
  </div>
</header>

<div class="tools">
  <input id="q" type="search" placeholder="Search control id, English title, or text…" aria-label="Search">
  <!--CHIPS-->
</div>

<main id="list"><!--ENTRIES--></main>
<p class="empty" id="empty" hidden>No matching controls.</p>

<footer>
  <p>Content is our own writing - interpretations, not official NIST publications,
  and with no affiliation to NIST.</p>
</footer>
</div>

<script>
(function(){
  var q=document.getElementById("q"),
      list=document.getElementById("list"),
      empty=document.getElementById("empty"),
      items=[].slice.call(list.querySelectorAll(".ctl")),
      tabs=[].slice.call(document.querySelectorAll(".tab")),
      chipsets=[].slice.call(document.querySelectorAll(".chips")),
      notes=[].slice.call(document.querySelectorAll(".fwnote")),
      fw=tabs.length?tabs[0].dataset.fw:"",
      fn="";
  function apply(){
    var t=q.value.trim().toLowerCase(),shown=0;
    items.forEach(function(el){
      var okFw=!fw||el.dataset.fw===fw,
          okFn=!fn||el.dataset.fn===fn,
          okQ=!t||el.dataset.q.toLowerCase().indexOf(t)>-1||
              el.textContent.toLowerCase().indexOf(t)>-1;
      var on=okFw&&okFn&&okQ;
      el.hidden=!on; if(on)shown++;
    });
    empty.hidden=shown>0;
  }
  q.addEventListener("input",apply);
  tabs.forEach(function(tab){
    tab.addEventListener("click",function(){
      fw=tab.dataset.fw; fn="";
      tabs.forEach(function(o){
        o.setAttribute("aria-pressed",String(o===tab));
      });
      chipsets.forEach(function(g){
        g.hidden=g.dataset.fw!==fw;
        [].slice.call(g.querySelectorAll(".chip")).forEach(function(c){
          c.setAttribute("aria-pressed","false");
        });
      });
      notes.forEach(function(n){n.hidden=n.dataset.fw!==fw});
      apply();
    });
  });
  [].slice.call(document.querySelectorAll(".chip")).forEach(function(c){
    c.setAttribute("aria-pressed","false");
    c.addEventListener("click",function(){
      var was=c.getAttribute("aria-pressed")==="true",
          siblings=[].slice.call(c.parentNode.querySelectorAll(".chip"));
      siblings.forEach(function(o){o.setAttribute("aria-pressed","false")});
      c.setAttribute("aria-pressed",was?"false":"true");
      fn=was?"":c.dataset.fn;
      apply();
    });
  });
})();
</script>
"""

PAGE = PAGE.replace("<!--THEME-->", THEME_CSS)
