// 알바지킴이 화면 동작. 모든 기록과 판단은 서버(API)에서 처리하고, 화면은 보여주기와 입력만 맡는다.
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const DAY_KEYS = ['월','화','수','목','금','토','일'];
const TIMES = []; for (let h=0; h<24; h++) { TIMES.push(pad(h)+':00'); TIMES.push(pad(h)+':30'); }
const BREAKS = ['없음','30분','1시간','1시간 30분','2시간','모름'];
const LABEL = {ok:'정상', warn:'확인 필요', bad:'위반 의심'};
const KIND = {contract:'근로계약서', payslip:'급여명세서', message:'사업주 메시지', schedule:'근무표', deposit:'입금 내역', post:'게시물 화면', notice:'채용공고', other:'기타'};

const state = { me:null, jobs:[], current:null, mode:null, cards:[], adding:false, seekFromApp:false };

function pad(n){ return String(n).padStart(2,'0'); }
function esc(t){ return String(t ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function toMin(t){ const [h,m]=t.split(':').map(Number); return h*60+m; }
function brkMin(b){ return {'없음':0,'30분':30,'1시간':60,'1시간 30분':90,'2시간':120}[b] ?? 0; }
function slotMinutes(s){ let a=toMin(s.start), b=toMin(s.end); if(b<=a) b+=1440; return Math.max(0,b-a-brkMin(s.brk)); }
function isNight(s){ let a=toMin(s.start), b=toMin(s.end); if(b<=a) b+=1440; for(let t=a;t<b;t+=30){ const x=t%1440; if(x>=1320||x<360) return true; } return false; }
function fmtH(min){ const h=Math.floor(min/60), m=min%60; return m?`${h}시간 ${m}분`:`${h}시간`; }
function fmtDT(iso){ const d=new Date(iso); return `${d.getMonth()+1}월 ${d.getDate()}일 ${pad(d.getHours())}:${pad(d.getMinutes())}`; }
function won(n){ return (n ?? 0).toLocaleString()+'원'; }
function ageOn(dateStr){
  if(!state.me) return null;
  const [y,m,d]=state.me.birth_date.split('-').map(Number);
  const t=dateStr?new Date(dateStr+'T00:00:00'):new Date();
  let a=t.getFullYear()-y; if(t.getMonth()+1<m||(t.getMonth()+1===m&&t.getDate()<d)) a--; return a;
}

let tt;
function toast(msg){ const el=$('#toast'); el.textContent=msg; el.classList.add('show'); clearTimeout(tt); tt=setTimeout(()=>el.classList.remove('show'),2600); }

async function api(method, url, body, isForm){
  const opt = { method, headers:{} };
  if (body !== undefined) {
    if (isForm) opt.body = body; else { opt.headers['Content-Type']='application/json'; opt.body=JSON.stringify(body); }
  }
  const r = await fetch(url, opt);
  if (!r.ok) {
    let msg = '요청을 처리하지 못했어요';
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : msg; } catch(e) {}
    const err = new Error(msg); err.status = r.status; throw err;
  }
  return r.json();
}

function show(id){ ['ob0','ob1','obLogin','obSeek','ob2'].forEach(x=>$('#'+x).classList.toggle('hidden', x!==id)); window.scrollTo(0,0); }
function openOverlay(id){ $('#onboard').classList.remove('hidden'); show(id); }
function closeOverlay(){ $('#onboard').classList.add('hidden'); }

// ---------- 공통 선택 버튼 ----------
function bindSeg(root, onChange){
  root.querySelectorAll('.seg').forEach(seg => {
    seg.setAttribute('role','group');
    seg.querySelectorAll('button').forEach(btn => {
      btn.setAttribute('aria-pressed','false');
      btn.addEventListener('click', () => {
        seg.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed','false'));
        btn.setAttribute('aria-pressed','true');
        onChange && onChange(seg.dataset.name, btn.dataset.v);
      });
    });
  });
}
function segVal(root, name){ const b=root.querySelector(`.seg[data-name="${name}"] [aria-pressed="true"]`); return b?b.dataset.v:null; }
function setSeg(root, name, v){ const b=root.querySelector(`.seg[data-name="${name}"] [data-v="${v}"]`); if(b) b.click(); }

// ---------- 시작 ----------
async function boot(){
  try {
    state.me = await api('GET','/api/me');
    await loadJobs();
    if (!state.jobs.length) { openOverlay('ob2'); addCard(); }
    else startApp();
  } catch(e) {
    if (e.status === 401) openOverlay('ob0'); else toast(e.message);
  }
}

$$('.mode').forEach(b => b.addEventListener('click', () => {
  $$('.mode').forEach(x=>x.setAttribute('aria-checked','false'));
  b.setAttribute('aria-checked','true'); state.mode=b.dataset.mode; $('#modeNext').disabled=false;
}));
$('#modeNext').onclick = () => show('ob1');
$('#toLogin').onclick = () => show('obLogin');
$('#toStart').onclick = () => show('ob0');

$('#regBtn').onclick = async () => {
  $('#regErr').textContent='';
  const email=$('#regEmail').value.trim(), password=$('#regPw').value, birth_date=$('#birth').value;
  if(!email||!password||!birth_date){ $('#regErr').textContent='이메일, 비밀번호, 생년월일을 모두 입력해 주세요'; return; }
  try {
    state.me = await api('POST','/api/auth/register',{email,password,birth_date,mode:state.mode||'work'});
    if (state.mode==='seek') { seekReset(); show('obSeek'); }
    else { show('ob2'); const c=addCard(); if(state.mode==='quit') setSeg(c.node,'status','quit'); }
  } catch(e) { $('#regErr').textContent=e.message; }
};
$('#logBtn').onclick = async () => {
  $('#logErr').textContent='';
  try {
    state.me = await api('POST','/api/auth/login',{email:$('#logEmail').value.trim(),password:$('#logPw').value});
    await loadJobs();
    if (!state.jobs.length) { show('ob2'); addCard(); } else { closeOverlay(); startApp(); }
  } catch(e) { $('#logErr').textContent=e.message; }
};

// ---------- 사업장 입력 카드 ----------
function addCard(){
  const node=$('#jobTpl').content.firstElementChild.cloneNode(true);
  const card={node, schedule:{}}; state.cards.push(card);
  const name=node.querySelector('.f-name');
  name.addEventListener('input',()=>{ node.querySelector('.job-title').textContent=name.value.trim()||'새 일하는 곳'; });
  node.querySelector('.remove').onclick=()=>{
    if(state.cards.length===1){ toast('하나 이상 있어야 해요'); return; }
    node.remove(); state.cards=state.cards.filter(c=>c!==card); refreshRemove();
  };
  node.querySelector('.schedule-btn').onclick=()=>openSheetFor(card);
  node.querySelector('.f-noend').onchange=e=>{ node.querySelector('.f-end').disabled=e.target.checked; };
  node.querySelector('.f-start').addEventListener('change',()=>checkMinor(card));
  bindSeg(node,(n,v)=>{
    if(n==='status') node.querySelector('.f-quit-wrap').classList.toggle('hidden',v!=='quit');
    if(n==='probation') node.querySelector('.f-probmonths').classList.toggle('hidden',v!=='yes');
    if(n==='contract') node.querySelector('.f-copy').classList.toggle('hidden',v!=='yes');
    if(n==='paymethod') node.querySelector('.f-cash').classList.toggle('hidden',v!=='현금');
    if(n==='paytype') node.querySelector('.f-payday-wrap').classList.toggle('hidden',v!=='월급');
  });
  setSeg(node,'status','working');
  $('#jobList').appendChild(node); checkMinor(card); refreshRemove();
  return card;
}
function refreshRemove(){ state.cards.forEach(c=>c.node.querySelector('.remove').classList.toggle('hidden',state.cards.length===1)); }
function checkMinor(card){
  const node=card.node, start=node.querySelector('.f-start').value;
  const now=ageOn(null), atStart=start?ageOn(start):null;
  const minor=(now!==null&&now<18)||(atStart!==null&&atStart<18);
  node.querySelector('.minor-only').classList.toggle('hidden',!minor);
  if(minor) node.querySelector('.minor-why').textContent = now<18
    ? `생년월일 기준 지금 만 ${now}세예요. 만 18세 미만은 이 서류가 필요해서 함께 확인해요.`
    : `일을 시작한 날 만 ${atStart}세였어요. 만 18세 전 근무 기간에 필요한 서류라 함께 확인해요.`;
}
function yn(v){ return v==='yes'?true:(v==='no'?false:null); }
function collect(card){
  const n=card.node, q=s=>n.querySelector(s);
  const wage=q('.f-wage').value, payday=q('.f-payday').value;
  return {
    name:q('.f-name').value.trim(), status:segVal(n,'status')||'working', quit_date:q('.f-quit').value||null,
    industry:q('.f-type').value, work_desc:q('.f-work').value.trim(), wage:wage?Number(wage):null,
    start_date:q('.f-start').value||null, end_date:q('.f-noend').checked?null:(q('.f-end').value||null), no_end:q('.f-noend').checked,
    contract_written:yn(segVal(n,'contract')), copy_received:yn(segVal(n,'copy')),
    probation:segVal(n,'probation')||'unknown', probation_months:q('.f-probmonths').value?Number(q('.f-probmonths').value):null,
    schedule:card.schedule, size:segVal(n,'size')||'unknown', pay_cycle:segVal(n,'paytype')||'',
    payday:payday?Number(payday):null, pay_method:segVal(n,'paymethod')||'', deduction:segVal(n,'deduct')||'',
    consent:segVal(n,'consent')||'', address:q('.f-addr').value.trim(), owner:q('.f-owner').value.trim(),
  };
}
$('#addJobBtn').onclick=()=>{ const c=addCard(); c.node.scrollIntoView({block:'start'}); };
$('#saveJobsBtn').onclick=async()=>{
  $('#jobErr').textContent='';
  for(const c of state.cards){
    const d=collect(c);
    if(!d.name){ c.node.scrollIntoView({block:'center'}); c.node.querySelector('.f-name').focus(); $('#jobErr').textContent='사업장 이름을 입력해 주세요'; return; }
    if(d.status==='quit'&&!d.quit_date){ c.node.scrollIntoView({block:'center'}); $('#jobErr').textContent='그만둔 날을 입력해 주세요'; return; }
  }
  try{
    let last=null;
    for(const c of state.cards){ last = await api('POST','/api/jobs',collect(c)); c.saved=true; }
    state.cards.forEach(c=>c.node.remove()); state.cards=[];
    await loadJobs();
    state.current = state.adding||!state.current ? (last?.id ?? state.jobs[0].id) : state.current;
    state.adding=false; closeOverlay(); startApp();
    toast(`${curJob().name} 기록을 보고 있어요`);
  }catch(e){ $('#jobErr').textContent=e.message; }
};
$('#cancelJobsBtn').onclick=()=>{ state.cards.forEach(c=>c.node.remove()); state.cards=[]; state.adding=false; closeOverlay(); };

// ---------- 근무 시간 선택 ----------
let editing=null, draft=null;
function openSheetFor(target){
  editing=target; draft=JSON.parse(JSON.stringify(target.schedule||{}));
  const nm=target.node.querySelector('.f-name')?.value.trim();
  $('#sheetJobName').textContent=nm?`${nm}에서 일하는 시간을 골라 주세요`:'일하는 시간을 골라 주세요';
  renderDays(); renderSlots(); $('#sheetBg').classList.remove('hidden'); document.body.style.overflow='hidden';
}
function closeSheet(){ $('#sheetBg').classList.add('hidden'); document.body.style.overflow=''; editing=null; }
function renderDays(){
  const box=$('#dayPicker'); box.innerHTML='';
  DAY_KEYS.forEach(k=>{ const b=document.createElement('button'); b.type='button'; b.className='day'; b.textContent=k;
    b.setAttribute('aria-pressed',draft[k]?'true':'false'); b.setAttribute('aria-label',`${k}요일`);
    b.onclick=()=>{ if(draft[k]) delete draft[k]; else draft[k]={start:'18:00',end:'22:00',brk:'없음'}; renderDays(); renderSlots(); };
    box.appendChild(b); });
}
function opts(list,sel){ return list.map(v=>`<option${v===sel?' selected':''}>${v}</option>`).join(''); }
function renderSlots(){
  const box=$('#slotList'); box.innerHTML='';
  const keys=DAY_KEYS.filter(k=>draft[k]);
  $('#slotEmpty').classList.toggle('hidden',keys.length>0); $('#copyAll').classList.toggle('hidden',keys.length<2);
  keys.forEach(k=>{ const s=draft[k]; const el=document.createElement('div'); el.className='slot';
    el.innerHTML=`<div class="slot-day">${k}요일 <span class="hrs">${fmtH(slotMinutes(s))}</span></div>
      <div class="slot-grid"><select class="input" aria-label="${k}요일 시작 시간">${opts(TIMES,s.start)}</select><span class="tilde">부터</span>
      <select class="input" aria-label="${k}요일 끝 시간">${opts(TIMES,s.end)}</select></div>
      <div class="brk">쉬는 시간<select class="input" aria-label="${k}요일 쉬는 시간">${opts(BREAKS,s.brk)}</select></div>`;
    const [st,en]=el.querySelectorAll('.slot-grid select'), br=el.querySelector('.brk select');
    const up=()=>{ el.querySelector('.hrs').textContent=fmtH(slotMinutes(s)); totals(); };
    st.onchange=()=>{s.start=st.value;up()}; en.onchange=()=>{s.end=en.value;up()}; br.onchange=()=>{s.brk=br.value;up()};
    box.appendChild(el); });
  totals();
}
function totals(){ const keys=DAY_KEYS.filter(k=>draft[k]);
  $('#weekTotal').textContent=fmtH(keys.reduce((a,k)=>a+slotMinutes(draft[k]),0));
  $('#nightFlag').classList.toggle('hidden',!keys.some(k=>isNight(draft[k]))); }
function schedSummary(schedule){ const keys=DAY_KEYS.filter(k=>schedule[k]); if(!keys.length) return '근무 요일 미등록';
  return `${keys.join(', ')}요일, 주 ${fmtH(keys.reduce((a,k)=>a+slotMinutes(schedule[k]),0))}`; }
$('#copyAll').onclick=()=>{ const keys=DAY_KEYS.filter(k=>draft[k]); const f=draft[keys[0]]; keys.forEach(k=>draft[k]={...f}); renderSlots(); };
$('#sheetClose').onclick=closeSheet;
$('#sheetBg').onclick=e=>{ if(e.target.id==='sheetBg') closeSheet(); };
$('#sheetSave').onclick=()=>{
  if(!DAY_KEYS.some(k=>draft[k])){ toast('일하는 요일을 하나 이상 골라 주세요'); return; }
  editing.schedule=draft; editing.node.querySelector('.sched-sum').textContent=schedSummary(draft);
  editing.node.querySelector('.sched-go').textContent='수정'; closeSheet(); toast('근무 시간을 저장했어요');
};

// ---------- 지원 전 확인 ----------
const seek={node:$('#seekForm'), schedule:{}};
bindSeg(seek.node);
seek.node.querySelector('.schedule-btn').onclick=()=>openSheetFor(seek);
$('#seekFile').onchange=async e=>{ const f=e.target.files[0]; if(!f) return;
  const fd=new FormData(); fd.append('file',f); fd.append('kind','notice');
  try{ await api('POST','/api/evidence',fd,true); toast('공고 사진을 원본으로 보관했어요'); }catch(err){ toast(err.message); } e.target.value=''; };
function seekInput(){ const n=seek.node, w=n.querySelector('.f-wage').value;
  return { name:n.querySelector('.f-name').value.trim(), industry:n.querySelector('.f-type').value, work_desc:n.querySelector('.f-work').value.trim(),
    wage:w?Number(w):null, probation:segVal(n,'probation')||'unknown', schedule:seek.schedule }; }
function itemHTML(it){
  const extra=[]; if(it.basis?.length) extra.push(`근거로 쓴 사실: ${esc(it.basis.join(', '))}`);
  if(it.needed?.length) extra.push(`필요한 정보: ${esc(it.needed.join(', '))}`);
  return `<div class="result ${it.status}"><div class="head"><span class="law">${esc(it.law)}</span><span class="tag ${it.status}">${LABEL[it.status]}</span></div>
    <p>${esc(it.text)}</p>${extra.map(x=>`<p class="basis">${x}</p>`).join('')}</div>`;
}
$('#seekRun').onclick=async()=>{
  $('#seekErr').textContent='';
  try{ const r=await api('POST','/api/seek/check',seekInput());
    $('#seekItems').innerHTML=r.items.map(itemHTML).join('');
    $('#seekQs').innerHTML=r.questions.map(q=>`<li>${esc(q)}</li>`).join('');
    $('#seekForm').classList.add('hidden'); $('#seekUpload').classList.add('hidden'); $('#seekResult').classList.remove('hidden');
    $('#seekClose').classList.toggle('hidden',!state.seekFromApp); window.scrollTo(0,0);
  }catch(e){ $('#seekErr').textContent=e.message; }
};
function seekReset(){ const n=seek.node; n.querySelectorAll('input').forEach(i=>i.value=''); n.querySelector('.f-type').value='';
  n.querySelectorAll('.seg button').forEach(b=>b.setAttribute('aria-pressed','false')); seek.schedule={};
  n.querySelector('.sched-sum').textContent='요일과 시간을 선택해 주세요'; n.querySelector('.sched-go').textContent='선택';
  $('#seekResult').classList.add('hidden'); $('#seekForm').classList.remove('hidden'); $('#seekUpload').classList.remove('hidden'); }
$('#seekAgain').onclick=seekReset;
$('#seekClose').onclick=()=>{ state.seekFromApp=false; closeOverlay(); };
$('#seekToWork').onclick=()=>{
  const d=seekInput(); state.adding=!!state.current;
  show('ob2'); const c=addCard(), n=c.node;
  n.querySelector('.f-name').value=d.name; n.querySelector('.f-name').dispatchEvent(new Event('input'));
  n.querySelector('.f-type').value=d.industry; n.querySelector('.f-work').value=d.work_desc; n.querySelector('.f-wage').value=d.wage??'';
  c.schedule=JSON.parse(JSON.stringify(d.schedule));
  if(DAY_KEYS.some(k=>c.schedule[k])){ n.querySelector('.sched-sum').textContent=schedSummary(c.schedule); n.querySelector('.sched-go').textContent='수정'; }
  if(d.probation!=='unknown') setSeg(n,'probation',d.probation);
  $('#saveJobsBtn').textContent=state.adding?'저장하기':'시작하기'; $('#cancelJobsBtn').classList.toggle('hidden',!state.adding);
  toast('공고 내용으로 채웠어요. 나머지를 확인해 주세요');
};
$('#seekEntry').onclick=()=>{ state.seekFromApp=true; seekReset(); openOverlay('obSeek'); };

// ---------- 앱 ----------
async function loadJobs(){ state.jobs=await api('GET','/api/jobs'); }
function curJob(){ return state.jobs.find(j=>j.id===state.current); }
function startApp(){
  if(!state.current||!curJob()) state.current=state.jobs[0].id;
  closeOverlay(); $('#app').classList.remove('hidden'); $('#tabs').classList.remove('hidden');
  applyCurrent(); showTab(currentTab);
}
function applyCurrent(){ const j=curJob(); $$('.cur-job').forEach(el=>el.textContent=j.name); $('#schedLine').textContent=schedSummary(j.schedule); }
let currentTab='home';
function showTab(v){
  currentTab=v;
  $$('nav.tabs button').forEach(b=>b.toggleAttribute('aria-current',b.dataset.v===v)); $$('nav.tabs button[aria-current]').forEach(b=>b.setAttribute('aria-current','page'));
  $$('.view').forEach(x=>x.classList.toggle('active',x.id==='v-'+v)); window.scrollTo(0,0);
  ({home:loadHome, work:loadWork, check:loadCheck, docs:loadDocs, guard:loadGuard})[v]().catch(e=>toast(e.message));
}
$$('nav.tabs button').forEach(b=>b.onclick=()=>showTab(b.dataset.v));

// 일하는 곳 선택
$('#jobSwitch').onclick=()=>{
  const box=$('#placeList'); box.innerHTML='';
  state.jobs.forEach(j=>{ const b=document.createElement('button'); b.type='button'; b.className='place'; b.setAttribute('role','radio');
    b.setAttribute('aria-checked',j.id===state.current?'true':'false');
    b.innerHTML=`<span class="radio" aria-hidden="true"></span><span class="info"><strong>${esc(j.name)}</strong><span>${j.status==='quit'?'그만둔 곳, ':''}${esc(schedSummary(j.schedule))}</span></span>`;
    b.onclick=()=>{ state.current=j.id; applyCurrent(); closePicker(); showTab(currentTab); toast(`${j.name} 기록을 보고 있어요`); };
    box.appendChild(b); });
  $('#pickerBg').classList.remove('hidden'); document.body.style.overflow='hidden';
};
function closePicker(){ $('#pickerBg').classList.add('hidden'); document.body.style.overflow=''; }
$('#pickerClose').onclick=closePicker;
$('#pickerBg').onclick=e=>{ if(e.target.id==='pickerBg') closePicker(); };
$('#addPlace').onclick=()=>{ closePicker(); state.adding=true; openOverlay('ob2'); addCard();
  $('#saveJobsBtn').textContent='저장하기'; $('#cancelJobsBtn').classList.remove('hidden'); };
$('#logoutBtn').onclick=async()=>{ await api('POST','/api/auth/logout'); location.reload(); };
document.addEventListener('keydown',e=>{ if(e.key!=='Escape') return; if(editing) closeSheet(); else closePicker(); });

// 출퇴근
const days=['일','월','화','수','목','금','토'];
function tick(){ const d=new Date(); $('#today').textContent=`${d.getMonth()+1}월 ${d.getDate()}일 (${days[d.getDay()]})`; $('#now').textContent=`${pad(d.getHours())}:${pad(d.getMinutes())}`; }
tick(); setInterval(tick,1000);
function setPunchUI(working, text){
  const btn=$('#punchBtn'), j=curJob();
  btn.disabled=j.status==='quit'; btn.textContent=j.status==='quit'?'그만둔 곳이에요':(working?'퇴근하기':'출근하기');
  btn.classList.toggle('out',working); $('#clockState').textContent=text;
}
async function loadHome(){
  const r=await api('GET',`/api/jobs/${state.current}/records`);
  const open=r.records.find(x=>!x.clock_out);
  setPunchUI(r.working, open?`${fmtDT(open.clock_in)} 출근 기록됨`:(r.records[0]?`마지막 퇴근 ${fmtDT(r.records[0].clock_out)}`:'아직 출근 기록이 없어요'));
  const ns=await api('GET','/api/notifications');
  $('#alerts').innerHTML=ns.length?ns.map(n=>`<div class="alert"><span class="dot ${n.read?'read':''}"></span><div><strong>${esc(n.title)}</strong><div class="sub">${esc(n.body)}</div></div></div>`).join('')
    :'<p class="sub" style="margin:0">아직 알림이 없어요. 점검 결과가 생기면 여기에 알려드려요.</p>';
  if(ns.some(n=>!n.read)) api('POST','/api/notifications/read');
}
function getPos(){ return new Promise(res=>{ if(!navigator.geolocation) return res(null);
  navigator.geolocation.getCurrentPosition(p=>res({lat:p.coords.latitude,lng:p.coords.longitude}),()=>res(null),{timeout:8000,maximumAge:0}); }); }
$('#punchBtn').onclick=async()=>{
  const btn=$('#punchBtn'); btn.disabled=true;
  try{ let pos=null; if($('#gpsOn').checked){ pos=await getPos(); if(!pos) toast('위치를 가져오지 못해 시각만 기록해요'); }
    const r=await api('POST',`/api/jobs/${state.current}/punch`,pos||{});
    const t=fmtDT(r.server_time);
    setPunchUI(r.action==='in', r.action==='in'?`${t} 출근 기록됨${pos?', 위치 함께 기록':''}`:`${t} 퇴근 기록됨`);
    toast(r.action==='in'?'출근이 기록됐어요':'퇴근이 기록됐어요');
  }catch(e){ toast(e.message); } finally{ btn.disabled=curJob().status==='quit'; }
};

// 근무
function thisMonth(){ const d=new Date(); return `${d.getFullYear()}-${pad(d.getMonth()+1)}`; }
async function loadWork(){
  const j=curJob(); applyCurrent();
  const r=await api('GET',`/api/jobs/${j.id}/records`);
  $('#recordsEmpty').classList.toggle('hidden',r.records.length>0); $('#recordsPanel').classList.toggle('hidden',!r.records.length);
  $('#records').innerHTML=r.records.map(x=>`<li><div class="main"><strong class="num">${fmtDT(x.clock_in).split(' ').slice(0,2).join(' ')}</strong>
    <div class="sub num">${fmtDT(x.clock_in).split(' ')[2]} 출근, ${x.clock_out?fmtDT(x.clock_out).split(' ')[2]+' 퇴근':'근무 중'}</div></div>
    <span class="tag ${x.gps?'ok':'warn'}">${x.gps?'위치 기록':'위치 미기록'}</span></li>`).join('');
  if(!$('#payMonth').value) $('#payMonth').value=thisMonth();
  await renderQuit(); await loadPay();
}
async function renderQuit(){
  const j=curJob(); const quit=j.status==='quit';
  $('#quitOpen').classList.toggle('hidden',quit); $('#quitForm').classList.add('hidden'); $('#quitPanel').classList.toggle('hidden',!quit);
  if(!quit) return;
  const {settlement:st}=await api('GET',`/api/jobs/${j.id}/settlement`);
  if(!st){ $('#quitTag').className='tag warn'; $('#quitTag').textContent='확인 필요'; $('#quitText').textContent='그만둔 날을 입력하면 정산 기한을 계산해 드려요.'; return; }
  const f=s=>{const d=new Date(s+'T00:00:00'); return `${d.getMonth()+1}월 ${d.getDate()}일`;};
  $('#quitTag').className='tag '+st.status; $('#quitTag').textContent=LABEL[st.status];
  $('#quitText').textContent = j.paid_after_quit===true ? `${f(st.quit_date)}에 그만뒀고, 남은 임금을 받았다고 기록했어요.`
    : st.left>=0 ? `${f(st.quit_date)}에 그만뒀어요. 남은 임금 지급 기한은 ${f(st.due)}로, ${st.left}일 남았어요.`
    : `${f(st.quit_date)}에 그만뒀고 지급 기한 ${f(st.due)}이 지났어요. 아직 못 받았다면 상담을 준비하세요.`;
  $('#quitReport').classList.toggle('hidden',!(j.paid_after_quit===false||(st.status==='bad'&&j.paid_after_quit!==true)));
}
$('#quitOpen').onclick=()=>{ $('#quitForm').classList.remove('hidden'); $('#quitOpen').classList.add('hidden'); };
$('#quitSave').onclick=async()=>{ const v=$('#quitDateMain').value; if(!v){ toast('그만둔 날을 골라 주세요'); return; }
  try{ await api('POST',`/api/jobs/${state.current}/quit`,{quit_date:v}); await loadJobs(); await loadWork(); toast('그만둔 날을 저장했어요'); }catch(e){ toast(e.message); } };
async function setPaid(p){ try{ await api('POST',`/api/jobs/${state.current}/paid`,{paid:p}); await loadJobs(); await renderQuit(); toast(p?'받았다고 기록했어요':'못 받았다고 기록했어요'); }catch(e){ toast(e.message); } }
$('#paidYes').onclick=()=>setPaid(true); $('#paidNo').onclick=()=>setPaid(false);
$('#quitReport').onclick=()=>showTab('docs');

function payHTML(r){
  const e=r.expected;
  if(e.error) return `<div class="result warn"><p>${esc(e.error)}</p></div>`;
  const c=r.compare;
  return `<div class="pay">
    <span>근무시간 ${fmtH(e.work_min)} 기준 기본급</span><span class="num">${won(e.base)}</span>
    <span>주휴수당</span><span class="num">${won(e.weekly_holiday)}</span>
    <span>가산수당 (야간 ${fmtH(e.night_min)}, 연장 ${fmtH(e.overtime_min)})</span><span class="num">${won(e.premium)}</span>
    <span class="total">받아야 할 금액</span><span class="total num">${won(e.total)}</span>
    <span>받은 금액</span><span class="num">${r.paid==null?'미입력':won(r.paid)}</span></div>
    <div class="result ${c.status}" style="margin-top:12px"><div class="head"><span class="law">비교 결과</span><span class="tag ${c.status}">${LABEL[c.status]}</span></div><p>${esc(c.text)}</p></div>
    ${e.notes.map(n=>`<p class="hint">${esc(n)}</p>`).join('')}`;
}
async function loadPay(){ const r=await api('GET',`/api/jobs/${state.current}/pay?month=${$('#payMonth').value}`); $('#payBody').innerHTML=payHTML(r); }
$('#payMonth').onchange=()=>loadPay().catch(e=>toast(e.message));
$('#payRun').onclick=async()=>{ try{ const r=await api('POST',`/api/jobs/${state.current}/agent/payday?month=${$('#payMonth').value}`); $('#payBody').innerHTML=payHTML(r); toast('에이전트가 급여를 점검했어요'); }catch(e){ toast(e.message); } };
$('#paySave').onclick=async()=>{
  const amt=$('#payAmount').value; if(!amt){ toast('받은 금액을 입력해 주세요'); return; }
  const fd=new FormData(); fd.append('month',$('#payMonth').value); fd.append('amount',amt); const f=$('#payFile').files[0]; if(f) fd.append('file',f);
  try{ const r=await api('POST',`/api/jobs/${state.current}/payslip`,fd,true); $('#payBody').innerHTML=payHTML(r); $('#payAmount').value=''; $('#payFile').value=''; toast('저장하고 비교했어요'); }catch(e){ toast(e.message); }
};

// 점검
async function loadCheck(){
  const f=await api('GET',`/api/jobs/${state.current}/contract/fields`);
  $('#fieldsBox').innerHTML=f.items.map(k=>`<label class="field"><span>${esc(k)}</span><input class="input" data-k="${esc(k)}" value="${esc(f.fields[k]||'')}" placeholder="계약서에 없으면 비워 두세요"></label>`).join('');
  const r=await api('GET',`/api/jobs/${state.current}/check`); renderCheck(r.items);
  await loadLog();
}
function renderCheck(items){
  if(!items){ $('#checkSummary').innerHTML=''; $('#checkItems').innerHTML='<p class="sub">아직 점검하지 않았어요. 계약서 내용을 확인하고 점검해 보세요.</p>'; return; }
  const c=s=>items.filter(i=>i.status===s).length;
  $('#checkSummary').innerHTML=`<div class="ok"><strong>${c('ok')}</strong>정상</div><div class="warn"><strong>${c('warn')}</strong>확인 필요</div><div class="bad"><strong>${c('bad')}</strong>위반 의심</div>`;
  const order={bad:0,warn:1,ok:2}; $('#checkItems').innerHTML=[...items].sort((a,b)=>order[a.status]-order[b.status]).map(itemHTML).join('');
}
async function loadLog(){ const logs=await api('GET',`/api/jobs/${state.current}/agent/log`);
  $('#agentLog').innerHTML=logs.length?logs.map(l=>`<div class="log"><b>${esc(l.event)} ${esc(l.step)}</b> ${esc(l.detail)}</div>`).join(''):'<p class="sub">기록이 없어요</p>'; }
$('#contractFile').onchange=async e=>{ const f=e.target.files[0]; if(!f) return; const fd=new FormData(); fd.append('file',f);
  try{ await api('POST',`/api/jobs/${state.current}/contract`,fd,true); toast('계약서 원본을 저장했어요. 아래 칸을 채워 주세요'); }catch(err){ toast(err.message); } e.target.value=''; };
$('#checkRun').onclick=async()=>{
  const fields={}; $$('#fieldsBox input').forEach(i=>fields[i.dataset.k]=i.value.trim());
  try{ await api('PUT',`/api/jobs/${state.current}/contract/fields`,{fields}); const r=await api('POST',`/api/jobs/${state.current}/check`);
    renderCheck(r.items); await loadLog(); toast('점검을 마쳤어요'); }catch(e){ toast(e.message); }
};

// 자료
async function loadDocs(){
  const evs=await api('GET',`/api/jobs/${state.current}/evidence`);
  $('#evList').innerHTML=evs.length?evs.map(e=>`<li><div class="main"><strong>${esc(KIND[e.kind]||e.kind)}</strong>
    <div class="sub">${esc(e.filename)}, ${fmtDT(e.uploaded_at)} 올림</div><a class="ev-link" href="/api/evidence/${e.id}/file" target="_blank">원본 보기</a></div><span class="tag ok">원본</span></li>`).join('')
    :'<li><span class="sub">아직 올린 자료가 없어요</span></li>';
  const cs=await api('GET','/api/counsel');
  $('#counsel').innerHTML=cs.map(c=>`<div class="panel"><strong>${esc(c.name)}</strong><div class="sub">${esc(c.note)}</div><div class="num" style="margin-top:4px">${esc(c.phone)}</div></div>`).join('');
}
$('#evFile').onchange=async e=>{ const f=e.target.files[0]; if(!f) return; const fd=new FormData(); fd.append('file',f); fd.append('kind',$('#evKind').value);
  try{ await api('POST',`/api/jobs/${state.current}/evidence`,fd,true); await loadDocs(); toast('자료를 원본으로 저장했어요'); }catch(err){ toast(err.message); } e.target.value=''; };
$('#reportBtn').onclick=async()=>{ try{ const r=await api('POST',`/api/jobs/${state.current}/report`); window.open(r.url,'_blank'); toast('상담 사전 자료를 만들었어요'); }catch(e){ toast(e.message); } };

// 보호
async function loadGuard(){ renderGuard(await api('GET',`/api/jobs/${state.current}/guard`)); }
function renderGuard(g){
  $('#reported').checked=g.reported; $('#guardOn').classList.toggle('hidden',!g.reported); $('#warnMsg').value=g.message;
  const st={pending:['warn','판별 대기'],suspect:['bad','보복 의심'],ok:['ok','문제 없음']};
  $('#postList').innerHTML=g.posts.length?g.posts.map(p=>`<li class="post"><div class="main"><strong>${esc(p.title||'제목 없음')}</strong>
    <div class="sub"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.url)}</a></div>
    <div class="sub">${fmtDT(p.found_at)} 확인${p.evidence_id?`, <a class="ev-link" href="/api/evidence/${p.evidence_id}/file" target="_blank">보존한 화면</a>`:', 화면 캡처 없음'}</div></div>
    <span class="tag ${st[p.status][0]}">${st[p.status][1]}</span></li>`).join(''):'<li><span class="sub">아직 확인한 게시물이 없어요</span></li>';
}
$('#reported').onchange=async e=>{ try{ renderGuard(await api('POST',`/api/jobs/${state.current}/guard`,{reported:e.target.checked})); }catch(err){ toast(err.message); } };
$('#copyMsg').onclick=()=>{ const t=$('#warnMsg').value; (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>toast('안내 문구를 복사했어요'),()=>toast('직접 선택해 복사해 주세요')); };
$('#postAdd').onclick=async()=>{ const url=$('#postUrl').value.trim(); if(!url){ toast('게시물 주소를 넣어 주세요'); return; }
  try{ await api('POST',`/api/jobs/${state.current}/guard/posts`,{url}); $('#postUrl').value=''; await loadGuard(); toast('주소와 확인 시각을 보존했어요'); }catch(e){ toast(e.message); } };
$('#postSearch').onclick=async()=>{ try{ const r=await api('POST',`/api/jobs/${state.current}/guard/search`); toast(r.skipped?r.reason:`새 게시물 ${r.added}건을 찾았어요`); await loadGuard(); }catch(e){ toast(e.message); } };

boot();
