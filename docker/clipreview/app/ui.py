"""Shared look for the review pages: brignano/design tokens, no colour of our own.

design-tokens.css and Geist come from docker/dashboard/assets (mounted read-only
at /srv/assets by the compose file), so `scripts/update-design-tokens.sh` is the
only place they are updated. Status colours follow the system's one-hue-one-
meaning rule: cobalt = act, forest = settled, rust = gone wrong, larch = wants
you. Nothing here picks a hex.
"""

from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
ICON_FILES = {
    "favicon.svg": "image/svg+xml", "favicon.ico": "image/x-icon", "favicon-32.png": "image/png",
    "apple-touch-icon.png": "image/png", "icon-192.png": "image/png", "icon-512.png": "image/png",
    "icon-maskable-512.png": "image/png", "site.webmanifest": "application/manifest+json",
}
ICON_ROUTES = {"/favicon.ico": "favicon.ico", "/apple-touch-icon.png": "apple-touch-icon.png",
               "/apple-touch-icon-precomposed.png": "apple-touch-icon.png",
               "/site.webmanifest": "site.webmanifest"}

# The mark, inked in the identity colour (--mark): a graphic, never a control.
LOGO = ('<svg class="logo" viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">'
        '<rect x="4.2" y="4.2" width="17.6" height="15.6" rx="1.8" fill="none" stroke="currentColor" stroke-width="2.4"/>'
        '<rect x="10" y="12" width="18" height="16" rx="1.8" fill="currentColor"/></svg>')

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
.brand{display:inline-flex;gap:var(--s2);align-items:center;color:var(--ink);text-decoration:none}
.brand:hover{text-decoration:none}
.logo{color:var(--mark);flex:none}
nav{display:flex;gap:var(--s1)}
nav a{padding:var(--s1) var(--s3);border-radius:var(--radius-sm);color:var(--ink-soft);font-weight:500}
nav a[aria-current]{background:var(--surface-active);color:var(--ink)}
nav a:hover{background:var(--surface-hover);text-decoration:none}
.grow{flex:1}
footer small.grow{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.exports{display:inline-flex;gap:var(--s2);align-items:center}
.exports a{padding:var(--s1) var(--s3);border:1px solid var(--line);border-radius:var(--radius-sm);color:var(--ink-soft);white-space:nowrap}
.exports a:hover{background:var(--surface-hover);text-decoration:none}
.muted,small{color:var(--slate);font-size:var(--text-sm)}
select,input[type=search]{font:inherit;font-weight:500;color:var(--ink);background-color:var(--card);border:1px solid var(--line-control);
  border-radius:var(--radius-sm);padding:var(--s1) var(--s3);min-height:2.5rem}
/* one chevron for every select, in the same colour as the muted text, instead of the OS widget */
select{appearance:none;-webkit-appearance:none;padding-right:2.25rem;cursor:pointer;
  background-image:linear-gradient(45deg,transparent 50%,var(--slate) 50%),linear-gradient(135deg,var(--slate) 50%,transparent 50%);
  background-position:calc(100% - 1.15rem) 52%,calc(100% - .8rem) 52%;background-size:.35rem .35rem;background-repeat:no-repeat}
select:hover,input[type=search]:hover{background-color:var(--surface-hover)}
select:focus-visible,input:focus-visible,button:focus-visible,nav a:focus-visible{outline:2px solid var(--i-ink);outline-offset:2px}
input[type=checkbox]{accent-color:var(--i);width:1.1rem;height:1.1rem;margin:0}
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
@media (pointer:coarse){button,select,input[type=search],nav a{min-height:44px}
  label{min-height:44px}input[type=checkbox]{width:1.25rem;height:1.25rem}}
@media (max-width:760px){
  header,footer{padding:var(--s2) var(--s3)}
  header{gap:var(--s2)}
  nav{order:3;width:100%}nav a{flex:1;text-align:center}
  main{flex-direction:column;padding:var(--s3);overflow:auto}
  button{flex:1 1 auto;justify-content:center}
  button kbd,footer .hint,footer small.grow{display:none}
}
/* date filter: year dropdown, month chips, previous / next */
.mf{display:flex;gap:var(--s2);align-items:center;flex-wrap:wrap}
.mf .step{padding:0;min-width:2.25rem;justify-content:center}
.mf .months{display:flex;gap:var(--s1);flex-basis:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:2px}
.mf .months[hidden]{display:none}
.mf .months button{flex:0 0 auto;min-width:3rem;justify-content:center}
.mf .months button[aria-pressed=true]{background:var(--i);border-color:transparent;color:var(--on-i)}
.mf .months button:disabled{opacity:.35;cursor:default}
@media (max-width:760px){.mf{width:100%}.mf select{flex:1}.mf .step{flex:0 0 3rem}.mf .months button{flex:1 0 auto}}
@supports (padding:max(0px)){footer{padding-bottom:max(var(--s3),env(safe-area-inset-bottom))}}
"""

HEAD = ('<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="theme-color" content="#111114" media="(prefers-color-scheme: dark)">'
        '<meta name="theme-color" content="#fafafb" media="(prefers-color-scheme: light)">'
        '<meta name="color-scheme" content="light dark">'
        '<link rel="icon" href="/icons/favicon.svg" type="image/svg+xml">'
        '<link rel="icon" href="/icons/favicon-32.png" type="image/png" sizes="32x32">'
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
        '<link rel="manifest" href="/site.webmanifest">'
        '<link rel="stylesheet" href="/assets/design-tokens.css">'
        '<style>' + CSS + '</style>')


def brand() -> str:
    return '<a class="brand" href="/" aria-label="Review, home">' + LOGO + "<h1>Review</h1></a>"


def nav(active: str) -> str:
    def a(href, label, key):
        return '<a href="%s"%s>%s</a>' % (href, ' aria-current="page"' if key == active else "", label)
    return "<nav>" + a("/", "Videos", "videos") + a("/dupes", "Duplicate photos", "dupes") + "</nav>"


MF_HTML = ('<div class="mf" id="mf"><select id="mfy" aria-label="Year"></select>'
           '<button type="button" class="step" id="mfp" aria-label="Previous month">&#8249;</button>'
           '<button type="button" class="step" id="mfn" aria-label="Next month">&#8250;</button>'
           '<div class="months" id="mfm" role="group" aria-label="Month" hidden></div></div>')

# Shared by both pages. MF.set(['2017-08', ...]) lists one entry per item, MF.match(month)
# says whether an item passes the filter, MF.cb runs after every change. MF.val is
# '' (everything), 'YYYY', or 'YYYY-MM'.
MF_JS = r"""
const MF=(()=>{
  const $=id=>document.getElementById(id), N=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const o={val:'',cb:()=>{},counts:{},total:0};
  const yearOf=()=>o.val.slice(0,4), sorted=()=>Object.keys(o.counts).sort();
  o.match=m=>!o.val||m===o.val||(m||'').startsWith(o.val+'-');
  o.label=()=>o.val.length===7?N[+o.val.slice(5)-1]+' '+o.val.slice(0,4):o.val||'All dates';
  o.set=months=>{
    o.counts={};o.total=0;
    for(const m of months)if(m){o.counts[m]=(o.counts[m]||0)+1;o.total++}
    const ys=[...new Set(sorted().map(m=>m.slice(0,4)))].reverse();
    const yc=y=>sorted().filter(m=>m.startsWith(y)).reduce((a,m)=>a+o.counts[m],0);
    if(o.val&&!ys.includes(yearOf()))o.val='';
    $('mfy').innerHTML='<option value="">All dates ('+o.total+')</option>'+ys.map(y=>`<option value="${y}">${y} (${yc(y)})</option>`).join('');
    draw();
  };
  function draw(){
    const y=yearOf(), box=$('mfm'), ms=sorted();
    $('mfy').value=y;
    box.hidden=!y;
    box.innerHTML=y?N.map((n,k)=>{
      const m=y+'-'+String(k+1).padStart(2,'0'), c=o.counts[m]||0;
      return `<button type="button" data-m="${m}" aria-pressed="${o.val===m}" ${c?'':'disabled'} title="${c} in ${n} ${y}">${n}</button>`}).join(''):'';
    const at=ms.indexOf(o.val.length===7?o.val:'');
    $('mfp').disabled=!ms.length||(o.val.length===7&&at<=0);
    $('mfn').disabled=!ms.length||(o.val.length===7&&at>=ms.length-1);
  }
  function put(v){o.val=v;draw();o.cb()}
  o.clear=()=>{if(o.val)put('')};
  function step(d){
    const ms=sorted(); if(!ms.length)return;
    if(o.val.length===7){const k=ms.indexOf(o.val)+d; if(k>=0&&k<ms.length)put(ms[k]);return}
    const pool=o.val?ms.filter(m=>m.startsWith(yearOf())):ms;
    put(pool[d>0?0:pool.length-1]);
  }
  $('mfy').onchange=e=>put(e.target.value);
  $('mfm').onclick=e=>{const b=e.target.closest('button[data-m]'); if(b&&!b.disabled)put(o.val===b.dataset.m?yearOf():b.dataset.m)};
  $('mfp').onclick=()=>step(-1);$('mfn').onclick=()=>step(1);
  addEventListener('keydown',e=>{if(e.key==='Escape'&&o.val&&!e.target.closest('input,textarea'))o.clear()});
  return o;
})();
"""
