"""Design tokens. Shared by the publish page and the local web shell - written twice, the two drift."""

THEME_CSS = """:root{
  --g-blue:#1a73e8; --g-red:#ea4335; --g-yellow:#fbbc04; --g-green:#34a853;
  --gemini-gradient:linear-gradient(135deg,#4285f4 0%,#9b72cb 35%,#d96570 70%,#fbbc04 100%);
  --g-rainbow:linear-gradient(90deg,#4285F4 0% 25%,#EA4335 25% 50%,#FBBC05 50% 75%,#34A853 75% 100%);
  --ground:#F8F9FA; --surface:#FFFFFF; --sunk:#EDF2FA; --surface-high:#F1F3F4;
  --ink:#1F1F1F; --body:#3C4043; --muted:#5F6368;
  --rule:#DADCE0; --accent:#1A73E8; --accent-soft:#E8F0FE; --ask:#D93025;
  --success:#1E8E3E; --success-soft:#E6F4EA;
  --mono:"Google Sans Mono","Roboto Mono","SF Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --serif:"Google Sans","Product Sans",Georgia,serif;
  --han:"Google Sans","Google Sans Text","Product Sans",system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#0D0F12; --surface:#1A1C22; --sunk:#14161A; --surface-high:#22252C;
    --ink:#F1F3F4; --body:#BDC1C6; --muted:#80868B;
    --rule:#2A2D35; --accent:#8AB4F8; --accent-soft:#17263C; --ask:#F28B82;
    --success:#81C995; --success-soft:#132B1C;
  }
}
:root[data-theme="dark"]{
  --ground:#0D0F12; --surface:#1A1C22; --sunk:#14161A; --surface-high:#22252C;
  --ink:#F1F3F4; --body:#BDC1C6; --muted:#80868B;
  --rule:#2A2D35; --accent:#8AB4F8; --accent-soft:#17263C; --ask:#F28B82;
  --success:#81C995; --success-soft:#132B1C;
}
*{box-sizing:border-box}
/* .chips is flex, which overrides hidden's display:none - both filter groups would show at once. */
[hidden]{display:none !important}
"""
