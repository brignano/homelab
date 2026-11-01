from . import ui

PAGE = r"""<!doctype html>
<html lang="en"><head><title>Duplicate photos · Review</title>
__HEAD__
<style>
main{overflow:auto;align-items:flex-start;justify-content:center;flex-wrap:wrap}
.card{width:22rem;max-width:100%;background:var(--card);border:2px solid var(--line);border-radius:var(--radius);
  overflow:hidden;cursor:pointer;box-shadow:var(--shadow-1)}
.card.keep{border-color:var(--success)}.card.remove{border-color:var(--danger)}.card.all{border-color:var(--line-strong)}
.card img,.card .nothumb{width:100%;height:20rem;object-fit:contain;background:var(--n-100);display:block}
.card .nothumb{display:flex;align-items:center;justify-content:center;color:var(--slate)}
.card div.info{padding:var(--s3) var(--s4);font-size:var(--text-sm)}
.conf{font-weight:650}.conf.high{color:var(--success-ink)}.conf.medium{color:var(--warning-ink,var(--ink))}.conf.low{color:var(--danger-ink)}
#report{margin:0;padding:var(--s2) var(--s4);font-size:var(--text-sm)}
.badge{font-weight:650;padding:0 var(--s3);border-radius:999px;margin-right:var(--s2);font-size:var(--text-xs);
  border:1px solid var(--line);display:inline-block}
.keep .badge{background:var(--success-surface);border-color:var(--success-line);color:var(--success-ink)}
.remove .badge{background:var(--danger-surface);border-color:var(--danger-line);color:var(--danger-ink)}
#here{display:none}
#kind{margin:0;padding:var(--s2) var(--s4);background:var(--bg);border-bottom:1px solid var(--line)}
footer{position:sticky;bottom:0;background:var(--surface-sticky);backdrop-filter:blur(8px)}
@media (max-width:760px){
  /* swipe sideways between the candidates, one big card at a time */
  main{flex-direction:row;flex-wrap:nowrap;overflow-x:auto;overflow-y:hidden;scroll-snap-type:x mandatory;
    align-items:center;justify-content:flex-start;gap:var(--s3);-webkit-overflow-scrolling:touch}
  .card{flex:0 0 calc(100vw - 2*var(--s3));width:calc(100vw - 2*var(--s3));scroll-snap-align:center;scroll-snap-stop:always;
    display:flex;flex-direction:column}
  #here{display:inline-flex}
  /* the photo sets the card height: no dark bands above and below it */
  .card img{height:auto;max-height:58dvh;background:var(--card)}
  .card .nothumb{height:40dvh}
  .card div.info{padding:var(--s2) var(--s3)}
  header label{display:none}            /* "hide decided" stays on; desktop can switch it */
  header #bulk{flex:1 1 100%;order:9;min-height:40px}
  #kind{font-size:var(--text-xs)}
  footer button{flex:1 1 40%;min-height:48px}
}
</style></head><body>
<header>
 __BRAND____NAV__
 <label><input type="checkbox" id="hide" checked> hide decided</label>
 <span id="count" class="muted"></span><span class="grow"></span>
 <button id="bulk" class="primary">Accept high confidence</button>
</header>
<p id="kind" class="muted" aria-live="polite"></p>
<p id="report" class="muted"></p>
<main id="main"></main>
<footer>
 <button id="here">Keep this one</button>
 <button id="accept" class="primary">Remove red<kbd>Enter</kbd></button>
 <button id="all">Keep them all<kbd>K</kbd></button>
 <button id="prev">Prev<kbd>←</kbd></button><button id="next">Next<kbd>→</kbd></button>
 <small id="hint" class="grow hint">Click a photo (or press 1-9) to make it the one you keep. Nothing is deleted here: accepted removals go into a Photos album from the Mac.</small>
</footer>
<script>
let all=[],list=[],i=0,pick=null,rep=null;
const $=id=>document.getElementById(id);
async function load(keepId){
  const d=await (await fetch('/api/dupes')).json();
  all=d.sets; rep=d.report; refilter(keepId);
}
function refilter(keepId){
  const hide=$('hide').checked;
  list=all.filter(s=>!hide||!s.decision||s.id===keepId);
  i=Math.max(0,list.findIndex(s=>s.id===keepId)); pick=null; show();
}
const cur=()=>list[i];
function keeper(s){return pick||(s.decision&&s.decision!=='all'?s.decision:null)||s.members.find(m=>m.keep).uuid}
const ICON={high:'✓',medium:'~',low:'!'}, WORD={high:'High confidence',medium:'Fairly sure',low:'Check this one'};
function showReport(){
  const t=rep&&rep.total; if(!t||!t.decided){$('report').textContent='';return}
  const pct=(a,b)=>b?Math.round(100*a/b)+'%':'–';
  const tiers=['high','medium','low'].map(k=>`${k} ${pct(rep.tiers[k].agreed,rep.tiers[k].decided)} of ${rep.tiers[k].decided}`).join(' · ');
  $('report').textContent=`You agreed with the proposed keeper on ${pct(t.agreed,t.decided)} of ${t.decided} decided sets (${tiers}).`;
}
function show(){
  showReport();vis=0;
  const done=all.filter(s=>s.decision).length;
  $('count').textContent=`${list.length?i+1:0}/${list.length} · ${done}/${all.length} decided`;
  const s=cur(), main=$('main'); main.innerHTML='';
  if(!s){$('kind').textContent='';main.textContent='Nothing left in this view. On the Mac: duplicates-to-album.py --from-review';return}
  const k=keeper(s), keepAll=s.decision==='all';
  s.members.forEach((m,n)=>{
    const c=document.createElement('div');
    const role=keepAll?'all':(m.uuid===k?'keep':'remove');
    c.className='card '+role; c.onclick=()=>{pick=m.uuid;show()};
    c.innerHTML=(m.thumb?`<img src="/dupes/thumb/${m.uuid}">`:'<div class="nothumb">no thumbnail</div>')+
     `<div class="info"><span class="badge">${role==='keep'?'KEEP':role==='all'?'KEEP':'REMOVE'}</span>${n+1}/${s.members.length} · ${m.filename}<br>`+
     `<small>${m.date.replace('T',' ').slice(0,19)} · ${(m.bytes/1e6).toFixed(1)} MB`+(m.score==null?'':` · score ${m.score.toFixed(2)}`)+`</small><br>`+
     (m.mirror==='ok'?'<small>✓ original in the mirror</small>':
      `<small class="${s.kind==='similar'?'warn':''}">✗ ${m.mirror==='size'?'in the mirror, but a different size':'not in the mirror'}`+(s.kind==='similar'?' (will not be accepted)':'')+`</small>`)+`</div>`;
    main.appendChild(c);
  });
  $('kind').innerHTML=`<span class="conf ${s.confidence}">${ICON[s.confidence]} ${WORD[s.confidence]}</span> · ${s.why} · `+(s.kind==='exact'?'Exact copies of one photo':'Same moment, different shots')+
    (s.decision?' · decided: '+(s.decision==='all'?'keep all':'remove the others'):'');
}
async function decide(s,keep){
  const r=await fetch('/api/dupes/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({set:s.id,keep})});
  if(!r.ok){$('hint').textContent=$('kind').textContent='Save failed: '+r.status;return false}
  s.decision=keep; return true;
}
async function accept(){const s=cur();if(!s)return;if(await decide(s,keeper(s))){next(s.id)}}
async function keepAll(){const s=cur();if(!s)return;if(await decide(s,'all')){next(s.id)}}
function next(id){ if($('hide').checked){list=list.filter(x=>x.id!==id);if(i>=list.length)i=Math.max(list.length-1,0)}else i=Math.min(i+1,list.length-1); pick=null; show() }
$('bulk').onclick=async()=>{
  const ex=all.filter(s=>s.auto_ok&&!s.decision);
  if(!ex.length){$('hint').textContent='No undecided high-confidence sets left.';return}
  if(!confirm(`Accept ${ex.length} high-confidence sets? Each keeps its proposed photo. Nothing is deleted: removals go to a Photos album you check first.`))return;
  for(const s of ex) await decide(s,s.members.find(m=>m.keep).uuid);
  await load();
};
let vis=0;
const colW=()=>{const c=$('main').firstElementChild;return c?c.getBoundingClientRect().width+parseFloat(getComputedStyle($('main')).columnGap||0):1};
$('main').addEventListener('scroll',()=>{vis=Math.round($('main').scrollLeft/colW())},{passive:true});
$('here').onclick=()=>{const s=cur();if(!s)return;const at=vis;pick=s.members[at].uuid;show();$('main').scrollLeft=at*colW();vis=at};
$('accept').onclick=accept;$('all').onclick=keepAll;
$('prev').onclick=()=>{i=Math.max(i-1,0);pick=null;show()};$('next').onclick=()=>{i=Math.min(i+1,list.length-1);pick=null;show()};
$('hide').onchange=()=>refilter();
addEventListener('keydown',e=>{
  if(e.metaKey||e.ctrlKey)return; const k=e.key.toLowerCase(), s=cur();
  if(k==='enter')accept();else if(k==='k')keepAll();
  else if(k==='arrowright')$('next').click();else if(k==='arrowleft')$('prev').click();
  else if(s&&/^[1-9]$/.test(k)&&s.members[k-1]){pick=s.members[k-1].uuid;show()}
});
load();
</script></body></html>
""".replace("__HEAD__", ui.HEAD).replace("__BRAND__", ui.brand()).replace("__NAV__", ui.nav("dupes"))
