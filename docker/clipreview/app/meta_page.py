from . import ui

PAGE = r"""<!doctype html>
<html lang="en"><head><title>Cold storage search · Review</title>
__HEAD__
<style>
main{overflow:auto;display:block;padding:var(--s3) var(--s4)}
form{display:grid;grid-template-columns:repeat(auto-fill,minmax(10rem,1fr));gap:var(--s3);align-items:end;margin-bottom:var(--s3)}
form label{display:flex;flex-direction:column;font-size:var(--text-xs);gap:2px}
form input:not([type=checkbox]),form select{width:100%;min-height:2.25rem}
form label.chk{flex-direction:row;align-items:center;gap:var(--s2);min-height:2.25rem;font-size:var(--text-sm)}
.actions{display:flex;gap:var(--s3);align-items:center;grid-column:1/-1}
#facets{font-size:var(--text-sm);margin:var(--s2) 0}
.chip{display:inline-flex;align-items:center;gap:6px;cursor:pointer;border:1px solid var(--line);border-radius:999px;padding:0 var(--s3);margin:2px;background:none;font-size:var(--text-xs)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:var(--s3)}
.r{border:1px solid var(--line);border-radius:var(--radius);background:var(--card);overflow:hidden;font-size:var(--text-sm)}
.r .m{width:100%;height:11rem;object-fit:contain;background:var(--n-100);display:flex;align-items:center;justify-content:center;color:var(--slate)}
.r .t{padding:var(--s2) var(--s3)}
.r .k{color:var(--slate);font-size:var(--text-xs)}
.x{opacity:.6;padding:0 2px}.x:hover{opacity:1}
.gone{color:var(--danger-ink)}
</style></head><body>
<header>__BRAND____NAV__<span id="count" class="muted"></span></header>
<main>
<p class="muted">What Photos knew about each photo and clip, kept on the drive. Search across all of it, including items already deleted from iCloud.</p>
<form id="f">
 <label>Person<input name="person" list="dl-person" placeholder="Dakota, Julia (together)"></label>
 <label>Thing<input name="label" list="dl-label" placeholder="beach, dog, climbing"></label>
 <label>Place<input name="place" placeholder="Boston"></label>
 <label>Album<input name="album" list="dl-album"></label>
 <label>Year<input name="year" size="4" placeholder="2019"></label>
 <label>Month<input name="month" size="7" placeholder="2019-07"></label>
 <label>Text<input name="text" placeholder="anything"></label>
 <label>Kind<select name="kind"><option value="">all</option><option>photo</option><option>video</option></select></label>
 <label>Where<select name="where"><option value="">anywhere</option><option>library</option><option>hidden</option><option value="trashed">recently deleted</option></select></label>
 <label class="chk"><input type="checkbox" name="fav" value="1"> Favourites only</label>
 <div class="actions"><button class="primary">Search</button> <button type="button" id="reset">Clear</button> <a href="/api/meta/export.csv">CSV of everything</a></div>
</form>
<datalist id="dl-person"></datalist><datalist id="dl-label"></datalist><datalist id="dl-album"></datalist>
<div id="facets"></div>
<p id="hid" class="muted" hidden></p>
<div class="grid" id="out"></div>
<p><button id="more" hidden>More</button></p>
</main>
<script>
const $=id=>document.getElementById(id);
let offset=0,HID=[],showHid=false;
// hidden people live on the server, so every device agrees
const saveHid=()=>fetch('/api/meta/hidden',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({people:HID})});
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
async function facets(){
  const d=await (await fetch('/api/meta/facets')).json();
  $('count').textContent=d.total?d.total+' items · '+d.trashed+' in Recently Deleted · '+d.hidden+' hidden':'nothing imported yet: run tools/photos/metadata-export.py on the Mac';
  for(const [k,id] of [['persons','dl-person'],['labels','dl-label'],['albums','dl-album']]){
    const dl=$(id);for(const [n] of d[k]){const o=el('option');o.value=n;dl.appendChild(o)}}
  FAC=d.persons;drawChips();
}
let FAC=[];
function drawChips(){
  const f=$('facets');f.textContent='';
  for(const [n,c] of FAC.filter(([n])=>showHid||!HID.includes(n)).slice(0,30)){
    const b=el('span','chip');
    const lab=el('span','',n+' '+c);
    lab.onclick=()=>{document.querySelector('[name=person]').value=n;run(true)};
    b.appendChild(lab);
    if(!HID.includes(n)){const x=el('span','x','\u00d7');x.title='Hide this person by default';
      x.onclick=e=>{e.stopPropagation();HID.push(n);saveHid();drawChips();run(true)};b.appendChild(x)}
    f.appendChild(b)}
  const h=$('hid');h.textContent='';h.hidden=!HID.length;
  if(HID.length){
    h.appendChild(document.createTextNode(HID.length+' hidden by default: '));
    const t=el('a','',showHid?'hide them again':'show hidden people');t.href='#';t.onclick=e=>{e.preventDefault();showHid=!showHid;drawChips();run(true)};h.appendChild(t);
    h.appendChild(document.createTextNode(' \u00b7 '));
    const u=el('a','','stop hiding anyone');u.href='#';u.onclick=e=>{e.preventDefault();HID=[];saveHid();showHid=false;drawChips();run(true)};h.appendChild(u);
  }
}
function card(r){
  const c=el('div','r');
  const m=el('div','m');
  if(r.viewable){
    const u='/meta/file/'+r.uuid;
    if(r.kind==='video'){const v=el('video');v.src=u;v.controls=true;v.preload='none';v.style.cssText='width:100%;height:100%';m.textContent='';m.appendChild(v)}
    else{const i=el('img');i.loading='lazy';i.src=u;i.style.cssText='width:100%;height:100%;object-fit:contain';m.appendChild(i)}
  }else m.textContent=r.mirror.startsWith('ok')?'in the mirror, no preview yet':'not in the mirror';
  c.appendChild(m);
  const t=el('div','t');
  t.appendChild(el('div','',(r.date||'').slice(0,10)+(r.fav?' ♥':'')+'  '+r.filename));
  const line=(k,v)=>{if(v&&v.length){const d=el('div','k',k+': '+(Array.isArray(v)?v.join(', '):v));t.appendChild(d)}};
  line('People',r.persons);line('Place',r.place);line('Things',r.labels);line('Albums',r.albums);line('Keywords',r.keywords);
  const flags=[r.trashed&&'in Recently Deleted',r.hidden&&'hidden',!r.mirror.startsWith('ok')&&'no mirror copy'].filter(Boolean);
  if(flags.length)t.appendChild(el('div','k gone',flags.join(' · ')));
  c.appendChild(t);return c;
}
async function run(reset){
  if(reset){offset=0;$('out').textContent=''}
  const p=new URLSearchParams(new FormData($('f')));
  for(const [k,v] of [...p])if(!v)p.delete(k);
  if(HID.length&&!showHid)p.set('exclude',HID.join(','));
  p.set('offset',offset);
  const d=await (await fetch('/api/meta/search?'+p)).json();
  for(const r of d.results)$('out').appendChild(card(r));
  offset+=d.results.length;
  $('more').hidden=offset>=d.total;
  if(reset&&!d.total)$('out').appendChild(el('p','muted','No matches.'));
  if(reset&&d.total)document.title=d.total+' found · Cold storage search';
}
$('f').onsubmit=e=>{e.preventDefault();run(true)};
$('more').onclick=()=>run(false);
$('reset').onclick=()=>{$('f').reset();run(true)};
fetch('/api/meta/hidden').then(r=>r.json()).then(d=>{HID=d.people||[]}).catch(()=>{}).then(facets).then(()=>run(true));
</script></body></html>
""".replace("__HEAD__", ui.HEAD).replace("__BRAND__", ui.brand()).replace("__NAV__", ui.nav("meta"))
