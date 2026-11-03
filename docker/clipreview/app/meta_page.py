from . import ui

PAGE = r"""<!doctype html>
<html lang="en"><head><title>Cold storage · Review</title>
__HEAD__
<style>
[hidden]{display:none!important}
main{display:block;overflow:auto;padding:0;scroll-behavior:smooth}
.wrap{max-width:1500px;margin:0 auto;padding:var(--s4)}
.lead{margin:0 0 var(--s3);color:var(--slate);font-size:var(--text-sm)}

/* search: one row of controls, one height, one radius */
#f{display:grid;grid-template-columns:repeat(auto-fit,minmax(9.5rem,1fr));gap:var(--s3);margin-bottom:var(--s4);
  padding:var(--s4);background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow-1)}
#f label{display:flex;flex-direction:column;align-items:stretch;gap:var(--s1);font-size:var(--text-xs);font-weight:600;
  color:var(--slate);text-transform:uppercase;letter-spacing:.04em;min-width:0}
#f input:not([type=checkbox]),#f select{width:100%;min-height:2.5rem;font:inherit;font-size:var(--text-base);font-weight:500;
  text-transform:none;letter-spacing:0;color:var(--ink);background-color:var(--bg);border:1px solid var(--line-control);
  border-radius:var(--radius-sm);padding:0 var(--s3)}
#f select{padding-right:2.25rem}
#f input::placeholder{color:var(--slate);opacity:.7}
#f .wide{grid-column:span 2}
#f .tog{flex-direction:row;align-items:center;gap:var(--s2);align-self:end;min-height:2.5rem;font-size:var(--text-sm);
  font-weight:550;text-transform:none;letter-spacing:0;color:var(--ink-soft)}
#f .actions{grid-column:1/-1;display:flex;gap:var(--s2);align-items:center;flex-wrap:wrap}
#f .actions a{margin-left:auto;font-size:var(--text-sm)}

/* people rail */
#rail{display:flex;gap:var(--s2);overflow-x:auto;padding:0 0 var(--s2);-webkit-overflow-scrolling:touch;scrollbar-width:thin}
.pp{flex:0 0 auto;display:inline-flex;align-items:center;gap:var(--s1);min-height:2.25rem;padding:0 var(--s1) 0 var(--s3);
  border:1px solid var(--line);border-radius:999px;background:var(--card);cursor:pointer;font-size:var(--text-sm);font-weight:550;
  transition:background var(--dur-fast) var(--ease),border-color var(--dur-fast) var(--ease)}
.pp:hover{background:var(--surface-hover);border-color:var(--line-control)}
.pp.on{background:var(--i);color:var(--on-i);border-color:transparent}
.pp small{color:inherit;opacity:.65;font-size:var(--text-xs)}
.pp.off{opacity:.7;border-style:dashed}
.xb{all:unset;box-sizing:border-box;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;
  min-width:1.75rem;height:1.75rem;margin-left:var(--s1);border-radius:999px;font-weight:700;font-size:var(--text-base);line-height:1}
.xb.hide{color:var(--danger-ink);background:var(--danger-surface);border:1px solid var(--danger-line)}
.xb.show{color:var(--success-ink);background:var(--success-surface);border:1px solid var(--success-line);padding:0 var(--s2);font-size:var(--text-xs)}
.xb:hover{filter:brightness(1.2)}
.xb:focus-visible{outline:2px solid var(--i-ink);outline-offset:1px}

/* results header */
.bar{display:flex;align-items:center;gap:var(--s3);flex-wrap:wrap;margin:var(--s3) 0 var(--s4)}
.bar h2{margin:0;font-size:var(--text-lg);font-weight:650;letter-spacing:-.01em}
.bar .sp{flex:1}
.bar button{min-height:2.25rem;font-size:var(--text-sm)}

/* grid: every tile the same shape, photos cover it (no bars) */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:var(--s3)}
.t{all:unset;box-sizing:border-box;position:relative;display:block;cursor:pointer;border-radius:var(--radius);overflow:hidden;
  background:var(--n-100);border:1px solid var(--line);aspect-ratio:4/3;box-shadow:var(--shadow-1);
  transition:transform var(--dur-fast) var(--ease),box-shadow var(--dur-fast) var(--ease),border-color var(--dur-fast) var(--ease)}
.t:hover{transform:translateY(-2px);border-color:var(--line-control);box-shadow:0 8px 24px rgb(0 0 0/.22)}
.t:focus-visible{outline:2px solid var(--i-ink);outline-offset:2px}
.t img,.t video{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}
.t .ph{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:var(--s1);
  color:var(--slate);font-size:var(--text-xs);text-align:center;padding:var(--s3);
  background:repeating-linear-gradient(135deg,transparent 0 10px,var(--surface-hover) 10px 20px)}
.t .ph b{font-size:1.6rem;font-weight:400;line-height:1}
.t .cap{position:absolute;left:0;right:0;bottom:0;padding:var(--s5,1.5rem) var(--s3) var(--s2);color:#fff;
  background:linear-gradient(to top,rgb(0 0 0/.78),rgb(0 0 0/0));pointer-events:none}
.t .cap .d{font-weight:650;font-size:var(--text-sm);text-shadow:0 1px 2px rgb(0 0 0/.5)}
.t .cap .p{font-size:var(--text-xs);opacity:.9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.t .fl{position:absolute;top:var(--s2);right:var(--s2);display:flex;gap:var(--s1);pointer-events:none}
.fl i{font-style:normal;font-size:var(--text-xs);font-weight:650;padding:1px var(--s2);border-radius:999px;color:#fff;background:rgb(0 0 0/.62);backdrop-filter:blur(4px)}
.fl i.del{background:rgb(160 30 30/.85)}
.empty{grid-column:1/-1;text-align:center;color:var(--slate);padding:var(--s6,3rem) 0}
#end{height:1px}

/* lightbox */
dialog{border:0;padding:0;background:transparent;max-width:none;max-height:none;width:100vw;height:100dvh;color:#fff}
dialog::backdrop{background:rgb(8 8 10/.9);backdrop-filter:blur(6px)}
.lb{display:grid;grid-template-columns:minmax(0,1fr) 22rem;height:100%}
.lb .stage{background:#0b0b0d;position:relative;display:flex;align-items:center;justify-content:center;min-width:0;padding:var(--s4)}
.lb .stage img,.lb .stage video{max-width:100%;max-height:100%;object-fit:contain;border-radius:var(--radius-sm)}
.lb .nope{color:#bbb;text-align:center;max-width:22rem}
.lb .side{overflow:auto;padding:var(--s5,1.5rem);background:var(--card);color:var(--ink);border-left:1px solid var(--line)}
.lb h3{margin:0 0 var(--s1);font-size:var(--text-lg);font-weight:650;word-break:break-all}
.lb dl{margin:var(--s4) 0 0;display:grid;gap:var(--s3)}
.lb dt{font-size:var(--text-xs);font-weight:600;color:var(--slate);text-transform:uppercase;letter-spacing:.04em;margin-bottom:var(--s1)}
.lb dd{margin:0;display:flex;flex-wrap:wrap;gap:var(--s1)}
.lb .c{all:unset;box-sizing:border-box;cursor:pointer;padding:2px var(--s3);border-radius:999px;border:1px solid var(--line);
  background:var(--surface-hover);font-size:var(--text-sm);font-weight:550;color:var(--ink-soft)}
.lb .c:hover{background:var(--surface-active);color:var(--ink)}
.lb .c:focus-visible{outline:2px solid var(--i-ink)}
.nav{position:absolute;top:50%;transform:translateY(-50%);width:3rem;height:3rem;border-radius:999px;padding:0;justify-content:center;
  background:rgb(20 20 24/.7);color:#fff;border:1px solid rgb(255 255 255/.18);font-size:1.4rem}
.nav:hover{background:rgb(40 40 46/.9)}
.nav.l{left:var(--s4)}.nav.r{right:var(--s4)}
.lb .close{position:absolute;top:var(--s3);left:var(--s3);background:rgb(20 20 24/.7);color:#fff;border-color:rgb(255 255 255/.18)}
.lb .acts{display:flex;gap:var(--s2);margin-top:var(--s5,1.5rem);flex-wrap:wrap}
@media (max-width:860px){
  .wrap{padding:var(--s3)}
  #f{padding:var(--s3)}#f .wide{grid-column:1/-1}
  .grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--s2)}
  .lb{grid-template-columns:1fr;grid-template-rows:minmax(0,1fr) auto}
  .lb .side{max-height:42dvh;border-left:0;border-top:1px solid var(--line)}
  .nav{width:2.5rem;height:2.5rem}.nav.l{left:var(--s2)}.nav.r{right:var(--s2)}
}
</style></head><body>
<header>__BRAND____NAV__<span id="count" class="muted"></span></header>
<main id="scroll"><div class="wrap">
<p class="lead">What Photos knew about each photo and clip, kept on the drive: who was in it, where, what it showed, albums and dates. It includes items already deleted from iCloud.</p>
<form id="f" autocomplete="off">
 <label class="wide">Search<input name="text" placeholder="Anything: a place, a label, a file name" type="search"></label>
 <label>Person<input name="person" list="dl-person" placeholder="Dakota, Julia"></label>
 <label>Thing<input name="label" list="dl-label" placeholder="beach, dog, climbing"></label>
 <label>Place<input name="place" placeholder="Boston"></label>
 <label>Album<input name="album" list="dl-album"></label>
 <label>Year<select name="year"><option value="">Any year</option></select></label>
 <label>Month<select name="mon" disabled><option value="">Any month</option></select></label>
 <label>Kind<select name="kind"><option value="">All</option><option value="photo">Photos</option><option value="video">Clips</option></select></label>
 <label>Where<select name="where"><option value="">Everywhere</option><option value="library">In the library</option><option value="hidden">Hidden album</option><option value="trashed">Recently Deleted</option></select></label>
 <label class="tog"><input type="checkbox" name="fav" value="1"> Favourites only</label>
 <div class="actions"><button class="primary" type="submit">Search</button><button type="button" id="reset">Clear</button><a href="/api/meta/export.csv">Download everything as CSV</a></div>
</form>
<datalist id="dl-person"></datalist><datalist id="dl-label"></datalist><datalist id="dl-album"></datalist>
<div id="rail" aria-label="People"></div>
<div class="bar"><h2 id="res">Loading…</h2><span class="sp"></span><span id="hid" class="muted"></span><button type="button" id="tog" hidden></button></div>
<div class="grid" id="out"></div>
<div id="end"></div>
</div></main>
<dialog id="lb" aria-label="Photo details"><div class="lb">
 <div class="stage" id="stage"><button class="close" id="lbx" type="button">Close <kbd>Esc</kbd></button>
  <button class="nav l" id="lbp" type="button" aria-label="Previous">&#8249;</button><button class="nav r" id="lbn" type="button" aria-label="Next">&#8250;</button></div>
 <div class="side" id="side"></div></div></dialog>
<script>
const $=id=>document.getElementById(id), f=$('f');
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e};
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
let offset=0,total=0,loading=false,items=[],HID=[],showHid=false,FAC=[],gen=0;

// hidden people live on the server so every device agrees; "Lauren" also hides "Lauren <emoji>" (same rule as the server)
const low=x=>x.toLowerCase(), same=(h,n)=>low(h)===low(n)||low(n).startsWith(low(h)+' ');
const isHid=n=>HID.some(h=>same(h,n));
const saveHid=()=>fetch('/api/meta/hidden',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({people:HID})});
const fmt=d=>{if(!d)return 'No date';const [y,m,dd]=d.slice(0,10).split('-').map(Number);return MON[m-1]+' '+dd+', '+y};

function params(){
  const p=new URLSearchParams();
  for(const [k,v] of new FormData(f)){if(!v||k==='mon')continue;p.set(k,v)}
  const y=f.year.value,m=f.mon.value;if(y&&m)p.set('month',y+'-'+m),p.delete('year');
  if(HID.length&&!showHid)p.set('exclude',HID.join(','));
  return p;
}

function tile(r,i){
  const b=el('button','t');b.type='button';b.onclick=()=>openLb(i);
  b.setAttribute('aria-label',fmt(r.date)+' '+r.filename);
  if(r.viewable){
    const u='/meta/file/'+r.uuid;
    if(r.kind==='video'){const v=el('video');v.src=u+'#t=0.1';v.preload='metadata';v.muted=true;v.playsInline=true;b.appendChild(v)}
    else{const im=el('img');im.loading='lazy';im.decoding='async';im.alt='';im.src=u;b.appendChild(im)}
  }else{
    const ph=el('div','ph');ph.appendChild(el('b','',r.kind==='video'?'▶':'▣'));
    ph.appendChild(el('span','',r.mirror&&r.mirror.startsWith('ok')?'Original kept, no preview':'No copy kept'));b.appendChild(ph);
  }
  const fl=el('div','fl');
  if(r.kind==='video')fl.appendChild(el('i','','Clip'));
  if(r.fav)fl.appendChild(el('i','','♥'));
  if(r.trashed)fl.appendChild(el('i','del','Deleted'));
  if(r.hidden)fl.appendChild(el('i','','Hidden'));
  b.appendChild(fl);
  const cap=el('div','cap');cap.appendChild(el('div','d',fmt(r.date)));
  const who=[...r.persons,...(r.place?[r.place]:[])].join(' · ');
  if(who)cap.appendChild(el('div','p',who));
  b.appendChild(cap);return b;
}

async function load(reset){
  if(loading)return;loading=true;const g=++gen;
  if(reset){offset=0;items=[];$('out').textContent=''}
  const p=params();p.set('offset',offset);
  let d;try{d=await (await fetch('/api/meta/search?'+p)).json()}catch(e){loading=false;return}
  if(g!==gen){loading=false;return}
  for(const r of d.results){items.push(r);$('out').appendChild(tile(r,items.length-1))}
  offset+=d.results.length;total=d.total;
  $('res').textContent=total.toLocaleString()+(total===1?' result':' results');
  if(!total)$('out').appendChild(el('div','empty','Nothing matches. Try fewer filters, or show hidden people.'));
  document.title=(total.toLocaleString())+' found · Cold storage';
  loading=false;
  if(offset<total&&$('end').getBoundingClientRect().top<innerHeight*1.5)load(false);
}
const run=()=>load(true);

// rail of people: click to filter, red x hides, dashed ones are hidden (shown only when asked)
function drawRail(){
  const r=$('rail');r.textContent='';
  for(const [n,c] of FAC.filter(([n])=>showHid||!isHid(n)).slice(0,40)){
    const hid=isHid(n),b=el('span','pp'+(hid?' off':'')),on=low(f.person.value)===low(n);if(on)b.classList.add('on');
    b.tabIndex=0;b.setAttribute('role','button');
    b.appendChild(el('span','',n));b.appendChild(el('small','',c));
    const go=()=>{f.person.value=on?'':n;drawRail();run()};
    b.onclick=go;b.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go()}};
    const x=el('button','xb '+(hid?'show':'hide'),hid?'+ unhide':'×');x.type='button';
    x.title=hid?'Stop hiding '+n:'Hide '+n+' by default';x.setAttribute('aria-label',x.title);
    x.onclick=e=>{e.stopPropagation();
      if(hid)HID=HID.filter(h=>!same(h,n));else HID.push(n);
      saveHid();drawRail();drawHid();run()};
    b.appendChild(x);r.appendChild(b);
  }
  drawHid();
}
function drawHid(){
  $('hid').textContent=HID.length?HID.length+' hidden by default':'';
  const t=$('tog');t.hidden=!HID.length;t.textContent=showHid?'Hide them again':'Show hidden people';
}
$('tog').onclick=()=>{showHid=!showHid;drawRail();run()};

// lightbox
let cur=-1;
function openLb(i){cur=i;const r=items[i];if(!r)return;
  const st=$('stage');for(const n of [...st.children])if(!n.classList.contains('nav')&&n.id!=='lbx')n.remove();
  if(r.viewable){
    const u='/meta/file/'+r.uuid;
    if(r.kind==='video'){const v=el('video');v.src=u;v.controls=true;v.autoplay=true;v.playsInline=true;st.prepend(v)}
    else{const im=el('img');im.alt=r.filename;im.src=u;st.prepend(im)}
  }else st.prepend(el('div','nope',r.mirror&&r.mirror.startsWith('ok')?'The original is on the drive but has no browser preview. Open it in Jellyfin.':'No copy of this one was kept; only what Photos knew about it.'));
  const s=$('side');s.textContent='';
  s.appendChild(el('h3','',r.filename));s.appendChild(el('div','muted',fmt(r.date)+(r.kind==='video'?' · clip':'')));
  const dl=el('dl');
  const row=(k,vals,key)=>{if(!vals||!vals.length)return;const dt=el('dt','',k),dd=el('dd');
    for(const v of vals){const c=el('button','c',v);c.type='button';
      c.onclick=()=>{if(key){f[key].value=v;drawRail();run();$('lb').close()}};dd.appendChild(c)}
    dl.appendChild(dt);dl.appendChild(dd)};
  row('People',r.persons,'person');row('Things',r.labels,'label');row('Albums',r.albums,'album');
  row('Place',r.place?[r.place]:[],'place');row('Keywords',r.keywords,'text');
  const fl=[r.fav&&'Favourite',r.trashed&&'In Recently Deleted',r.hidden&&'Hidden album',r.mirror&&r.mirror.startsWith('ok')?'Original on the drive':'No copy on the drive'].filter(Boolean);
  row('Status',fl,null);s.appendChild(dl);
  if(r.viewable){const a=el('div','acts'),l=el('a','',r.kind==='video'?'Open the clip':'Open the original');l.href='/meta/file/'+r.uuid;l.target='_blank';l.rel='noopener';a.appendChild(l);s.appendChild(a)}
  $('lbp').hidden=i<=0;$('lbn').hidden=false;
  if(!$('lb').open)$('lb').showModal();
  if(i>=items.length-6&&offset<total)load(false);
}
$('lbx').onclick=()=>$('lb').close();
$('lbp').onclick=()=>openLb(cur-1);$('lbn').onclick=()=>openLb(Math.min(cur+1,items.length-1));
$('lb').addEventListener('close',()=>{for(const v of $('stage').querySelectorAll('video'))v.pause()});
$('lb').addEventListener('click',e=>{if(e.target===$('lb')||e.target===$('stage'))$('lb').close()});
addEventListener('keydown',e=>{if(!$('lb').open)return;if(e.key==='ArrowLeft'&&cur>0)openLb(cur-1);if(e.key==='ArrowRight'&&cur<items.length-1)openLb(cur+1)});

// controls: selects and the checkbox search at once, typing waits a moment
let t;const later=()=>{clearTimeout(t);t=setTimeout(run,350)};
f.onsubmit=e=>{e.preventDefault();run()};
for(const n of ['text','person','label','place','album'])f[n].addEventListener('input',()=>{later();if(n==='person')drawRail()});
for(const n of ['year','kind','where','fav'])f[n].addEventListener('change',()=>{
  if(n==='year'){f.mon.disabled=!f.year.value;if(!f.year.value)f.mon.value=''}run()});
f.mon.addEventListener('change',run);
$('reset').onclick=()=>{f.reset();f.mon.disabled=true;drawRail();run()};
new IntersectionObserver(es=>{if(es[0].isIntersecting&&!loading&&offset<total)load(false)},{root:$('scroll'),rootMargin:'600px'}).observe($('end'));

async function init(){
  try{HID=(await (await fetch('/api/meta/hidden')).json()).people||[]}catch(e){}
  const d=await (await fetch('/api/meta/facets')).json();
  $('count').textContent=d.total?d.total.toLocaleString()+' items · '+d.trashed.toLocaleString()+' in Recently Deleted':'nothing imported yet: run tools/photos/metadata-export.py on the Mac';
  for(const [k,id] of [['persons','dl-person'],['labels','dl-label'],['albums','dl-album']])
    for(const [n] of d[k]){const o=el('option');o.value=n;$(id).appendChild(o)}
  for(const [y,c] of d.years){const o=el('option','',y+' ('+c.toLocaleString()+')');o.value=y;f.year.appendChild(o)}
  MON.forEach((m,i)=>{const o=el('option','',m);o.value=String(i+1).padStart(2,'0');f.mon.appendChild(o)});
  FAC=d.persons;drawRail();run();
}
init();
</script></body></html>
""".replace("__HEAD__", ui.HEAD).replace("__BRAND__", ui.brand()).replace("__NAV__", ui.nav("meta"))
