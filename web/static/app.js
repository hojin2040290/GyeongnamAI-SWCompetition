// 알바지킴이 화면 동작. 모든 기록과 판단은 서버(API)에서 처리하고, 화면은 보여주기와 입력만 맡는다.
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const DAY_KEYS = ['월','화','수','목','금','토','일'];
const TIMES = []; for (let h=0; h<24; h++) { TIMES.push(pad(h)+':00'); TIMES.push(pad(h)+':30'); }
const BREAKS = ['없음','30분','1시간','1시간 30분','2시간','모름'];
const LABEL = {ok:'정상', warn:'확인 필요', bad:'위반 의심'};
const EVENT = {contract_check:'계약서 점검', shift_check:'퇴근 점검', seek_check:'지원 전 확인', payday:'급여 점검', quit_check:'퇴직 정산',
  report:'상담 자료', guard_on:'보복 대응 시작', guard_off:'보복 대응 끔', guard_search:'게시물 검색', guard_preserve:'게시물 보존'};
const KIND = {contract:'근로계약서', payslip:'급여명세서', message:'사업주 메시지', schedule:'근무표', deposit:'입금 내역', post:'게시물 화면', notice:'채용공고', other:'기타'};

const state = { me:null, jobs:[], current:null, mode:null, cards:[], adding:false, seekFromApp:false,
  inApp:false, editingJob:null, curOb:null, tabHist:[] };

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

// ---------- 바로 저장 ----------
// 입력이 멈추면 바로 서버에 저장한다. 저장 전에 창을 닫으려 하면 경고한다.
const saveTimers={}, pendingSaves=new Map();
function autosave(key, fn, statusEl, delay=700){
  clearTimeout(saveTimers[key]); pendingSaves.set(key, fn);
  if(statusEl) $(statusEl).textContent='저장 중…';
  saveTimers[key]=setTimeout(()=>flushSave(key, statusEl), delay);
}
async function flushSave(key, statusEl){
  const fn=pendingSaves.get(key); if(!fn) return; clearTimeout(saveTimers[key]); pendingSaves.delete(key);
  try{ await fn(); if(statusEl) $(statusEl).textContent='자동 저장됨'; }
  catch(e){ if(statusEl) $(statusEl).textContent='저장하지 못했어요. 다시 입력해 주세요'; toast(e.message); }
}
function flushAll(){ return Promise.all([...pendingSaves.keys()].map(k=>flushSave(k))); }
function lsGet(k){ try{ return localStorage.getItem(k); }catch(e){ return null; } }
function lsSet(k,v){ try{ localStorage.setItem(k,v); }catch(e){} }
window.addEventListener('beforeunload', e=>{
  if(pendingSaves.size || (visible('#onboard') && state.curOb==='ob2' && state.formDirty)){ flushAll(); e.preventDefault(); e.returnValue=''; }
});

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

function show(id){
  ['ob0','ob1','obLogin','obSeek','ob2','obMe'].forEach(x=>$('#'+x).classList.toggle('hidden', x!==id));
  state.curOb=id; if(id==='ob0') startScreen(); window.scrollTo(0,0); refreshNav();
}
function openOverlay(id){ $('#onboard').classList.remove('hidden'); show(id); }
function closeOverlay(){ $('#onboard').classList.add('hidden'); state.curOb=null; refreshNav(); }
function visible(id){ return !$(id).classList.contains('hidden'); }

// ---------- 뒤로 가기 ----------
// 화면 위 뒤로 버튼, 각 화면의 뒤로 버튼, 휴대폰과 브라우저의 뒤로 버튼이 모두 같은 규칙을 따른다.
function backTarget(){
  if(visible('#sheetBg')) return closeSheet;
  if(visible('#pickerBg')) return closePicker;
  if(visible('#onboard')){
    switch(state.curOb){
      case 'ob1': case 'obLogin': return ()=>show('ob0');
      case 'obSeek':
        if(visible('#seekResult')) return seekBackToForm;
        return state.seekFromApp ? ()=>{ state.seekFromApp=false; closeOverlay(); } : ()=>show('ob0');
      case 'ob2': return state.inApp ? cancelJobs : ()=>{ if(!confirmLeave()) return; clearCards(); show('ob0'); };
      case 'obMe': return closeOverlay;
      default: return null;
    }
  }
  if(!state.inApp) return null;
  if(visible('#quitForm')) return closeQuitForm;
  if(state.tabHist.length) return ()=>showTab(state.tabHist.pop(), false);
  if(currentTab!=='home') return ()=>showTab('home', false);
  return null;
}
let skipPop=false;
function goBack(){
  const t=backTarget(); if(t) t();
  if(!backTarget() && history.state?.albaBack){ skipPop=true; history.back(); }
  refreshNav();
}
function refreshNav(){
  const can=!!backTarget();
  $('#backBtn').classList.toggle('hidden', !can);
  if(can && !history.state?.albaBack) history.pushState({albaBack:true}, '');
}
window.addEventListener('popstate', ()=>{
  if(skipPop){ skipPop=false; return; }
  const t=backTarget(); if(t) t(); refreshNav();
});
$('#backBtn').onclick=goBack;
$$('.ob-back').forEach(b=>b.onclick=goBack);

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
    if (!state.jobs.length) openOverlay('ob0');
    else startApp();
  } catch(e) {
    if (e.status === 401) openOverlay('ob0'); else toast(e.message);
  }
}

$$('.mode').forEach(b => b.addEventListener('click', () => {
  $$('.mode').forEach(x=>x.setAttribute('aria-checked','false'));
  b.setAttribute('aria-checked','true'); state.mode=b.dataset.mode; $('#modeNext').disabled=false;
}));
function startScreen(){
  $('#toLoginWrap').classList.toggle('hidden', !!state.me); $('#obLogoutWrap').classList.toggle('hidden', !state.me);
}
function afterMode(){
  if (state.mode==='seek') { seekReset(); show('obSeek'); return; }
  clearCards(); prepJobForm(false); show('ob2'); const c=addCard(); if(state.mode==='quit') setSeg(c.node,'status','quit');
}
$('#modeNext').onclick = () => { if(state.me) afterMode(); else show('ob1'); };
$('#obLogout').onclick = async () => { await api('POST','/api/auth/logout'); location.reload(); };
$('#toLogin').onclick = () => show('obLogin');
$('#toStart').onclick = () => show('ob0');

$('#regBtn').onclick = async () => {
  $('#regErr').textContent='';
  const email=$('#regEmail').value.trim(), password=$('#regPw').value, birth_date=$('#birth').value;
  if(!email||!password||!birth_date){ $('#regErr').textContent='이메일, 비밀번호, 생년월일을 모두 입력해 주세요'; return; }
  try {
    state.me = await api('POST','/api/auth/register',{email,password,birth_date,mode:state.mode||'work'});
    afterMode();
  } catch(e) { $('#regErr').textContent=e.message; }
};
$('#logBtn').onclick = async () => {
  $('#logErr').textContent='';
  try {
    state.me = await api('POST','/api/auth/login',{email:$('#logEmail').value.trim(),password:$('#logPw').value});
    await loadJobs();
    if (!state.jobs.length) show('ob0'); else { closeOverlay(); startApp(); }
  } catch(e) { $('#logErr').textContent=e.message; }
};

// ---------- 사업장 입력 카드 ----------
function addCard(){
  const node=$('#jobTpl').content.firstElementChild.cloneNode(true);
  const card={node, schedule:{}}; state.cards.push(card);
  const name=node.querySelector('.f-name');
  name.addEventListener('input',()=>{ node.querySelector('.job-title').textContent=name.value.trim()||'새 일하는 곳'; });
  node.querySelector('.remove').onclick=async()=>{
    if(state.editingJob){ await deleteJob(state.editingJob); return; }
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
function refreshRemove(){ state.cards.forEach(c=>c.node.querySelector('.remove').classList.toggle('hidden',state.cards.length===1&&!state.editingJob)); }
function clearCards(){ state.cards.forEach(c=>c.node.remove()); state.cards=[]; }
function setSched(card, schedule){
  card.schedule=JSON.parse(JSON.stringify(schedule||{}));
  const has=DAY_KEYS.some(k=>card.schedule[k]);
  card.node.querySelector('.sched-sum').textContent=has?schedSummary(card.schedule):'요일과 시간을 선택해 주세요';
  card.node.querySelector('.sched-go').textContent=has?'수정':'선택';
}
function ynV(v){ return v===true?'yes':(v===false?'no':null); }
function fillCard(card, j){
  const n=card.node, q=s=>n.querySelector(s);
  setSeg(n,'status',j.status); q('.f-quit').value=j.quit_date||'';
  q('.f-name').value=j.name; q('.f-name').dispatchEvent(new Event('input'));
  q('.f-type').value=j.industry||''; q('.f-work').value=j.work_desc||''; q('.f-wage').value=j.wage??'';
  q('.f-start').value=j.start_date||''; q('.f-end').value=j.end_date||'';
  q('.f-noend').checked=!!j.no_end; q('.f-end').disabled=!!j.no_end;
  if(ynV(j.contract_written)) setSeg(n,'contract',ynV(j.contract_written));
  if(ynV(j.copy_received)) setSeg(n,'copy',ynV(j.copy_received));
  setSeg(n,'probation',j.probation||'unknown'); q('.f-probmonths').value=j.probation_months??'';
  setSched(card, j.schedule);
  if(j.size) setSeg(n,'size',j.size);
  if(j.pay_cycle) setSeg(n,'paytype',j.pay_cycle);
  q('.f-payday').value=j.payday??'';
  if(j.pay_method) setSeg(n,'paymethod',j.pay_method);
  if(j.deduction) setSeg(n,'deduct',j.deduction);
  if(j.consent) setSeg(n,'consent',j.consent);
  q('.f-addr').value=j.address||''; q('.f-owner').value=j.owner||'';
  checkMinor(card);
}
// 등록 화면을 새로 등록, 추가, 수정 중 어느 용도로 쓸지 정한다
function prepJobForm(mode){
  state.editingJob = mode==='edit' ? state.current : null; state.formDirty=false;
  const inApp = mode==='add' || mode==='edit';
  $('#ob2Step').textContent = mode==='edit' ? '아르바이트 정보' : (inApp ? '새 일하는 곳' : '2 / 2 아르바이트 정보');
  $('#ob2Title').textContent = mode==='edit' ? '일하는 곳 정보 수정' : '일하는 곳을 등록해 주세요';
  $('#addJobBtn').classList.toggle('hidden', mode==='edit');
  $('#saveJobsBtn').textContent = inApp ? '저장하기' : '시작하기';
  $('#cancelJobsBtn').classList.toggle('hidden', !inApp);
  $('#jobErr').textContent='';
}
function openJobEditor(job){
  closePicker(); state.current=job.id; clearCards(); prepJobForm('edit');
  openOverlay('ob2'); const c=addCard(); fillCard(c, job);
  c.node.querySelector('.remove').textContent='이곳 삭제'; refreshRemove(); state.formDirty=false;
}
async function deleteJob(id){
  const j=state.jobs.find(x=>x.id===id);
  if(!confirm(`${j.name}을(를) 삭제할까요? 근무 기록과 자료도 이 사업장에서 더 이상 보이지 않아요.`)) return;
  try{ await api('DELETE',`/api/jobs/${id}`); await loadJobs(); clearCards(); state.editingJob=null;
    if(!state.jobs.length){ state.inApp=false; state.current=null; $('#app').classList.add('hidden'); $('#tabs').classList.add('hidden'); show('ob0'); return; }
    state.current=state.jobs[0].id; closeOverlay(); startApp(); toast('삭제했어요');
  }catch(e){ toast(e.message); }
}
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
    if(state.editingJob){
      await api('PUT',`/api/jobs/${state.editingJob}`,collect(state.cards[0]));
      clearCards(); state.editingJob=null; state.formDirty=false; await loadJobs(); closeOverlay(); startApp(); toast('정보를 고쳤어요. 점검을 다시 해 보세요'); return;
    }
    let last=null;
    for(const c of state.cards.filter(c=>!c.saved)){ last = await api('POST','/api/jobs',collect(c)); c.saved=true; }
    clearCards(); state.formDirty=false;
    await loadJobs();
    state.current = state.adding||!state.current ? (last?.id ?? state.jobs[0].id) : state.current;
    state.adding=false; closeOverlay(); startApp();
    toast(`${curJob().name} 기록을 보고 있어요`);
  }catch(e){ $('#jobErr').textContent=e.message; }
};
function confirmLeave(){ return !state.formDirty || confirm('저장하지 않은 내용이 있어요. 저장하지 않고 나갈까요?'); }
function cancelJobs(){ if(!confirmLeave()) return; clearCards(); state.adding=false; state.editingJob=null; state.formDirty=false; closeOverlay(); }
['input','change'].forEach(ev=>$('#jobList').addEventListener(ev,()=>{ state.formDirty=true; }));
$('#jobList').addEventListener('click',e=>{ if(e.target.closest('.seg button')) state.formDirty=true; });
$('#cancelJobsBtn').onclick=cancelJobs;

// ---------- 근무 시간 선택 ----------
let editing=null, draft=null;
function openSheetFor(target){
  editing=target; draft=JSON.parse(JSON.stringify(target.schedule||{}));
  const nm=target.node.querySelector('.f-name')?.value.trim();
  $('#sheetJobName').textContent=nm?`${nm}에서 일하는 시간을 골라 주세요`:'일하는 시간을 골라 주세요';
  renderDays(); renderSlots(); $('#sheetBg').classList.remove('hidden'); document.body.style.overflow='hidden'; refreshNav();
}
function closeSheet(){ $('#sheetBg').classList.add('hidden'); document.body.style.overflow=''; editing=null; refreshNav(); }
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
$('#sheetClose').onclick=goBack;
$('#sheetBg').onclick=e=>{ if(e.target.id==='sheetBg') goBack(); };
$('#sheetSave').onclick=()=>{
  if(!DAY_KEYS.some(k=>draft[k])){ toast('일하는 요일을 하나 이상 골라 주세요'); return; }
  if(editing!==seek) state.formDirty=true;
  editing.schedule=draft; editing.node.querySelector('.sched-sum').textContent=schedSummary(draft);
  editing.node.querySelector('.sched-go').textContent='수정'; goBack(); toast('근무 시간을 저장했어요');
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
function articleHTML(a){
  if(!a) return '';
  if(!a.built) return `<p class="basis"><span class="chip muted">법 기준표 미구축</span> 법제처 API로 조문을 불러오면 원문이 붙어요.</p>`;
  return `<details class="law-text"><summary>조문 원문 보기${a.title?` (${esc(a.title)})`:''}</summary><pre>${esc(a.text)}</pre></details>`;
}
function itemHTML(it){
  const extra=[]; if(it.basis?.length) extra.push(`근거로 쓴 사실: ${esc(it.basis.join(', '))}`);
  if(it.needed?.length) extra.push(`필요한 정보: ${esc(it.needed.join(', '))}`);
  const src=it.source==='records'?'<span class="chip">근무 기록</span>':'';
  return `<div class="result ${it.status}"><div class="head"><span class="law">${esc(it.law)} ${src}</span><span class="tag ${it.status}">${LABEL[it.status]}</span></div>
    <p>${esc(it.text)}</p>${extra.map(x=>`<p class="basis">${x}</p>`).join('')}${articleHTML(it.article)}</div>`;
}
// 에이전트가 거친 단계 (입력, 판단, 도구 실행, 결과)
function traceHTML(trace){
  if(!trace?.length) return '';
  return `<details class="more trace"><summary>에이전트 동작 보기 (${trace.length}단계)</summary>${trace.map(t=>`<div class="log"><b>${esc(t.step)}</b> ${esc(t.detail)}</div>`).join('')}</details>`;
}
$('#seekRun').onclick=async()=>{
  $('#seekErr').textContent='';
  try{ const r=await api('POST','/api/seek/check',seekInput());
    $('#seekItems').innerHTML=r.items.map(itemHTML).join('');
    $('#seekQs').innerHTML=r.questions.map(q=>`<li>${esc(q)}</li>`).join('');
    $('#seekTrace').innerHTML=traceHTML(r.trace);
    $('#seekForm').classList.add('hidden'); $('#seekUpload').classList.add('hidden'); $('#seekResult').classList.remove('hidden');
    $('#seekClose').classList.toggle('hidden',!state.seekFromApp); window.scrollTo(0,0); refreshNav();
  }catch(e){ $('#seekErr').textContent=e.message; }
};
function seekReset(){ const n=seek.node; n.querySelectorAll('input').forEach(i=>i.value=''); n.querySelector('.f-type').value='';
  n.querySelectorAll('.seg button').forEach(b=>b.setAttribute('aria-pressed','false')); seek.schedule={};
  n.querySelector('.sched-sum').textContent='요일과 시간을 선택해 주세요'; n.querySelector('.sched-go').textContent='선택';
  seekBackToForm(); }
// 결과에서 뒤로: 입력한 내용은 그대로 두고 입력 화면으로
function seekBackToForm(){ $('#seekResult').classList.add('hidden'); $('#seekForm').classList.remove('hidden'); $('#seekUpload').classList.remove('hidden'); window.scrollTo(0,0); refreshNav(); }
$('#seekAgain').onclick=seekReset;
$('#seekClose').onclick=()=>{ state.seekFromApp=false; closeOverlay(); };
$('#seekToWork').onclick=()=>{
  const d=seekInput(); state.adding=state.inApp; state.seekFromApp=false;
  clearCards(); prepJobForm(state.inApp?'add':false); show('ob2'); const c=addCard(), n=c.node;
  n.querySelector('.f-name').value=d.name; n.querySelector('.f-name').dispatchEvent(new Event('input'));
  n.querySelector('.f-type').value=d.industry; n.querySelector('.f-work').value=d.work_desc; n.querySelector('.f-wage').value=d.wage??'';
  c.schedule=JSON.parse(JSON.stringify(d.schedule));
  if(DAY_KEYS.some(k=>c.schedule[k])){ n.querySelector('.sched-sum').textContent=schedSummary(c.schedule); n.querySelector('.sched-go').textContent='수정'; }
  if(d.probation!=='unknown') setSeg(n,'probation',d.probation);
  toast('공고 내용으로 채웠어요. 나머지를 확인해 주세요');
};
$('#seekEntry').onclick=()=>{ state.seekFromApp=true; seekReset(); openOverlay('obSeek'); };

// ---------- 앱 ----------
async function loadJobs(){ state.jobs=await api('GET','/api/jobs'); }
function curJob(){ return state.jobs.find(j=>j.id===state.current); }
function startApp(){
  if(!state.current||!curJob()) state.current=state.me.last_job_id;
  if(!state.current||!curJob()) state.current=state.jobs[0].id;
  if(!state.inApp){ const t=lsGet('alba.tab'); if(TABS.includes(t)) currentTab=t; }
  $('#gpsOn').checked=!!state.me.gps_consent;
  state.inApp=true; closeOverlay(); $('#app').classList.remove('hidden'); $('#tabs').classList.remove('hidden');
  applyCurrent(); showTab(currentTab, false);
}
function rememberJob(){
  if(state.me && state.me.last_job_id!==state.current){
    state.me.last_job_id=state.current;
    api('PUT','/api/me/prefs',{last_job_id:state.current}).catch(e=>toast(e.message));
  }
}
function applyCurrent(){ rememberJob(); const j=curJob(); $$('.cur-job').forEach(el=>el.textContent=j.name); $('#schedLine').textContent=schedSummary(j.schedule); }
const TABS=['home','pay','check','docs','guard'];
let currentTab='home';
function showTab(v, push=true){
  if(push && v!==currentTab){ state.tabHist.push(currentTab); if(state.tabHist.length>20) state.tabHist.shift(); }
  flushAll(); currentTab=v; lsSet('alba.tab', v);
  $$('nav.tabs button').forEach(b=>b.toggleAttribute('aria-current',b.dataset.v===v)); $$('nav.tabs button[aria-current]').forEach(b=>b.setAttribute('aria-current','page'));
  $$('.view').forEach(x=>x.classList.toggle('active',x.id==='v-'+v)); window.scrollTo(0,0);
  ({home:loadHome, pay:loadPayTab, check:loadCheck, docs:loadDocs, guard:loadGuard})[v]().catch(e=>toast(e.message));
  refreshNav();
}
$$('nav.tabs button').forEach(b=>b.onclick=()=>showTab(b.dataset.v));

// 일하는 곳 선택
$('#jobSwitch').onclick=()=>{
  const box=$('#placeList'); box.innerHTML='';
  state.jobs.forEach(j=>{ const b=document.createElement('button'); b.type='button'; b.className='place'; b.setAttribute('role','radio');
    b.setAttribute('aria-checked',j.id===state.current?'true':'false');
    b.innerHTML=`<span class="radio" aria-hidden="true"></span><span class="info"><strong>${esc(j.name)}</strong><span>${j.status==='quit'?'그만둔 곳, ':''}${esc(schedSummary(j.schedule))}</span></span>`;
    b.onclick=()=>{ flushAll(); state.current=j.id; applyCurrent(); closePicker(); showTab(currentTab, false); toast(`${j.name} 기록을 보고 있어요`); };
    const row=document.createElement('div'); row.className='place-row';
    const ed=document.createElement('button'); ed.type='button'; ed.className='btn ghost small'; ed.textContent='수정';
    ed.setAttribute('aria-label',`${j.name} 정보 수정`); ed.onclick=()=>openJobEditor(j);
    row.append(b, ed); box.appendChild(row); });
  $('#pickerBg').classList.remove('hidden'); document.body.style.overflow='hidden'; refreshNav();
};
function closePicker(){ $('#pickerBg').classList.add('hidden'); document.body.style.overflow=''; refreshNav(); }
$('#pickerClose').onclick=goBack;
$('#pickerBg').onclick=e=>{ if(e.target.id==='pickerBg') goBack(); };
$('#addPlace').onclick=()=>{ closePicker(); state.adding=true; clearCards(); prepJobForm('add'); openOverlay('ob2'); addCard(); };
$('#editMe').onclick=()=>{ closePicker(); $('#meEmail').value=state.me.email; $('#meBirth').value=state.me.birth_date; $('#meErr').textContent=''; openOverlay('obMe'); };
$('#meSave').onclick=async()=>{
  const v=$('#meBirth').value; if(!v){ $('#meErr').textContent='생년월일을 입력해 주세요'; return; }
  try{ state.me=await api('PUT','/api/me',{birth_date:v}); closeOverlay(); showTab(currentTab,false); toast('생년월일을 고쳤어요. 점검을 다시 해 보세요'); }
  catch(e){ $('#meErr').textContent=e.message; }
};
$('#editJobBtn').onclick=()=>openJobEditor(curJob());
$('#logoutBtn').onclick=async()=>{ await api('POST','/api/auth/logout'); location.reload(); };
document.addEventListener('keydown',e=>{ if(e.key==='Escape' && backTarget() && (visible('#sheetBg')||visible('#pickerBg'))) goBack(); });

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
  const j=curJob(); applyCurrent();
  await refreshRecords();
  if(state.shiftFor!==j.id) $('#shiftResult').classList.add('hidden');
  await renderQuit();
  await loadAlerts();
}
// 알림: 지금 보고 있는 일하는 곳의 알림만 보여 준다
async function loadAlerts(){
  const jid=state.current, ns=await api('GET',`/api/notifications?job_id=${jid}`);
  $('#alertsClear').classList.toggle('hidden',!ns.length);
  $('#alerts').innerHTML=ns.length?ns.map(n=>`<div class="alert"><span class="dot ${n.read?'read':''}"></span>
      <div class="main"><strong>${esc(n.title)}</strong><div class="sub">${esc(n.body)}</div><div class="sub note-at">${fmtDT(n.at)}</div></div>
      <button class="note-x" data-note="${n.id}" aria-label="${esc(n.title)} 알림 지우기">×</button></div>`).join('')
    :'<p class="sub" style="margin:0">아직 알림이 없어요. 점검 결과가 생기면 여기에 알려드려요.</p>';
  if(ns.some(n=>!n.read)) api('POST',`/api/notifications/read?job_id=${jid}`);
}
$('#alerts').addEventListener('click',async e=>{
  const b=e.target.closest('[data-note]'); if(!b) return;
  try{ await api('DELETE',`/api/notifications/${b.dataset.note}`); await loadAlerts(); }catch(err){ toast(err.message); }
});
$('#alertsClear').onclick=async()=>{
  if(!confirm('이곳 알림을 모두 지울까요? 점검 결과와 기록은 그대로 남아요.')) return;
  try{ await api('DELETE',`/api/notifications?job_id=${state.current}`); await loadAlerts(); toast('알림을 지웠어요'); }catch(e){ toast(e.message); }
};
$('#gpsOn').onchange=async e=>{
  try{ state.me=await api('PUT','/api/me/prefs',{gps_consent:e.target.checked}); toast(e.target.checked?'위치 기록에 동의했어요':'위치를 기록하지 않아요'); }
  catch(err){ e.target.checked=!e.target.checked; toast(err.message); }
};
function getPos(){ return new Promise(res=>{ if(!navigator.geolocation) return res(null);
  navigator.geolocation.getCurrentPosition(p=>res({lat:p.coords.latitude,lng:p.coords.longitude}),()=>res(null),{timeout:8000,maximumAge:0}); }); }
$('#punchBtn').onclick=async()=>{
  const btn=$('#punchBtn'); btn.disabled=true;
  try{ let pos=null; if($('#gpsOn').checked){ pos=await getPos(); if(!pos) toast('위치를 가져오지 못해 시각만 기록해요'); }
    let r;
    try{ r=await api('POST',`/api/jobs/${state.current}/punch`,pos||{}); }
    catch(e){
      if(e.status!==409) throw e;
      // 방금 출근했거나 퇴근을 오래 안 눌렀을 때는 한 번 더 확인한다
      if(!confirm(e.message)){ toast('기록하지 않았어요'); return; }
      r=await api('POST',`/api/jobs/${state.current}/punch`,{...(pos||{}), confirm:true});
    }
    const t=fmtDT(r.server_time);
    setPunchUI(r.action==='in', r.action==='in'?`${t} 출근 기록됨${pos?', 위치 함께 기록':''}`:`${t} 퇴근 기록됨`);
    if(r.shift) renderShift(r.shift);
    await refreshRecords(false);
    toast(r.action==='in'?'출근이 기록됐어요':(r.shift?.items?.length?'퇴근 기록, 오늘 근무에서 확인할 점이 있어요':'퇴근이 기록됐어요'));
  }catch(e){ toast(e.message); } finally{ btn.disabled=curJob().status==='quit'; }
};

// 근무 기록 (홈)
function thisMonth(){ const d=new Date(); return `${d.getFullYear()}-${pad(d.getMonth()+1)}`; }
// 출퇴근 버튼 상태, 퇴근 잊음 안내, 기록 목록을 한 번에 새로 그린다
async function refreshRecords(updateState=true){
  const r=await api('GET',`/api/jobs/${state.current}/records`);
  const live=r.records.filter(x=>!x.void), open=live.find(x=>!x.clock_out), last=live.find(x=>x.clock_out);
  if(updateState) setPunchUI(r.working, open?`${fmtDT(open.clock_in)} 출근 기록됨`:(last?`마지막 퇴근 ${fmtDT(last.clock_out)}`:'아직 출근 기록이 없어요'));
  else setPunchUI(r.working, $('#clockState').textContent);
  const box=$('#openAlert');
  if(open && open.hours>=r.open_alert_hours){
    box.classList.remove('hidden');
    box.innerHTML=`<strong>퇴근을 누르지 않은 것 같아요</strong><p class="sub" style="margin:4px 0 10px">${fmtDT(open.clock_in)}에 출근한 뒤 ${Math.floor(open.hours)}시간 동안 퇴근 기록이 없어요.
      일을 마쳤다면 퇴근을 누르고, 잘못 누른 출근이면 실수로 표시해 주세요. 퇴근을 늦게 누르면 그 시간까지 근무로 계산돼요.</p>
      <button class="btn ghost block" data-void="${open.id}">이 출근 기록을 실수로 표시</button>`;
  } else box.classList.add('hidden');
  renderRecords(r.records);
}
function renderRecords(recs){
  $('#recordsEmpty').classList.toggle('hidden',recs.length>0); $('#recordsPanel').classList.toggle('hidden',!recs.length);
  $('#records').innerHTML=recs.map(x=>{
    const when=`${fmtDT(x.clock_in).split(' ')[2]} 출근, ${x.clock_out?fmtDT(x.clock_out).split(' ')[2]+' 퇴근':(x.void?'퇴근 없음':'근무 중')}`;
    const side=x.void
      ? `<button class="btn ghost small" data-unvoid="${x.id}">표시 취소</button>`
      : `<span class="rec-side"><span class="tag ${x.gps?'ok':'warn'}">${x.gps?'위치 기록':'위치 미기록'}</span><button class="link muted small" data-void="${x.id}">실수로 누름</button></span>`;
    const note=x.void?`<div class="sub void-note">실수로 표시함 (${fmtDT(x.void_at)}), ${esc(x.void_reason)}. 급여 계산과 점검에서 빠져요</div>`:'';
    return `<li class="${x.void?'void':''}"><div class="main"><strong class="num">${fmtDT(x.clock_in).split(' ').slice(0,2).join(' ')}</strong>
      <div class="sub num rec-time">${when}</div>${note}</div>${side}</li>`;
  }).join('');
}
// 실수 표시: 기록은 지우지 않고 표시만 한다
async function voidRecord(id){
  const reason=prompt('실수로 누른 기록으로 표시할까요? 시각은 그대로 남고 급여 계산과 점검에서만 빠져요.\n이유를 적어 주세요 (예: 일 안 하는 날 잘못 누름)','실수로 누름');
  if(reason===null) return;
  try{ await api('POST',`/api/jobs/${state.current}/records/${id}/void`,{reason}); await refreshRecords(); toast('실수로 표시했어요'); }catch(e){ toast(e.message); }
}
async function unvoidRecord(id){
  if(!confirm('실수 표시를 취소할까요? 다시 급여 계산과 점검에 들어가요.')) return;
  try{ await api('DELETE',`/api/jobs/${state.current}/records/${id}/void`); await refreshRecords(); toast('표시를 취소했어요'); }catch(e){ toast(e.message); }
}
document.addEventListener('click',e=>{
  const v=e.target.closest('[data-void]'), u=e.target.closest('[data-unvoid]');
  if(v) voidRecord(Number(v.dataset.void)); else if(u) unvoidRecord(Number(u.dataset.unvoid));
});
// 퇴근 직후 에이전트 점검 결과
function renderShift(sh){
  state.shiftFor=state.current;
  const box=$('#shiftResult'); box.classList.remove('hidden');
  const head=sh.items.length?`<strong>오늘 근무 점검</strong><p class="sub" style="margin:2px 0 10px">퇴근 기록으로 쉬는 시간, 청소년 근로시간 한도, 야간근로를 바로 확인했어요.</p>`
    :`<strong>오늘 근무 점검</strong><p class="sub" style="margin:2px 0 0">퇴근 기록에서 확인할 점을 찾지 못했어요.</p>`;
  box.innerHTML=`<div class="panel">${head}${sh.items.map(itemHTML).join('')}${traceHTML(sh.trace)}</div>`;
}
async function renderQuit(){
  const j=curJob(); const quit=j.status==='quit';
  $('#quitOpen').classList.toggle('hidden',quit); $('#quitForm').classList.add('hidden'); $('#quitPanel').classList.toggle('hidden',!quit); refreshNav();
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
function openQuitForm(){ const j=curJob(); $('#quitDateMain').value=j.quit_date||''; $('#quitForm').classList.remove('hidden'); $('#quitOpen').classList.add('hidden'); refreshNav(); }
function closeQuitForm(){ $('#quitForm').classList.add('hidden'); $('#quitOpen').classList.toggle('hidden',curJob().status==='quit'); refreshNav(); }
$('#quitOpen').onclick=openQuitForm; $('#quitEdit').onclick=openQuitForm; $('#quitCancel').onclick=goBack;
$('#quitSave').onclick=async()=>{ const v=$('#quitDateMain').value; if(!v){ toast('그만둔 날을 골라 주세요'); return; }
  try{ await api('POST',`/api/jobs/${state.current}/quit`,{quit_date:v}); await loadJobs(); closeQuitForm(); await loadHome(); toast('그만둔 날을 저장했어요'); }catch(e){ toast(e.message); } };
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
// 급여 점검 탭
async function loadPayTab(){ applyCurrent(); if(!$('#payMonth').value) $('#payMonth').value=thisMonth(); $('#payTrace').innerHTML=''; await loadPay(); await loadPayslips(); }
async function loadPay(){ const r=await api('GET',`/api/jobs/${state.current}/pay?month=${$('#payMonth').value}`); $('#payBody').innerHTML=payHTML(r); }
async function loadPayslips(){
  const ps=await api('GET',`/api/jobs/${state.current}/payslips`);
  $('#payslips').innerHTML=ps.length?ps.map(p=>`<li><div class="main"><strong class="num">${esc(p.month)}</strong><div class="sub num">${won(p.amount)}${p.evidence_id?`, <a class="ev-link" href="/api/evidence/${p.evidence_id}/file" target="_blank">명세서 원본</a>`:''}</div></div>
    <span class="row-btns"><button class="btn ghost small" data-edit="${esc(p.month)}" data-amt="${p.amount}">고치기</button><button class="btn ghost small danger" data-del="${esc(p.month)}">지우기</button></span></li>`).join('')
    :'<li><span class="sub">아직 저장한 받은 급여가 없어요</span></li>';
  $$('#payslips [data-edit]').forEach(b=>b.onclick=()=>{ $('#payMonth').value=b.dataset.edit; $('#payAmount').value=b.dataset.amt; $('#payAmount').focus(); loadPay().catch(e=>toast(e.message)); toast('금액을 고친 뒤 저장하고 비교하기를 눌러 주세요'); });
  $$('#payslips [data-del]').forEach(b=>b.onclick=async()=>{ if(!confirm(`${b.dataset.del} 받은 금액을 지울까요? 명세서 원본은 증거 자료로 남아요.`)) return;
    try{ await api('DELETE',`/api/jobs/${state.current}/payslips/${b.dataset.del}`); await loadPay(); await loadPayslips(); toast('지웠어요'); }catch(e){ toast(e.message); } });
}
$('#payMonth').onchange=()=>{ $('#payTrace').innerHTML=''; loadPay().catch(e=>toast(e.message)); };
$('#payRun').onclick=async()=>{ try{ const r=await api('POST',`/api/jobs/${state.current}/agent/payday?month=${$('#payMonth').value}`); $('#payBody').innerHTML=payHTML(r); $('#payTrace').innerHTML=traceHTML(r.trace); toast('에이전트가 급여를 점검했어요'); }catch(e){ toast(e.message); } };
$('#paySave').onclick=async()=>{
  const amt=$('#payAmount').value; if(!amt){ toast('받은 금액을 입력해 주세요'); return; }
  const fd=new FormData(); fd.append('month',$('#payMonth').value); fd.append('amount',amt); const f=$('#payFile').files[0]; if(f) fd.append('file',f);
  try{ const r=await api('POST',`/api/jobs/${state.current}/payslip`,fd,true); $('#payBody').innerHTML=payHTML(r); $('#payTrace').innerHTML=traceHTML(r.trace); $('#payAmount').value=''; $('#payFile').value=''; await loadPayslips(); toast('저장하고 비교했어요'); }catch(e){ toast(e.message); }
};

// 점검
async function loadCheck(){
  const f=await api('GET',`/api/jobs/${state.current}/contract/fields`);
  $('#fieldsBox').innerHTML=f.items.map(k=>`<label class="field"><span>${esc(k)}</span><input class="input" data-k="${esc(k)}" value="${esc(f.fields[k]||'')}" placeholder="계약서에 없으면 비워 두세요"></label>`).join('')
    +'<p class="hint save-state" id="fieldsSaved">입력하면 바로 저장돼요</p>';
  const jobId=state.current;
  $$('#fieldsBox input').forEach(i=>i.addEventListener('input',()=>autosave(`fields-${jobId}`,()=>saveFields(jobId),'#fieldsSaved')));
  const r=await api('GET',`/api/jobs/${state.current}/check`); renderCheck(r.items); $('#checkTrace').innerHTML='';
  const ls=await api('GET','/api/law/status');
  $('#lawStatus').textContent=ls.built?`법 기준표: ${Object.entries(ls.laws).map(([k,v])=>`${k} ${v}개 조문`).join(', ')} (법제처 API)`
    :'법 기준표 미구축: 법제처 API 키를 등록하고 조문을 불러오면 결과마다 조문 원문이 붙어요.';
  await loadLog();
}
function renderCheck(items){
  if(!items){ $('#checkSummary').innerHTML=''; $('#checkItems').innerHTML='<p class="sub">아직 점검하지 않았어요. 계약서 내용을 확인하고 점검해 보세요.</p>'; return; }
  const c=s=>items.filter(i=>i.status===s).length;
  $('#checkSummary').innerHTML=`<div class="ok"><strong>${c('ok')}</strong>정상</div><div class="warn"><strong>${c('warn')}</strong>확인 필요</div><div class="bad"><strong>${c('bad')}</strong>위반 의심</div>`;
  const order={bad:0,warn:1,ok:2}; $('#checkItems').innerHTML=[...items].sort((a,b)=>order[a.status]-order[b.status]).map(itemHTML).join('');
}
async function loadLog(){ const logs=await api('GET',`/api/jobs/${state.current}/agent/log`);
  $('#agentLog').innerHTML=logs.length?logs.map(l=>`<div class="log"><b>${esc(EVENT[l.event]||l.event)} ${esc(l.step)}</b> ${esc(l.detail)}</div>`).join(''):'<p class="sub">기록이 없어요</p>'; }
$('#contractFile').onchange=async e=>{ const f=e.target.files[0]; if(!f) return; const fd=new FormData(); fd.append('file',f);
  try{ await api('POST',`/api/jobs/${state.current}/contract`,fd,true); toast('계약서 원본을 저장했어요. 아래 칸을 채워 주세요'); }catch(err){ toast(err.message); } e.target.value=''; };
function readFields(){ const fields={}; $$('#fieldsBox input').forEach(i=>fields[i.dataset.k]=i.value.trim()); return fields; }
// 입력하던 사업장 번호를 고정해 두어, 저장 전에 다른 곳으로 바꿔도 섞이지 않게 한다
const fieldsCache={};
function saveFields(jobId){ return api('PUT',`/api/jobs/${jobId}/contract/fields`,{fields:fieldsCache[jobId]}); }
$('#fieldsBox').addEventListener('input',()=>{ fieldsCache[state.current]=readFields(); });
$('#checkRun').onclick=async()=>{
  fieldsCache[state.current]=readFields(); pendingSaves.delete(`fields-${state.current}`); clearTimeout(saveTimers[`fields-${state.current}`]);
  try{ await saveFields(state.current); $('#fieldsSaved').textContent='자동 저장됨'; const r=await api('POST',`/api/jobs/${state.current}/check`);
    renderCheck(r.items); $('#checkTrace').innerHTML=traceHTML(r.trace); await loadLog(); toast('점검을 마쳤어요'); }catch(e){ toast(e.message); }
};

// 자료
async function loadDocs(){
  const evs=await api('GET',`/api/jobs/${state.current}/evidence`);
  $('#evList').innerHTML=evs.length?evs.map(e=>`<li><div class="main"><strong>${esc(KIND[e.kind]||e.kind)}</strong>
    <div class="sub">${esc(e.filename)}, ${fmtDT(e.uploaded_at)} 올림</div><a class="ev-link" href="/api/evidence/${e.id}/file" target="_blank">원본 보기</a></div><span class="tag ok">원본</span></li>`).join('')
    :'<li><span class="sub">아직 올린 자료가 없어요</span></li>';
  const rs=await api('GET',`/api/jobs/${state.current}/reports`);
  $('#reportList').innerHTML=rs.map(r=>`<li><div class="main"><strong>상담 사전 자료</strong><div class="sub">${fmtDT(r.created_at)} 작성</div></div><a class="ev-link" href="${esc(r.url)}" target="_blank">열기</a></li>`).join('');
  $('#reportTrace').innerHTML='';
  const cs=await api('GET','/api/counsel');
  $('#counsel').innerHTML=cs.map(c=>`<div class="panel"><strong>${esc(c.name)}</strong><div class="sub">${esc(c.note)}</div><div class="num" style="margin-top:4px">${esc(c.phone)}</div></div>`).join('');
}
$('#evFile').onchange=async e=>{ const f=e.target.files[0]; if(!f) return; const fd=new FormData(); fd.append('file',f); fd.append('kind',$('#evKind').value);
  try{ await api('POST',`/api/jobs/${state.current}/evidence`,fd,true); await loadDocs(); toast('자료를 원본으로 저장했어요'); }catch(err){ toast(err.message); } e.target.value=''; };
$('#reportBtn').onclick=async()=>{
  const w=window.open('','_blank');  // 팝업 차단을 피하려고 누른 순간 창을 먼저 연다
  try{ const r=await api('POST',`/api/jobs/${state.current}/report`); if(w) w.location=r.url; else location.href=r.url;
    await loadDocs(); $('#reportTrace').innerHTML=traceHTML(r.trace); toast('상담 사전 자료를 만들었어요'); }
  catch(e){ if(w) w.close(); toast(e.message); } };

// 보호
async function loadGuard(){ $('#guardTrace').innerHTML=''; renderGuard(await api('GET',`/api/jobs/${state.current}/guard`)); }
function renderGuard(g){
  $('#reported').checked=g.reported; $('#guardOn').classList.toggle('hidden',!g.reported); if(!pendingSaves.has(`msg-${state.current}`)) $('#warnMsg').value=g.message;
  $('#msgReset').classList.toggle('hidden',!g.custom_message);
  state.keywords=g.keywords;
  $('#kwList').innerHTML=g.keywords.map((k,i)=>`<span class="chip kw">${esc(k)}<button type="button" data-i="${i}" aria-label="${esc(k)} 지우기">×</button></span>`).join('');
  $$('#kwList [data-i]').forEach(b=>b.onclick=()=>saveKeywords(state.keywords.filter((_,i)=>i!==Number(b.dataset.i))));
  $('#kwQueries').textContent=`검색할 말: ${g.queries.join(' / ')}`;
  const st={pending:['warn','판별 대기'],suspect:['bad','보복 의심'],ok:['ok','문제 없음']};
  $('#postList').innerHTML=g.posts.length?g.posts.map(p=>`<li class="post"><div class="main"><strong>${esc(p.title||'제목 없음')}</strong>
    <div class="sub"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.url)}</a></div>
    <div class="sub">${fmtDT(p.found_at)} 확인${p.evidence_id?`, <a class="ev-link" href="/api/evidence/${p.evidence_id}/file" target="_blank">보존한 화면</a>`:', 화면 캡처 없음'}</div></div>
    <span class="tag ${st[p.status][0]}">${st[p.status][1]}</span></li>`).join(''):'<li><span class="sub">아직 확인한 게시물이 없어요</span></li>';
}
$('#reported').onchange=async e=>{ try{ const g=await api('POST',`/api/jobs/${state.current}/guard`,{reported:e.target.checked}); renderGuard(g); $('#guardTrace').innerHTML=traceHTML(g.trace); }catch(err){ toast(err.message); } };
async function saveKeywords(list){ try{ renderGuard(await api('PUT',`/api/jobs/${state.current}/guard/keywords`,{keywords:list})); }catch(e){ toast(e.message); } }
$('#kwAdd').onclick=()=>{ const v=$('#kwInput').value.trim(); if(!v){ toast('검색어를 넣어 주세요'); return; }
  $('#kwInput').value=''; saveKeywords([...(state.keywords||[]), ...v.split(',').map(x=>x.trim()).filter(Boolean)]); };
$('#kwInput').addEventListener('keydown',e=>{ if(e.key==='Enter'){ e.preventDefault(); $('#kwAdd').click(); } });
$('#warnMsg').addEventListener('input',()=>{
  const jobId=state.current, msg=$('#warnMsg').value;
  autosave(`msg-${jobId}`,()=>api('PUT',`/api/jobs/${jobId}/guard/message`,{message:msg}).then(g=>{ if(jobId===state.current) $('#msgReset').classList.toggle('hidden',!g.custom_message); }),'#msgSaved');
});
$('#msgReset').onclick=async()=>{ if(!confirm('고친 문구를 지우고 기본 문구로 되돌릴까요?')) return;
  try{ renderGuard(await api('PUT',`/api/jobs/${state.current}/guard/message`,{message:''})); $('#msgSaved').textContent='기본 문구로 되돌렸어요'; }catch(e){ toast(e.message); } };
$('#copyMsg').onclick=()=>{ const t=$('#warnMsg').value; (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>toast('안내 문구를 복사했어요'),()=>toast('직접 선택해 복사해 주세요')); };
$('#postAdd').onclick=async()=>{ const url=$('#postUrl').value.trim(); if(!url){ toast('게시물 주소를 넣어 주세요'); return; }
  try{ const r=await api('POST',`/api/jobs/${state.current}/guard/posts`,{url}); $('#postUrl').value=''; await loadGuard(); $('#guardTrace').innerHTML=traceHTML(r.trace); toast('주소와 확인 시각을 보존했어요'); }catch(e){ toast(e.message); } };
$('#postSearch').onclick=async()=>{ try{ const r=await api('POST',`/api/jobs/${state.current}/guard/search`); await loadGuard(); $('#guardTrace').innerHTML=traceHTML(r.trace); toast(r.skipped?r.reason:`새 게시물 ${r.added}건을 찾았어요`); }catch(e){ toast(e.message); } };

boot();
