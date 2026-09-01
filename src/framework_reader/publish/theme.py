"""设计令牌。发布页与本地 Web 壳共用——分两处写，两处就会慢慢长得不一样。"""

THEME_CSS = """:root{
  --ground:#F1F4F3; --surface:#FFFFFF; --sunk:#E7ECEA;
  --ink:#16202A; --body:#2B3942; --muted:#6B7A80;
  --rule:#D2DAD8; --accent:#1F4E5F; --accent-soft:#DCE8E9; --ask:#A6462F;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --serif:Spectral,"Songti SC",Georgia,serif;
  --han:"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Source Han Sans SC",system-ui,sans-serif;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#0E1518; --surface:#151E22; --sunk:#1B262A;
    --ink:#E6ECEA; --body:#C3CFCE; --muted:#8698A0;
    --rule:#28353A; --accent:#77B6BC; --accent-soft:#1D3237; --ask:#D98A72;
  }
}
:root[data-theme="dark"]{
  --ground:#0E1518; --surface:#151E22; --sunk:#1B262A;
  --ink:#E6ECEA; --body:#C3CFCE; --muted:#8698A0;
  --rule:#28353A; --accent:#77B6BC; --accent-soft:#1D3237; --ask:#D98A72;
}
*{box-sizing:border-box}
/* .chips 是 flex，会盖掉 hidden 自带的 display:none —— 两组筛选会同时显示。 */
[hidden]{display:none !important}
"""
