PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Duplicate photos</title>
<style>
:root{color-scheme:light dark;font-family:system-ui,sans-serif}
body{margin:0;display:flex;flex-direction:column;height:100vh}
header,footer{padding:.5rem 1rem;display:flex;gap:.75rem;align-items:center;flex-wrap:wrap}
header{border-bottom:1px solid #8884}
main{flex:1;display:flex;gap:1rem;padding:1rem;min-height:0;overflow:auto;align-items:flex-start;justify-content:center}
.card{width:22rem;border:3px solid #8884;border-radius:10px;overflow:hidden;cursor:pointer}
.card.keep{border-color:#2a7}.card.remove{border-color:#c33}.card.all{border-color:#8888}
.card img{width:100%;height:20rem;object-fit:contain;background:#000;display:block}
.card .nothumb{height:20rem;display:flex;align-items:center;justify-content:center;background:#000;color:#999}
.card div.info{padding:.5rem .75rem;font-size:.9rem}
.badge{font-weight:700;padding:.1rem .5rem;border-radius:999px;margin-right:.4rem}
.keep .badge{background:#2a74}.remove .badge{background:#c224}
button{font:inherit;padding:.4rem .8rem;border-radius:6px;border:1px solid #8886;background:#8881;cursor:pointer}
button b{opacity:.6;margin-left:.3rem}
small{opacity:.7}.warn{color:#c33}
</style></head><body>
<header>
 <b>Duplicate photos</b> <a href="/">clips</a>
 <label><input type="checkbox" id="hide" checked> hide decided</label>
 <span id="count"></span><span style="flex:1"></span>
 <button id="bulk">Accept all exact copies</button>
</header>
<main id="main"></main>
<footer>
 <button id="accept">Remove the red ones<b>Enter</b></button>
 <button id="all">Keep them all<b>K</b></button>
 <button id="prev">Prev<b>←</b></button><button id="next">Next<b>→</b></button>
 <small id="hint">Click a photo (or press 1-9) to make it the one you keep. Nothing is deleted here: accepted removals go into a Photos album from the Mac.</small>
</footer>
<script>
let all=[],list=[],i=0,pick=null;
const $=id=>document.getElementById(id);
async function load(keepId){
  all=(await (await fetch('/api/dupes')).json()).sets;
  refilter(keepId);
}
function refilter(keepId){
  const hide=$('hide').checked;
  list=all.filter(s=>!hide||!s.decision||s.id===keepId);
  i=Math.max(0,list.findIndex(s=>s.id===keepId)); pick=null; show();
}
const cur=()=>list[i];
function keeper(s){return pick||(s.decision&&s.decision!=='all'?s.decision:null)||s.members.find(m=>m.keep).uuid}
function show(){
  const done=all.filter(s=>s.decision).length;
  $('count').textContent=`${list.length?i+1:0}/${list.length} · ${done}/${all.length} decided`;
  const s=cur(), main=$('main'); main.innerHTML='';
  if(!s){main.textContent='Nothing left in this view. On the Mac: duplicates-to-album.py --from-review';return}
  const k=keeper(s), keepAll=s.decision==='all';
  s.members.forEach((m,n)=>{
    const c=document.createElement('div');
    const role=keepAll?'all':(m.uuid===k?'keep':'remove');
    c.className='card '+role; c.onclick=()=>{pick=m.uuid;show()};
    c.innerHTML=(m.thumb?`<img src="/dupes/thumb/${m.uuid}">`:'<div class="nothumb">no thumbnail</div>')+
     `<div class="info"><span class="badge">${role==='keep'?'KEEP':role==='all'?'KEEP':'REMOVE'}</span>${n+1}. ${m.filename}<br>`+
     `<small>${m.date.replace('T',' ').slice(0,19)} · ${(m.bytes/1e6).toFixed(1)} MB`+(m.score==null?'':` · score ${m.score.toFixed(2)}`)+`</small><br>`+
     (m.mirror==='ok'?'<small>✓ original in the mirror</small>':
      `<small class="${s.kind==='similar'?'warn':''}">✗ ${m.mirror==='size'?'in the mirror, but a different size':'not in the mirror'}`+(s.kind==='similar'?' (will not be accepted)':'')+`</small>`)+`</div>`;
    main.appendChild(c);
  });
  const kind=document.createElement('div');
  kind.innerHTML=`<small>${s.kind==='exact'?'Exact copies of one photo':'Same moment, different shots'}${s.decision?` · decided: ${s.decision==='all'?'keep all':'remove the others'}`:''}</small>`;
  main.appendChild(kind);
}
async function decide(s,keep){
  const r=await fetch('/api/dupes/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({set:s.id,keep})});
  if(!r.ok){$('hint').textContent='Save failed: '+r.status;return false}
  s.decision=keep; return true;
}
async function accept(){const s=cur();if(!s)return;if(await decide(s,keeper(s))){next(s.id)}}
async function keepAll(){const s=cur();if(!s)return;if(await decide(s,'all')){next(s.id)}}
function next(id){ if($('hide').checked){list=list.filter(x=>x.id!==id);if(i>=list.length)i=Math.max(list.length-1,0)}else i=Math.min(i+1,list.length-1); pick=null; show() }
$('bulk').onclick=async()=>{
  const ex=all.filter(s=>s.kind==='exact'&&!s.decision);
  if(!ex.length||!confirm(`Accept ${ex.length} exact-copy sets? Each keeps its default photo.`))return;
  for(const s of ex) await decide(s,s.members.find(m=>m.keep).uuid);
  refilter();
};
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
"""
