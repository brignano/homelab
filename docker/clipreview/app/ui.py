"""Shared look for the review pages: brignano/design tokens, no colour of our own.

design-tokens.css and Geist come from docker/dashboard/assets (mounted read-only
at /srv/assets by the compose file), so `scripts/update-design-tokens.sh` is the
only place they are updated. Status colours follow the system's one-hue-one-
meaning rule: cobalt = act, forest = settled, rust = gone wrong, larch = wants
you. Nothing here picks a hex.
"""

ASSET_TYPES = {".css": "text/css; charset=utf-8", ".woff2": "font/woff2",
               ".txt": "text/plain; charset=utf-8"}

CSS = r"""
@font-face{font-family:"Geist";src:url("/assets/fonts/geist-latin-wght-normal.woff2") format("woff2");
  font-weight:100 900;font-style:normal;font-display:swap}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;display:flex;flex-direction:column;height:100dvh;background:var(--bg);color:var(--ink);
  font:var(--text-base)/1.45 var(--sans);-webkit-text-size-adjust:100%}
a{color:var(--i-ink);text-decoration:none}
a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--i-ink);outline-offset:2px}
header{display:flex;gap:var(--s3);align-items:center;flex-wrap:wrap;padding:var(--s3) var(--s4);
  background:var(--card);border-bottom:1px solid var(--line)}
header h1{font-size:var(--text-lg);margin:0;font-weight:650;letter-spacing:-.01em}
nav{display:flex;gap:var(--s1)}
nav a{padding:var(--s1) var(--s3);border-radius:var(--radius-sm);color:var(--ink-soft);font-weight:500}
nav a[aria-current]{background:var(--surface-active);color:var(--ink)}
nav a:hover{background:var(--surface-hover);text-decoration:none}
.grow{flex:1}
.muted,small{color:var(--slate);font-size:var(--text-sm)}
select,input[type=search]{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line-control);
  border-radius:var(--radius-sm);padding:var(--s1) var(--s3);min-height:2.25rem}
label{display:inline-flex;gap:var(--s2);align-items:center;color:var(--ink-soft);font-size:var(--text-sm)}
main{flex:1;min-height:0;display:flex;gap:var(--s4);padding:var(--s4)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:var(--s4)}
footer{display:flex;gap:var(--s2);align-items:center;flex-wrap:wrap;padding:var(--s3) var(--s4);
  background:var(--card);border-top:1px solid var(--line)}
button{font:inherit;font-weight:550;min-height:2.5rem;padding:var(--s2) var(--s4);border-radius:var(--radius-sm);
  color:var(--ink);background:var(--card);border:1px solid var(--line-control);cursor:pointer;
  display:inline-flex;gap:var(--s2);align-items:center;transition:background var(--dur-fast) var(--ease)}
button:hover{background:var(--surface-hover)}
button:active{background:var(--surface-active)}
button:disabled{color:var(--i-disabled-ink);background:var(--i-disabled-bg);cursor:default}
button kbd{font:var(--text-xs) var(--mono);color:var(--slate);border:1px solid var(--line);border-radius:4px;padding:0 var(--s1)}
button.primary{background:var(--i);color:var(--on-i);border-color:transparent}
button.primary:hover{background:var(--i-hover)}
button.primary kbd{color:inherit;border-color:currentColor;opacity:.8}
button.on{box-shadow:inset 0 0 0 2px var(--i-ink)}
.chip{display:inline-flex;align-items:center;padding:0 var(--s3);min-height:1.5rem;border-radius:999px;
  font-size:var(--text-sm);font-weight:550;border:1px solid var(--line);background:var(--surface-hover);color:var(--ink-soft)}
.chip.send,.chip.keep{background:var(--success-surface);border-color:var(--success-line);color:var(--success-ink)}
.chip.fall{background:var(--danger-surface);border-color:var(--danger-line);color:var(--danger-ink)}
.chip.unsure{background:var(--attention-surface);border-color:var(--attention-line);color:var(--attention-ink)}
.chip.cold{background:var(--info-surface);border-color:var(--info-line);color:var(--info-ink)}
.warn{color:var(--danger-ink)}
.sep{width:1px;align-self:stretch;background:var(--line);margin:0 var(--s2)}
@media (max-width:760px){
  header,footer{padding:var(--s2) var(--s3)}
  main{flex-direction:column;padding:var(--s3);overflow:auto}
  button{flex:1 1 auto;justify-content:center}
  button kbd{display:none}
}
"""

HEAD = ('<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="stylesheet" href="/assets/design-tokens.css">'
        '<style>' + CSS + '</style>')


def nav(active: str) -> str:
    def a(href, label, key):
        return '<a href="%s"%s>%s</a>' % (href, ' aria-current="page"' if key == active else "", label)
    return "<nav>" + a("/", "Videos", "videos") + a("/dupes", "Duplicate photos", "dupes") + "</nav>"
