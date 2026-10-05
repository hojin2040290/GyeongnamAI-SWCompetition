// 알바지킴이 화면 동작. 모든 기록과 판단은 서버(API)에서 처리하고, 화면은 보여주기와 입력만 맡는다.
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const DAY_KEYS = ['월','화','수','목','금','토','일'];
const BREAKS = ['없음','15분','30분','45분','1시간','1시간 30분','2시간','모름'];  // 빠른 선택지. 그 밖의 값은 '직접 입력'
const BRK_CUSTOM = '직접 입력';
const LABEL = {ok:'정상', warn:'확인 필요', bad:'위반 의심', pending:'AI 에이전트 판단 대기'};  // pending: AI 판단 전 (AI를 기다림)
const JUDGING = 'AI 에이전트가 판단 중';  // 에이전트가 지금 판단하고 있을 때
const EVENT = {contract_check:'계약서 점검', shift_check:'퇴근 점검', seek_check:'지원 전 확인', payday:'급여 점검', quit_check:'퇴직 정산',
  report:'상담 자료', guard_on:'신고 후 보호 시작', guard_off:'신고 후 보호 끔', guard_search:'게시물 검색', guard_preserve:'게시물 보존',
  guard_review:'게시물 판별', daily:'매일 자동 점검', advice:'매일 종합 조언', overview:'종합 점검'};
const KIND = {contract:'근로계약서', payslip:'급여명세서', message:'사업주 메시지', schedule:'근무표', deposit:'입금 내역', post:'게시물 화면', notice:'채용공고', other:'기타'};

const state = { me:null, jobs:[], current:null, mode:null, cards:[], adding:false, seekFromApp:false,
  inApp:false, editingJob:null, curOb:null, tabHist:[] };

function pad(n){ return String(n).padStart(2,'0'); }
// 링크는 http, https 주소만 연다 (javascript: 같은 주소가 스크립트로 실행되지 않게)
function safeUrl(u){ return /^https?:\/\//i.test(String(u||''))?u:'#'; }
function esc(t){ return String(t ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function toMin(t){ const [h,m]=t.split(':').map(Number); return h*60+m; }
// 쉬는 시간 글('19분', '3시간', '1시간 15분')을 분으로 (서버의 schedule.parse_break와 같은 규칙). 모르면 null
function parseBrk(b){ b=String(b??'').trim(); if(b==='없음') return 0;
  const m=b.match(/^(?:(\d{1,2})\s*시간)?\s*(?:(\d{1,4})\s*분)?$/); if(!b||!m||!(m[1]||m[2])) return null;
  return Number(m[1]||0)*60+Number(m[2]||0); }
function brkText(min){ const h=Math.floor(min/60), m=min%60; return (!h&&!m)?'없음':[h?`${h}시간`:'', m?`${m}분`:''].filter(Boolean).join(' '); }
function brkMin(b){ return parseBrk(b) ?? 0; }
function slotMinutes(s){ let a=toMin(s.start), b=toMin(s.end); if(b<=a) b+=1440; return Math.max(0,b-a-brkMin(s.brk)); }
function slotsOf(v){ return Array.isArray(v)?v:(v?[v]:[]); }  // 한 요일의 시간대 목록 (예전 형식은 하나)
function dayMinutes(v){ return slotsOf(v).reduce((a,s)=>a+slotMinutes(s),0); }
function isNight(s){ let a=toMin(s.start), b=toMin(s.end); if(b<=a) b+=1440; for(let t=a;t<b;t+=30){ const x=t%1440; if(x>=1320||x<360) return true; } return false; }
function fmtH(min){ const h=Math.floor(min/60), m=min%60; return m?`${h}시간 ${m}분`:`${h}시간`; }
// 날짜와 시각 표시. 서버가 준 한국 시각 글자를 그대로 읽는다 (브라우저 시간대로 바꾸지 않음).
// 올해가 아니면 연도를 붙이고, 증거(출퇴근, 올린 자료, 보존한 게시물)는 초까지 적는다
const WD='일월화수목금토';
function parseDT(s){ const m=String(s||'').match(/(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/); if(!m) return null;
  return {y:+m[1], mo:+m[2], d:+m[3], h:m[4]??'00', mi:m[5]??'00', s:m[6]??'00'}; }
function fmtDay(s,{wd=true}={}){ const t=parseDT(s); if(!t) return '';
  const yr=t.y!==new Date().getFullYear()?`${t.y}년 `:''; const w=WD[new Date(Date.UTC(t.y,t.mo-1,t.d)).getUTCDay()];
  return `${yr}${t.mo}월 ${t.d}일${wd?` (${w})`:''}`; }
function fmtTime(s,{sec=false}={}){ const t=parseDT(s); return t?`${t.h}:${t.mi}${sec?':'+t.s:''}`:''; }
function fmtDT(s,{sec=false,wd=false}={}){ return parseDT(s)?`${fmtDay(s,{wd})} ${fmtTime(s,{sec})}`:''; }
function fmtMonth(m){ const x=String(m||'').match(/^(\d{4})-(\d{2})$/); return x?`${x[1]}년 ${+x[2]}월`:String(m||''); }
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
// 'AI 응답 대기 중' 글자에는 어디서든 도는 표시를 붙인다 (알림, 토스트 포함)
function waitMark(text){ return esc(text).replace(/(AI )?응답 대기 중|AI (에이전트 )?판단 대기( \d+건?)?/g,m=>`<span class="wait-note">${m}</span>`); }
function toast(msg){ const el=$('#toast'); el.innerHTML=waitMark(msg); el.classList.add('show'); clearTimeout(tt); tt=setTimeout(()=>el.classList.remove('show'),2600); }

// 서버가 이유를 적지 않은 오류 (연결 중간의 프록시, 터널이 돌려준 오류 등)
function failText(status){
  if([502,503,504,522,523,524].includes(status)) return '서버 응답이 너무 오래 걸려 연결이 끊겼어요. 작업은 계속될 수 있으니 잠시 뒤 새로고침해 보세요';
  if(status===413) return '파일이 너무 커요';
  if(status>=500) return '서버에서 오류가 났어요. 잠시 뒤 다시 해 주세요';
  return `요청을 처리하지 못했어요 (${status})`;
}

// agent() 진행 칸이 떠 있는 동안(값이 0보다 큼) 보내는 저장·점검 요청은 서버가 뒤에서 실행한다 (app/long_task.py).
// AI가 여러 번 불려 오래 걸려도 cloudflared의 100초 제한에 끊기지 않게, 작업 번호를 받고 결과를 따로 묻는다.
let longDepth=0;
const NET_ERR='서버에 연결하지 못했어요. 인터넷 연결이나 서버가 켜져 있는지 확인해 주세요';
async function waitTask(id){
  let fails=0;
  for(let i=0; i<3600; i++){  // 1초마다, 최대 1시간
    await new Promise(r=>setTimeout(r,1000));
    let r;
    try{ r=await fetch(`/api/tasks/${id}`); fails=0; }
    catch(e){ if(++fails>=30){ const err=new Error(NET_ERR); err.status=0; throw err; } continue; }  // 잠깐 끊겨도 다시 묻는다
    if(r.status!==202 || !r.headers.get('x-task-pending')) return r;
  }
  const err=new Error('작업이 너무 오래 걸려요. 잠시 뒤 화면을 다시 열어 결과를 확인해 주세요'); err.status=0; throw err;
}
async function api(method, url, body, isForm){
  const opt = { method, headers:{} };
  if (body !== undefined) {
    if (isForm) opt.body = body; else { opt.headers['Content-Type']='application/json'; opt.body=JSON.stringify(body); }
  }
  const long = longDepth>0 && method!=='GET';
  if (long) opt.headers['X-Long-Task']='1';
  let r;
  try { r = await fetch(url, opt); }
  catch(e){ const err=new Error(NET_ERR); err.status=0; throw err; }
  if (long && r.status===202) { const j=await r.json(); if (j.task_id) r=await waitTask(j.task_id); }
  if (!r.ok) {
    let msg = failText(r.status);
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : msg; } catch(e) {}
    const err = new Error(msg); err.status = r.status; throw err;
  }
  const out = await r.json();
  if (method!=='GET' && !url.startsWith('/api/guide/')) guideSoon();  // 체험 안내: 무언가 하면 미션 진행을 다시 본다
  return out;
}

function show(id){
  if(id==='ob2') setTimeout(guideJobFillSync,0);  // 체험 안내: 등록 화면 위에 예시 계약서로 채우기 (안내가 없으면 아무것도 안 함)
  ['ob0','ob1','obLogin','obSeek','ob2','obMe'].forEach(x=>$('#'+x).classList.toggle('hidden', x!==id));
  state.curOb=id; if(id==='ob0') startScreen(); if(id==='ob1') prepBasic(); window.scrollTo(0,0); refreshNav();
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
        return state.seekFromApp ? ()=>{ state.seekFromApp=false; closeOverlay(); } : ()=>show('ob1');
      // 처음 설정은 한 단계씩 되돌아간다 (2단계 → 1단계 기본 정보 → 처음 화면)
      case 'ob2': return state.inApp ? cancelJobs : ()=>{ if(!confirmLeave()) return; clearCards(); show('ob1'); };
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
        if(seg.dataset.multi){  // 여러 개 고르기 (예: 공제 항목). '없음', '모름'은 혼자만 고른다
          const solo=v=>['없음','모름'].includes(v), on=btn.getAttribute('aria-pressed')!=='true';
          seg.querySelectorAll('button').forEach(x=>{ if(on && (solo(btn.dataset.v)||solo(x.dataset.v))) x.setAttribute('aria-pressed','false'); });
          btn.setAttribute('aria-pressed', on?'true':'false');
        } else {
          seg.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed','false'));
          btn.setAttribute('aria-pressed','true');
        }
        onChange && onChange(seg.dataset.name, btn.dataset.v);
      });
    });
  });
}
// 저장된 값에 따옴표 등이 섞여 있어도 선택자가 깨지지 않게 CSS.escape로 감싼다
const cssq=v=>(window.CSS&&CSS.escape)?CSS.escape(String(v)):String(v).replace(/["\\]/g,'\\$&');
function segVal(root, name){ const seg=root.querySelector(`.seg[data-name="${name}"]`); if(!seg) return null;
  const on=[...seg.querySelectorAll('[aria-pressed="true"]')].map(b=>b.dataset.v);
  return on.length?(seg.dataset.multi?on.join(', '):on[0]):null; }
function setSeg(root, name, v){ const seg=root.querySelector(`.seg[data-name="${name}"]`); if(!seg||v==null) return;
  if(seg.dataset.multi){ seg.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed','false'));
    String(v).split(',').map(x=>x.trim()).forEach(x=>seg.querySelector(`[data-v="${cssq(x)}"]`)?.click()); return; }
  seg.querySelector(`[data-v="${cssq(v)}"]`)?.click(); }

// ---------- 시작 ----------
// ---------- 입력 칸 규칙: 칸마다 쓸 수 있는 글자만 남긴다 (서버와 같은 규칙) ----------
async function loadRules(){ try{ state.rules=await api('GET','/api/input-rules'); }catch(e){ state.rules={contract:{},job:null}; } }
// 한글을 조합하는 동안에는 거르지 않고, 조합이 끝나면 거른다 (입력 중인 자모가 사라지지 않게)
function bindChars(input, chars, onBad){
  if(!input||!chars) return ()=>{};
  const re=new RegExp(`[^${chars}]`,'g');
  const run=()=>{ const v=input.value, nv=v.replace(re,''); if(nv===v) return;
    const pos=Math.max(0,(input.selectionStart??nv.length)-(v.length-nv.length)); input.value=nv;
    try{ input.setSelectionRange(pos,pos); }catch(e){} onBad&&onBad(); };
  input.addEventListener('input',e=>{ if(!e.isComposing) run(); });
  input.addEventListener('compositionend',run);
  return run;
}
// 사업장 이름, 사업주, 주소, 하는 일 칸
function bindJobChars(root){ const j=state.rules?.job; if(!j) return;
  [['.f-name','name'],['.f-owner','owner'],['.f-addr','address'],['.f-work','work_desc']].forEach(([sel,f])=>{
    const el=root.querySelector(sel); bindChars(el, j.chars, ()=>toast(`${j.fields[f]}에는 ${j.allowed}만 쓸 수 있어요`)); }); }
// 금액 칸(시급, 받은 금액): '55만원', '1만 2천원', '12,000원'처럼 적어도 된다. 칸을 벗어나면 숫자로 바꾼다
function parseWon(text){
  const t=String(text??'').replace(/[\s,원]/g,''); if(!t) return null; if(/^\d+$/.test(t)) return Number(t);
  const unit={'만':10000,'천':1000,'백':100}; let total=0, used='', m; const re=/(\d+)(만|천|백)|(\d+)$/g;
  while((m=re.exec(t))){ used+=m[0]; total+=m[1]?Number(m[1])*unit[m[2]]:Number(m[3]); }
  return used===t?total:null;
}
document.addEventListener('input',e=>{ const el=e.target; if(!el.matches?.('input[data-num]')) return;
  const v=el.value.replace(/[^0-9,만천백원 ]/g,''); if(v!==el.value){ el.value=v; toast(`${el.dataset.num}은(는) 숫자로 적어 주세요 (예: 12,000원, 55만원)`); } });
document.addEventListener('change',e=>{ const el=e.target; if(!el.matches?.('input[data-num]')||el.value==='') return;
  const n=parseWon(el.value), min=Number(el.dataset.min), max=Number(el.dataset.max);
  if(n==null){ toast(`${el.dataset.num}을(를) 알아볼 수 없어요. 숫자로 적어 주세요 (예: 12,000원, 55만원)`); el.value=''; return; }
  if(n<min||n>max){ toast(`${el.dataset.num}은(는) ${min.toLocaleString()}원부터 ${max.toLocaleString()}원까지 적을 수 있어요`); el.value=''; return; }
  if(String(n)!==el.value){ toast(`${el.value} → ${n.toLocaleString()}원으로 적었어요`); el.value=String(n); } });
async function boot(){
  await loadRules(); bindJobChars(seek.node);
  try {
    state.me = await api('GET','/api/me'); guideRefresh();
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
// 로그인한 상태여도 1단계(기본 정보)를 거친다: 가입한 이메일과 생년월일을 보여 주고 확인받는다
$('#modeNext').onclick = () => show('ob1');
function prepBasic(){
  const signed=!!state.me; $('#regSigned').classList.toggle('hidden',!signed); $('#regPwWrap').classList.toggle('hidden',signed);
  $('#regErr').textContent='';
  if(signed){ $('#regEmail').value=state.me.email; $('#birth').value=state.me.birth_date; $('#regPw').value=''; }
  if(signed && emailProblem(state.me.email)) $('#regErr').textContent=`${emailProblem(state.me.email)}. 고친 뒤 다음을 눌러 주세요`;
}
$('#obLogout').onclick = async () => { await api('POST','/api/auth/logout'); location.reload(); };
$('#toLogin').onclick = () => show('obLogin');
$('#toStart').onclick = () => show('ob0');

// 이메일 형식 (서버의 input_rules.EMAIL_PATTERN과 같은 규칙)
const EMAIL_RE=/^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$/;
function emailProblem(v){
  v=String(v||'').trim(); if(!v) return '이메일을 입력해 주세요';
  if(!v.includes('@')) return '이메일에 @가 없어요 (예: alba@example.com)';
  if(v.length>254||!EMAIL_RE.test(v)) return '이메일 형식이 맞지 않아요 (예: alba@example.com)';
  return '';
}
$('#regEmail').addEventListener('blur',()=>{ const v=$('#regEmail').value; $('#regErr').textContent=v.trim()?emailProblem(v):''; });
$('#regEmail').addEventListener('input',()=>{ if($('#regErr').textContent && !emailProblem($('#regEmail').value)) $('#regErr').textContent=''; });
$('#regBtn').onclick = async () => {
  $('#regErr').textContent='';
  const email=$('#regEmail').value.trim(), password=$('#regPw').value, birth_date=$('#birth').value;
  if(!email||(!state.me&&!password)||!birth_date){ $('#regErr').textContent=state.me?'이메일과 생년월일을 입력해 주세요':'이메일, 비밀번호, 생년월일을 모두 입력해 주세요'; return; }
  const bad=emailProblem(email); if(bad){ $('#regErr').textContent=bad; $('#regEmail').focus(); return; }
  try {
    state.me = state.me ? await api('PUT','/api/me',{email,birth_date,mode:state.mode||'work'})
      : await api('POST','/api/auth/register',{email,password,birth_date,mode:state.mode||'work'});
    afterMode(); guideRefresh();  // 새로 가입했으면 체험 안내가 시작된다 (상황을 다시 골랐으면 미션도 바뀐다)
  } catch(e) { $('#regErr').textContent=e.message; }
};
$('#logBtn').onclick = async () => {
  $('#logErr').textContent='';
  if(!$('#logEmail').value.trim()||!$('#logPw').value){ $('#logErr').textContent='이메일과 비밀번호를 입력해 주세요'; return; }
  const bad=emailProblem($('#logEmail').value); if(bad){ $('#logErr').textContent=bad; $('#logEmail').focus(); return; }
  try {
    state.me = await api('POST','/api/auth/login',{email:$('#logEmail').value.trim(),password:$('#logPw').value});
    await loadJobs(); guideRefresh();
    if (!state.jobs.length) show('ob0'); else { closeOverlay(); startApp(); }
  } catch(e) { $('#logErr').textContent=e.message; }
};

// ---------- 사업장 입력 카드 ----------
function addCard(){
  const node=$('#jobTpl').content.firstElementChild.cloneNode(true);
  const card={node, schedule:{}}; state.cards.push(card); bindJobChars(node);
  // 월급날: 1~30일과 말일(매달 마지막 날, 31로 저장). 그 달에 없는 날이면 그 달 마지막 날로 본다
  node.querySelector('.f-payday').insertAdjacentHTML('beforeend',
    Array.from({length:30},(_,i)=>`<option value="${i+1}">${i+1}일</option>`).join('')+'<option value="31">말일 (매달 마지막 날)</option>');
  const name=node.querySelector('.f-name');
  name.addEventListener('input',()=>{ node.querySelector('.job-title').textContent=name.value.trim()||'새 일하는 곳'; });
  node.querySelector('.remove').onclick=async()=>{
    if(state.editingJob){ await deleteJob(state.editingJob); return; }
    if(state.cards.length===1){ toast('하나 이상 있어야 해요'); return; }
    node.remove(); state.cards=state.cards.filter(c=>c!==card); refreshRemove();
  };
  node.querySelector('.schedule-btn').onclick=()=>openSheetFor(card);
  node.querySelector('.f-noend').onchange=e=>{ node.querySelector('.f-end').disabled=e.target.checked; };
  const startEl=node.querySelector('.f-start');
  startEl.max=todayStr();  // 달력에서 미래 날짜를 고를 수 없게
  const syncMin=()=>{ node.querySelector('.f-end').min=startEl.value; node.querySelector('.f-quit').min=startEl.value; };
  startEl.addEventListener('change',()=>{ checkMinor(card); syncMin(); });
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
  q('.f-bizno').value=j.biz_no||'';
  q('.f-type').value=j.industry||''; q('.f-work').value=j.work_desc||''; q('.f-wage').value=j.wage??'';
  q('.f-start').value=j.start_date||''; q('.f-end').value=j.end_date||'';
  q('.f-end').min=j.start_date||''; q('.f-quit').min=j.start_date||'';
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
// 사업자등록번호: 숫자만 받아 000-00-00000으로 보여 주고, 마지막 검증 번호가 맞는지 바로 알려 준다
const BIZ_W=[1,3,7,1,3,7,1,3,5];
function bizValid(n){ if(n.length!==10) return false; const d=[...n].map(Number);
  const t=BIZ_W.reduce((a,w,i)=>a+d[i]*w,0)+Math.floor(d[8]*5/10); return (10-t%10)%10===d[9]; }
function bizFormat(v){ const n=v.replace(/\D/g,'').slice(0,10); return n.length>5?`${n.slice(0,3)}-${n.slice(3,5)}-${n.slice(5)}`:(n.length>3?`${n.slice(0,3)}-${n.slice(3)}`:n); }
const BIZ_HINT='계약서, 급여명세서, 가게 영수증에 적혀 있어요. 모르면 비워 두세요.';
document.addEventListener('input',e=>{
  if(!e.target.matches('.f-bizno')) return;
  const el=e.target; el.value=bizFormat(el.value);
  const n=el.value.replace(/\D/g,''), hint=el.parentElement.querySelector('.bizno-hint');
  const bad=n.length===10&&!bizValid(n);
  hint.textContent=bad?'번호가 맞지 않아요. 계약서나 영수증에서 다시 확인해 주세요.':BIZ_HINT; hint.classList.toggle('bad',bad);
});
function yn(v){ return v==='yes'?true:(v==='no'?false:null); }
function collect(card){
  const n=card.node, q=s=>n.querySelector(s);
  const wage=parseWon(q('.f-wage').value), payday=q('.f-payday').value;
  return {
    name:q('.f-name').value.trim(), status:segVal(n,'status')||'working', quit_date:q('.f-quit').value||null,
    industry:q('.f-type').value, work_desc:q('.f-work').value.trim(), wage:wage||null,
    start_date:q('.f-start').value||null, end_date:q('.f-noend').checked?null:(q('.f-end').value||null), no_end:q('.f-noend').checked,
    contract_written:yn(segVal(n,'contract')), copy_received:yn(segVal(n,'copy')),
    probation:segVal(n,'probation')||'unknown', probation_months:q('.f-probmonths').value?Number(q('.f-probmonths').value):null,
    schedule:card.schedule, size:segVal(n,'size')||'unknown', pay_cycle:segVal(n,'paytype')||'',
    payday:payday?Number(payday):null, pay_method:segVal(n,'paymethod')||'', deduction:segVal(n,'deduct')||'',
    consent:segVal(n,'consent')||'', address:q('.f-addr').value.trim(), owner:q('.f-owner').value.trim(),
    biz_no:q('.f-bizno').value.trim(),
  };
}
$('#addJobBtn').onclick=()=>{ const c=addCard(); c.node.scrollIntoView({block:'start'}); };
$('#saveJobsBtn').onclick=async()=>{
  $('#jobErr').textContent='';
  for(const c of state.cards){
    const d=collect(c);
    if(!d.name){ c.node.scrollIntoView({block:'center'}); c.node.querySelector('.f-name').focus(); $('#jobErr').textContent='사업장 이름을 입력해 주세요'; return; }
    if(d.status==='quit'&&!d.quit_date){ c.node.scrollIntoView({block:'center'}); $('#jobErr').textContent='그만둔 날을 입력해 주세요'; return; }
    const dp=dateProblem(d); if(dp){ c.node.scrollIntoView({block:'center'}); $('#jobErr').textContent=dp; return; }
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
// 한 요일에 시간대가 여러 개일 수 있다 (예: 10:00~14:00, 18:00~22:00 쪼개기 근무).
// 편집 중에는 요일마다 시간대 목록으로 다루고, 저장할 때 하나면 예전처럼 시간대 하나로 저장한다.
let editing=null, draft=null;
function openSheetFor(target){
  editing=target; draft={};
  DAY_KEYS.forEach(k=>{ const v=slotsOf(target.schedule?.[k]); if(v.length) draft[k]=JSON.parse(JSON.stringify(v)); });
  const nm=target.node.querySelector('.f-name')?.value.trim();
  $('#sheetJobName').textContent=nm?`${nm}에서 일하는 시간을 골라 주세요`:'일하는 시간을 골라 주세요';
  renderDays(); renderSlots(); $('#sheetBg').classList.remove('hidden'); document.body.style.overflow='hidden'; refreshNav();
}
function closeSheet(){ $('#sheetBg').classList.add('hidden'); document.body.style.overflow=''; editing=null; refreshNav(); }
function renderDays(){
  const box=$('#dayPicker'); box.innerHTML='';
  DAY_KEYS.forEach(k=>{ const b=document.createElement('button'); b.type='button'; b.className='day'; b.textContent=k;
    b.setAttribute('aria-pressed',draft[k]?'true':'false'); b.setAttribute('aria-label',`${k}요일`);
    b.onclick=()=>{ if(draft[k]) delete draft[k]; else draft[k]=newDay(); renderDays(); renderSlots(); };
    box.appendChild(b); });
}
// 새로 고른 요일은 이미 고른 요일의 시간대를 그대로 가져온다 (처음이면 18:00~22:00)
function newDay(){ const k=DAY_KEYS.find(k=>draft[k]); return k?JSON.parse(JSON.stringify(draft[k])):[{start:'18:00',end:'22:00',brk:'없음'}]; }
// 같은 요일에 시간대를 더하면 앞 시간대가 끝난 1시간 뒤부터 4시간으로 시작한다
function nextSlot(last){ const h=m=>`${pad(Math.floor(m/60)%24)}:${pad(m%60)}`, e=toMin(last.end)+60; return {start:h(e),end:h(e+240),brk:'없음'}; }
// 평일, 주말, 매일, 모두 해제: 그 요일들만 고른 상태로 만든다
$$('#dayQuick [data-days]').forEach(b=>b.onclick=()=>{ const want=[...b.dataset.days];
  const tpl=newDay(); DAY_KEYS.forEach(k=>{ if(!want.includes(k)) delete draft[k]; else if(!draft[k]) draft[k]=JSON.parse(JSON.stringify(tpl)); });
  renderDays(); renderSlots(); });
function opts(list,sel){ return list.map(v=>`<option${v===sel?' selected':''}>${v}</option>`).join(''); }
function renderSlots(){
  const box=$('#slotList'); box.innerHTML='';
  const keys=DAY_KEYS.filter(k=>draft[k]);
  $('#slotEmpty').classList.toggle('hidden',keys.length>0); $('#copyAll').classList.toggle('hidden',keys.length<2);
  keys.forEach(k=>{ const list=draft[k]; const el=document.createElement('div'); el.className='slot';
    el.innerHTML=`<div class="slot-day">${k}요일 <span class="hrs">${fmtH(dayMinutes(list))}</span></div>`+list.map((s,i)=>`
      <div class="slot-part" data-i="${i}">${list.length>1?`<div class="part-head"><span>시간대 ${i+1}</span><button type="button" class="link small muted" data-rm="${i}">이 시간대 빼기</button></div>`:''}
      <div class="slot-grid"><input type="time" class="input" step="1800" value="${esc(s.start)}" aria-label="${k}요일 시간대 ${i+1} 시작"><span class="tilde">부터</span>
      <input type="time" class="input" step="1800" value="${esc(s.end)}" aria-label="${k}요일 시간대 ${i+1} 끝"></div>
      <div class="brk">쉬는 시간<select class="input" aria-label="${k}요일 시간대 ${i+1} 쉬는 시간">${opts([...BREAKS,BRK_CUSTOM],BREAKS.includes(s.brk)?s.brk:BRK_CUSTOM)}</select></div>
      <div class="brk-custom${BREAKS.includes(s.brk)?' hidden':''}"><input type="number" class="input" inputmode="numeric" min="0" max="23" placeholder="0" value="${BREAKS.includes(s.brk)?'':Math.floor(brkMin(s.brk)/60)||''}" aria-label="${k}요일 시간대 ${i+1} 쉬는 시간 (시간)"><span>시간</span>
        <input type="number" class="input" inputmode="numeric" min="0" max="59" placeholder="0" value="${BREAKS.includes(s.brk)?'':brkMin(s.brk)%60||''}" aria-label="${k}요일 시간대 ${i+1} 쉬는 시간 (분)"><span>분</span></div></div>`).join('')+
      `<button type="button" class="link small add-part">+ ${k}요일에 시간대 더하기 (쪼개서 일할 때)</button>`;
    const up=()=>{ el.querySelector('.hrs').textContent=fmtH(dayMinutes(list)); totals(); };
    el.querySelectorAll('.slot-part').forEach(part=>{ const s=list[Number(part.dataset.i)];
      const [st,en]=part.querySelectorAll('.slot-grid input'), br=part.querySelector('.brk select');
      // 휴대폰 기본 시간 휠. 지우면 이전 값으로 되돌린다
      st.onchange=()=>{ if(st.value) s.start=st.value; else st.value=s.start; up(); };
      en.onchange=()=>{ if(en.value) s.end=en.value; else en.value=s.end; up(); };
      const cu=part.querySelector('.brk-custom'), [bh,bm]=cu.querySelectorAll('input');
      br.onchange=()=>{ const own=br.value===BRK_CUSTOM; cu.classList.toggle('hidden',!own);
        if(own){ bh.value=''; bm.value=''; bh.focus(); s.brk='없음'; } else s.brk=br.value; up(); };
      // 직접 입력: 시간과 분을 숫자로 받아 '1시간 15분' 모양으로 저장한다. 일하는 시간보다 길면 알려 준다
      const custom=()=>{ const h=Math.min(23,Math.max(0,parseInt(bh.value)||0)), m=Math.max(0,parseInt(bm.value)||0), total=h*60+m;
        const span=((toMin(s.end)-toMin(s.start))+1440)%1440||1440;
        if(total>=span){ toast(`쉬는 시간이 일하는 시간(${s.start}~${s.end})보다 길어요`); bh.value=''; bm.value=''; s.brk='없음'; }
        else s.brk=brkText(total);
        up(); };
      bh.onchange=custom; bm.onchange=custom; });
    el.querySelectorAll('[data-rm]').forEach(b=>b.onclick=()=>{ list.splice(Number(b.dataset.rm),1); renderSlots(); });
    el.querySelector('.add-part').onclick=()=>{ list.push(nextSlot(list[list.length-1])); renderSlots(); };
    box.appendChild(el); });
  totals();
}
function totals(){ const keys=DAY_KEYS.filter(k=>draft[k]);
  $('#weekTotal').textContent=fmtH(keys.reduce((a,k)=>a+dayMinutes(draft[k]),0));
  $('#nightFlag').classList.toggle('hidden',!keys.some(k=>slotsOf(draft[k]).some(isNight))); }
function schedSummary(schedule){ const keys=DAY_KEYS.filter(k=>slotsOf(schedule[k]).length); if(!keys.length) return '근무 요일 미등록';
  return `${keys.join(', ')}요일, 주 ${fmtH(keys.reduce((a,k)=>a+dayMinutes(schedule[k]),0))}`; }
$('#copyAll').onclick=()=>{ const keys=DAY_KEYS.filter(k=>draft[k]); const f=draft[keys[0]]; keys.forEach(k=>draft[k]=JSON.parse(JSON.stringify(f))); renderSlots(); };
$('#sheetClose').onclick=goBack;
$('#sheetBg').onclick=e=>{ if(e.target.id==='sheetBg') goBack(); };
$('#sheetSave').onclick=()=>{
  if(!DAY_KEYS.some(k=>draft[k])){ toast('일하는 요일을 하나 이상 골라 주세요'); return; }
  if(editing!==seek) state.formDirty=true;
  const out={}; DAY_KEYS.forEach(k=>{ if(draft[k]) out[k]=draft[k].length===1?draft[k][0]:draft[k]; });  // 하나면 예전 형식
  editing.schedule=out; editing.node.querySelector('.sched-sum').textContent=schedSummary(out);
  editing.node.querySelector('.sched-go').textContent='수정'; goBack(); toast('근무 시간을 저장했어요');
};

// ---------- 지원 전 확인 ----------
const seek={node:$('#seekForm'), schedule:{}};
bindSeg(seek.node);
seek.node.querySelector('.schedule-btn').onclick=()=>openSheetFor(seek);
$('#seekFile').onchange=async e=>{ const files=[...e.target.files]; if(!files.length) return; let ok=0;
  for(const f of files){ const fd=new FormData(); fd.append('file',f); fd.append('kind','notice');
    try{ await api('POST','/api/evidence',fd,true); ok++; }catch(err){ toast(`${f.name}: ${err.message}`); } }
  if(ok) toast(`공고 사진 ${ok}장을 원본으로 보관했어요`); e.target.value=''; };
function seekInput(){ const n=seek.node, w=parseWon(n.querySelector('.f-wage').value);
  return { name:n.querySelector('.f-name').value.trim(), industry:n.querySelector('.f-type').value, work_desc:n.querySelector('.f-work').value.trim(),
    wage:w||null, probation:segVal(n,'probation')||'unknown', schedule:seek.schedule,
    biz_no:n.querySelector('.f-bizno').value.trim() }; }
function articleHTML(a){
  if(!a || a.na) return '';
  if(!a.built) return `<p class="basis"><span class="chip muted">법 기준표 미구축</span> 법제처 API로 조문을 불러오면 원문이 붙어요.</p>`;
  return `<details class="law-text"><summary>조문 원문 보기${a.title?` (${esc(a.title)})`:''}</summary><pre>${esc(a.text)}</pre></details>`;
}
// 법제처에서 받아 둔 판례, 해석례, 결정문 (참고용)
function refsHTML(refs){
  if(!refs?.length) return '';
  return `<details class="law-text"><summary>참고 판례·해석 ${refs.length}건</summary>${refs.map(r=>`<div class="ref">
    <div class="ref-head"><span class="chip">${esc(r.kind)}</span> <b>${esc(r.title)}</b></div>
    <div class="sub">${esc(r.number)}${r.date?`, ${esc(r.date)}`:''}</div>${r.summary?`<p class="ref-sum">${esc(r.summary)}</p>`:''}</div>`).join('')}
    <p class="sub">비슷한 사례라도 사실관계에 따라 결과가 다를 수 있어요.</p></details>`;
}
// 항목마다 AI 에이전트의 판단 칸 (결과, 이유, 근거 조항과 사실). 코드가 정리한 사실과 구분해 보여 준다
function aiJudgeHTML(it){
  if(it.ai_reason) return `<div class="ai-judge"><div class="ai-judge-head"><b>AI 에이전트 판단</b><span class="tag ${it.status}">${LABEL[it.status]}</span></div>
    <p>${esc(it.ai_reason)}</p><p class="sub">근거 조항: ${esc(it.ai_law||'-')} · 근거 사실: ${esc(it.ai_fact||'-')}</p></div>`;
  if(it.status==='pending') return `<div class="ai-judge"><div class="ai-judge-head"><b>AI 에이전트 판단</b></div>
    <p><span class="wait-note">AI 응답 대기 중</span>${it.ai_error?` (${esc(it.ai_error)})`:' AI가 연결되면 판단해요.'}</p></div>`;
  return `<div class="ai-judge none"><div class="ai-judge-head"><b>AI 에이전트 판단</b></div><p class="sub">AI 판단 없이 코드가 정리한 사실로 분류했어요.</p></div>`;
}
function itemHTML(it){
  const extra=[]; if(it.basis?.length) extra.push(`근거로 쓴 사실: ${esc(it.basis.join(', '))}`);
  if(it.needed?.length) extra.push(`필요한 정보: ${esc(it.needed.join(', '))}`);
  const src=it.source==='records'?'<span class="chip">근무 기록</span>':'';
  return `<div class="result ${it.status}"><div class="head"><span class="law">${esc(it.law)} ${src}</span><span class="tag ${it.status}">${LABEL[it.status]}</span></div>
    <p>${esc(it.text)}</p>${extra.map(x=>`<p class="basis">${x}</p>`).join('')}${aiJudgeHTML(it)}${articleHTML(it.article)}${refsHTML(it.refs)}</div>`;
}
// 에이전트가 판단하는 동안: 예전 결과 대신 'AI 에이전트가 판단 중'을 보여 준다 (끝나면 새 판단으로 바뀐다)
function judging(sel){
  if(sel==='#checkItems'){ const n=$$('#checkItems .result').length; if(n) $('#checkSummary').innerHTML=`<div class="pending"><strong>${n}</strong><span class="wait-note">AI 판단 중</span></div>`; }
  $$(`${sel} .result`).forEach(r=>{ r.className='result pending';
    const t=r.querySelector('.head .tag'); if(t){ t.className='tag pending'; t.textContent=JUDGING; }
    const a=r.querySelector('.ai-judge'); if(a) a.outerHTML=`<div class="ai-judge"><div class="ai-judge-head"><b>AI 에이전트 판단</b></div><p><span class="wait-note">${JUDGING}</span></p></div>`; });
}
// 에이전트가 거친 단계 (입력, 판단, 도구 실행, 결과)
// 에이전트가 일하는 동안: 새로 남는 동작 기록을 불러와 단계별로 보여 준다
// 요소의 윗부분(제목과 첫 단계)이 위 머리줄과 아래 메뉴에 가리지 않게 스크롤한다 (처음 설정 화면은 그 안에서 스크롤)
function reveal(node){
  if(!node) return;
  requestAnimationFrame(()=>{
    const over=visible('#onboard'), r=node.getBoundingClientRect(), nav=$('#tabs');
    const bottom=(!over && nav && !nav.classList.contains('hidden'))?nav.getBoundingClientRect().top:innerHeight;
    const top=over?0:($('header.top')?.getBoundingClientRect().bottom||0);
    const want=r.top+Math.min(r.height,140);
    let dy=0;
    if(want>bottom-12) dy=want-(bottom-12); else if(r.top<top+12) dy=r.top-(top+12);
    if(dy) (over?$('#onboard'):window).scrollBy(0,dy);
  });
}
// 저장은 바로 끝내고, 시간이 걸리는 AI 점검은 이어서 따로 한다. 점검이 실패해도 저장한 값은 남아 있다고 알린다
async function checkAfterSave(box, run){
  try{ return await agent(box, run); }
  catch(e){ toast(`저장은 됐어요. 점검은 끝내지 못했어요: ${e.message}`); return null; }
}
// 화면이 알아서 연 점검(홈을 열 때 종합 점검, AI가 생긴 뒤 다시 점검)은 화면을 옮기지 않는다
let autoDepth=0;
function autoRun(fn){ autoDepth++; try{ return fn(); }finally{ autoDepth--; } }
// 사용자가 직접 화면을 움직이면(손가락, 휠, 키) 그 실행이 끝날 때까지 진행 칸을 따라가지 않는다
const HAND=['wheel','touchstart','keydown','mousedown'];
async function agent(box, run){
  const el=$(box); let last=0, stop=false, follow=!autoDepth;
  const hands=e=>{ if(e.type!=='keydown' || /Arrow|Page|Home|End|^ $/.test(e.key)) follow=false; };
  if(follow) HAND.forEach(t=>addEventListener(t,hands,{passive:true}));
  el.innerHTML='<div class="live"><p class="wait-note live-head">에이전트가 일하는 중이에요</p><div class="live-steps"></div></div>';
  const mine=el.querySelector('.live'), ours=()=>el.contains(mine);  // 다른 일하는 곳으로 바꿔 칸이 비워졌으면 이 실행의 칸이 아니다
  mine.dataset.job=state.current;
  if(follow) reveal(mine);  // 누른 버튼 아래 진행 칸이 가려져 있으면 보이는 곳까지 옮긴다 (끝날 때까지 이 화면에 머문다)
  try{ last=(await api('GET','/api/agent/last')).id; }catch(e){}
  (async()=>{ while(!stop){
    try{ queueHead(mine, await api('GET','/api/agent/queue')); }catch(e){}
    try{ const rows=await api('GET',`/api/agent/live?after=${last}`);
      const steps=ours()?mine.querySelector('.live-steps'):null;
      if(rows.length && !stop && steps){ last=rows[rows.length-1].id;
        steps.insertAdjacentHTML('beforeend',rows.map(stepHTML).join(''));
        steps.scrollTop=steps.scrollHeight;  // 칸 높이는 그대로, 칸 안에서 최근 단계가 보이게
        steps.classList.toggle('cut',steps.scrollTop>0);  // 위로 밀린 단계가 있으면 윗줄을 흐리게 (잘린 글자가 보이지 않게)
        if(follow) reveal(mine); } }catch(e){}
    await new Promise(r=>setTimeout(r,700)); } })();
  longDepth++;
  try{ return await run(); }catch(e){ if(ours()) el.innerHTML=''; throw e; }finally{ stop=true; longDepth--; HAND.forEach(t=>removeEventListener(t,hands)); }
}
// 대기줄(서버 전체에 하나, 1번이 실행 중): 내 실행이 1번이 아니면 진행 칸 머리에 순서를 보여 준다
function queueHead(box, q){
  const head=box.querySelector('.live-head'); if(!head) return;
  const pos=Math.min(...(q.mine||[]).map(x=>x.pos));  // 내 실행이 여럿이면 가장 앞의 것
  head.textContent=isFinite(pos)&&pos>1?`대기 중이에요 · 대기줄 ${pos}번째 (앞에 ${pos-1}개)`:'에이전트가 일하는 중이에요';
}
// 단계 한 줄: AI가 고른 도구는 판단 글과 따로 태그로 (예전 기록의 '도구 선택: a, b' 글도 서버가 태그로 바꿔 준다)
function stepHTML(t){
  const tags=(t.tags||[]).map(x=>`<span class="chip tool">${esc(x)}</span>`).join('');
  return `<div class="log${/^AI (판단|끝냄)/.test(t.step)?' log-ai':''}"><b>${esc(t.step)}</b>${tags?` <span class="log-tags">${tags}</span>`:''}${t.detail?` ${esc(t.detail)}`:''}</div>`;
}
function traceHTML(trace, at){
  if(!trace?.length) return '';
  return `<details class="more trace"><summary>에이전트 동작 보기 (${trace.length}단계${at?`, ${fmtDT(at,{sec:true})}`:''})</summary>${trace.map(stepHTML).join('')}</details>`;
}
// 화면을 다시 열어도 마지막 에이전트 실행의 단계를 보여 준다 (진행 중인 칸은 덮지 않음)
async function lastTrace(box, events, month=''){
  const jobId=state.current;
  try{ const runs=await api('GET',`/api/jobs/${jobId}/agent/runs?events=${events}&limit=1${month?`&month=${month}`:''}`);
    const el=$(box); if(jobId!==state.current || !el || el.querySelector('.live')) return;
    el.innerHTML=runs.length?traceHTML(runs[0].steps, runs[0].at):''; }catch(e){}
}
$('#seekRun').onclick=async()=>{
  $('#seekErr').textContent='';
  try{ const r=await agent('#seekLive',()=>api('POST','/api/seek/check',seekInput())); $('#seekLive').innerHTML='';
    $('#seekItems').innerHTML=r.items.map(itemHTML).join('');
    $('#seekQs').innerHTML=r.questions.map(q=>`<li>${esc(q)}</li>`).join('');
    $('#seekTrace').innerHTML=traceHTML(r.trace, r.trace_at);
    $('#seekForm').classList.add('hidden'); $('#seekUpload').classList.add('hidden'); $('#seekResult').classList.remove('hidden');
    $('#seekClose').classList.toggle('hidden',!state.seekFromApp); window.scrollTo(0,0); refreshNav();
  }catch(e){ $('#seekErr').textContent=e.message; }
};
function seekReset(){ $('#seekLive').innerHTML=''; const n=seek.node; n.querySelectorAll('input').forEach(i=>i.value=''); n.querySelector('.f-type').value='';
  const bh=n.querySelector('.bizno-hint'); bh.textContent=BIZ_HINT; bh.classList.remove('bad');
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
  n.querySelector('.f-bizno').value=d.biz_no;
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
  const saving=flushAll(); currentTab=v; lsSet('alba.tab', v);
  $$('nav.tabs button').forEach(b=>b.toggleAttribute('aria-current',b.dataset.v===v)); $$('nav.tabs button[aria-current]').forEach(b=>b.setAttribute('aria-current','page'));
  $$('.view').forEach(x=>x.classList.toggle('active',x.id==='v-'+v)); window.scrollTo(0,0);
  // 저장 중인 입력이 끝난 뒤에 화면을 불러온다 (먼저 불러오면 저장 전 값이 다시 보여 덮어쓸 수 있음)
  saving.then(()=>({home:loadHome, pay:loadPayTab, check:loadCheck, docs:loadDocs, guard:loadGuard})[v]()).catch(e=>toast(e.message));
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
  const v=$('#meBirth').value, email=$('#meEmail').value.trim(); if(!v){ $('#meErr').textContent='생년월일을 입력해 주세요'; return; }
  const bad=emailProblem(email); if(bad){ $('#meErr').textContent=bad; $('#meEmail').focus(); return; }
  const birthChanged=v!==state.me.birth_date;
  try{ state.me=await api('PUT','/api/me',{birth_date:v,email}); closeOverlay(); showTab(currentTab,false); toast(birthChanged?'생년월일을 고쳤어요. 점검을 다시 해 보세요':'내 정보를 저장했어요'); }
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
  const j=curJob(); applyCurrent(); $('#punchLive').innerHTML=''; $('#quitLive').innerHTML=''; $('#quitFormLive').innerHTML='';
  await refreshRecords();
  if(state.shiftFor!==j.id) $('#shiftResult').classList.add('hidden');
  await renderQuit();
  await loadOverview();
  await loadAsk();
  await loadCase();
  await loadAlerts();
}
// 홈의 AI 에이전트 종합 점검: 코드가 정리한 사실(지금 기록) + 마지막 AI 종합 판단과 조언.
// 아직 없거나 AI 판단 대기면 AI가 있을 때 한 번 바로 점검한다. 그 뒤로는 버튼과 매일 자동 점검으로 새로 한다
async function loadOverview(){
  const jobId=state.current;
  const o=await api('GET',`/api/jobs/${jobId}/overview`); if(jobId!==state.current) return;
  const running=$('#ovLive .live'); if(running && +running.dataset.job===jobId) return;
  if(running) $('#ovLive').innerHTML='';  // 다른 일하는 곳의 진행 칸 (그 점검은 서버에서 그대로 끝난다)
  const v=o.overview;
  ovStatus(v?v.status:'none', v?LABEL[v.status]:'아직 안 함');
  ovSummary(o.parts);
  const quit=curJob()?.status==='quit';  // 그만둔 곳: 퇴직 정산 줄은 위 #quitPanel이 버튼과 함께 보여 준다 (두 번 나오지 않게)
  $('#ovParts').innerHTML=o.parts.filter(p=>!(quit && p.name==='퇴직 후 임금 정산')).map(ovRowHTML).join('');
  $('#ovAiWrap').open=!v || v.status==='pending';  // 판단이 없거나 기다리는 중이면 펼쳐 둔다 (대기 표시가 가려지지 않게)
  $('#ovAi').innerHTML=v?aiJudgeHTML(v):o.ai?'':`<div class="ai-judge"><div class="ai-judge-head"><b>AI 에이전트 판단</b></div><p><span class="wait-note">AI 응답 대기 중</span> AI가 연결되면 판단해요.</p></div>`;
  $('#ovNote').textContent=v?`${fmtDT(v.at)} 점검${o.changed?' · 그 뒤로 기록이 바뀌었어요. 다시 점검해 보세요':''}`:'';
  if(!$('#ovLive').innerHTML) lastTrace('#ovLive','overview').then(ovTraceJudge); else ovTraceJudge();
  // 자동 점검은 사업장마다 화면을 연 동안 한 번만. 점검 시각을 열쇠에 넣으면, AI가 답을 못 내 대기로 끝날 때마다
  // 새 시각으로 열쇠가 바뀌어 끝없이 다시 돌았다 (실제 모델에서 진행 칸이 '시작'부터 계속 다시 뜸). 대기로 남은 것은 뒤의 다시 맡기기가 한다
  if(o.need && !homeBusy()) rejudgeOnce(`overview-${jobId}`, runOverview);  // 다른 점검이 도는 중이면 그 점검이 끝나고 다시 부를 때 시작
}
// 종합 점검의 '에이전트 동작 보기' 끝에 'AI 에이전트 판단 보기'와 같은 판단(결과, 이유, 근거 조항, 근거 사실)을 붙인다
function ovTraceJudge(){
  const d=$('#ovLive details.trace'); if(!d) return;
  d.querySelector('.trace-judge')?.remove();
  const judge=$('#ovAi').innerHTML.trim(); if(!judge) return;
  d.insertAdjacentHTML('beforeend',`<div class="trace-judge">${judge}</div>`);
}
// 홈에서 에이전트 진행 칸은 한 번에 하나만 (퇴직 정산과 종합 점검이 동시에 돌면 같은 단계가 두 칸에 겹쳐 보였다)
function homeBusy(){ return quitBusy || !!document.querySelector('#v-home .live'); }  // quitBusy: 그만둔 날 저장, 받았어요/못 받았어요가 점검을 곧 시작함
function waitOthers(){ if(!homeBusy()) return false; toast('에이전트가 다른 점검을 하는 중이에요. 끝난 뒤에 다시 눌러 주세요'); return true; }
async function runOverview(){
  const jobId=state.current;
  ovStatus('pending', JUDGING);
  $('#ovAi').innerHTML=`<div class="ai-judge"><div class="ai-judge-head"><b>AI 에이전트 판단</b></div><p><span class="wait-note">${JUDGING}</span></p></div>`;
  try{ const r=await agent('#ovLive',()=>api('POST',`/api/jobs/${jobId}/agent/overview`));
    if(jobId!==state.current) return;  // 그사이 다른 일하는 곳으로 바꿨으면 그곳 카드를 덮지 않는다
    $('#ovLive').innerHTML=traceHTML(r.trace, r.trace_at);
  }catch(e){ if(jobId!==state.current) return; toast(e.message); }
  // 다시 불러오기: 그사이 로그아웃했거나 화면을 떠났으면 조용히 그만둔다 (자동 점검은 잡아 주는 곳이 없어 오류로 샌다)
  try{ await loadOverview(); await loadAsk(); await loadCase(); await loadAlerts(); }catch(e){ if(e.status!==401) toast(e.message); }
  if(jobId===state.current) $('#ovAiWrap').open=true;  // 끝나면 판단 결과를 펼쳐 보여 준다
}
$('#ovRun').onclick=()=>{ if(!waitOthers()) runOverview(); };
// 종합 점검 머리줄: 결과 색의 점과 결과 이름 (판단 중이면 도는 표시)
function ovStatus(status, text){ $('#ovHead').className='ov-head '+status; $('#ovTag').className='tag '+status; $('#ovTag').textContent=text; }
// 한 줄 결론과 남은 항목: 코드가 항목의 결과를 센다 (AI 판단 글은 아래 'AI 에이전트 판단 보기'에)
function ovSummary(parts){
  const of=s=>parts.filter(p=>p.status===s), bad=of('bad'), warn=[...of('warn'),...of('pending')], todo=parts.filter(p=>p.todo);
  const n=warn.length+todo.length;
  $('#ovHeadline').textContent=bad.length?`위반이 의심되는 것이 ${bad.length}개 있어요`
    :n?`확인할 것이 ${n}개 있어요`:'지금은 확인할 것이 없어요';
  const names=list=>list.map(p=>p.name).join(', ');
  $('#ovSummary').textContent=[bad.length&&`위반 의심: ${names(bad)}`, warn.length&&`확인 필요: ${names(warn)}`,
    todo.length&&`아직 안 한 점검: ${names(todo)}`].filter(Boolean).join(' · ');
}
// 기록 한 줄: 이름(과 짧은 설명), 오른쪽에 결과나 값. 누르면 그 기록이 있는 화면으로
function ovRowHTML(p){
  const wait=p.wait?`<span class="wait-note">${esc(p.wait)}</span>`:'';
  const right=p.status?`<span class="tag ${p.status}">${LABEL[p.status]}</span>`:p.todo?'<span class="ov-val muted">아직 안 함</span>':`<span class="ov-val">${esc(p.text)}</span>`;
  const sub=(p.status&&p.text)||wait?`<span class="ov-text">${p.status?esc(p.text):''}${p.status&&p.text&&wait?' ':''}${wait}</span>`:'';
  const go=p.tab||(p.name==='근무 기록'?'records':p.name==='답을 기다리는 질문'?'ask':p.name==='퇴근 안 누른 기록'?'records':'');
  return `<li><button type="button" class="ov-row" ${go?`data-go="${go}"`:'disabled'}><span class="ov-main"><span class="ov-name">${esc(p.name)}</span>${sub}</span>${right}${go?'<svg class="ov-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>':''}</button></li>`;
}
$('#ovParts').addEventListener('click',e=>{ const b=e.target.closest('[data-go]'); if(!b) return; const g=b.dataset.go;
  if(g==='records') $('.job-card').scrollIntoView({behavior:'smooth',block:'start'});
  else if(g==='ask') $('#askCard').scrollIntoView({behavior:'smooth',block:'start'});
  else showTab(g); });
// 에이전트의 질문: 답하면 질문했던 점검을 에이전트가 다시 판단한다
const RESUME_TAB={contract_check:'check',payday:'pay',report:'docs',guard_review:'guard'};
async function loadAsk(){
  const qs=await api('GET',`/api/jobs/${state.current}/questions`);
  if(state.askFor!==state.current) $('#askTrace').innerHTML='';  // 다른 일하는 곳의 결과는 지운다
  $('#askCard').classList.toggle('hidden',!qs.length && !$('#askTrace').innerHTML);
  $('#askList').innerHTML=qs.map(q=>`<div class="ask" data-q="${q.id}"><p class="ask-q">${esc(q.question)}</p>
      <p class="sub">${esc(q.why)}${q.law?` (${esc(q.law)})`:''}</p>
      <div class="ask-opts">${q.options.map(o=>`<button class="btn ghost small" data-a="${esc(o)}">${esc(o)}</button>`).join('')}</div>
      <div class="kw-row"><input class="input" placeholder="직접 적어서 답하기" aria-label="직접 답하기"><button class="btn ghost small" data-own>보내기</button></div>
      <button class="link small muted" data-close>이 질문 닫기</button></div>`).join('');
  $$('#askList .ask').forEach(el=>{ const id=el.dataset.q;
    el.querySelectorAll('[data-a]').forEach(b=>b.onclick=()=>answerAsk(id,b.dataset.a));
    el.querySelector('[data-own]').onclick=()=>{ const v=el.querySelector('input').value.trim(); if(!v){ toast('답을 적어 주세요'); return; } answerAsk(id,v); };
    el.querySelector('[data-close]').onclick=async()=>{ try{ await api('DELETE',`/api/questions/${id}`); await loadAsk(); }catch(e){ toast(e.message); } };
  });
}
async function answerAsk(id, answer){
  $$('#askList button').forEach(b=>b.disabled=true); state.askFor=state.current;
  try{ await api('POST',`/api/questions/${id}/answer`,{answer, check:false}); toast('답을 저장했어요. 에이전트가 다시 판단해요');
    const r=await checkAfterSave('#askTrace',()=>api('POST',`/api/questions/${id}/rerun`)); if(!r){ await loadAsk(); return; }
    $('#askTrace').innerHTML=traceHTML(r.trace, r.trace_at)+(RESUME_TAB[r.event]?`<button class="btn ghost small" style="margin-top:8px" data-go="${RESUME_TAB[r.event]}">다시 판단한 결과 보기</button>`:'');
    $$('#askTrace [data-go]').forEach(b=>b.onclick=()=>showTab(b.dataset.go));
    toast('답을 받아 에이전트가 다시 판단했어요'); await loadAsk(); await loadCase(); await loadAlerts();
  }catch(e){ toast(e.message); $$('#askList button').forEach(b=>b.disabled=false); }
}
// AI 에이전트 진행 상황(코드가 정리)과 에이전트 조언, 메모(에이전트가 남김)
const NEXT_TAB={check:'계약서 점검하러 가기',pay:'급여 점검하러 가기',docs:'상담 사전 자료 만들러 가기',guard:'신고 후 보호로 가기'};
async function loadCase(){
  const c=await api('GET',`/api/jobs/${state.current}/case`);
  $('#caseSteps').innerHTML=c.progress.map(p=>`<li class="${p.done?'done':''} tone-${p.tone||'none'}"><strong>${p.done?'✓ ':''}${esc(p.name)}</strong><span>${waitMark(p.detail)}</span></li>`).join('');
  const a=c.advice;
  $('#caseAdvice').innerHTML=a?`<div class="advice"><div class="advice-head">에이전트 조언</div><p>${esc(a.text)}</p>
      ${a.next_tab&&NEXT_TAB[a.next_tab]?`<button class="btn ghost small" data-go="${esc(a.next_tab)}">${NEXT_TAB[a.next_tab]}</button>`:''}
      <div class="sub note-at">${a.event==='advice'?'매일 종합 조언':a.event==='overview'?'종합 점검':`${esc(EVENT[a.event]||a.event)} 뒤`} ${fmtDT(a.created_at)}</div></div>`
    // 조언이 아직 없을 때: AI가 있으면 점검을 기다리는 것이지 AI를 기다리는 게 아니다 (도는 표시는 AI를 기다릴 때만)
    :c.ai?'<p class="ai-note">아직 조언이 없어요. 종합 점검을 하면 에이전트가 조언을 남겨요.</p>'
    :'<p class="ai-note wait">AI 응답 대기 중: AI가 연결되면 에이전트가 조언해 드려요.</p>';
  $$('#caseAdvice [data-go]').forEach(b=>b.onclick=()=>showTab(b.dataset.go));
  const fdt=t=>{ const d=new Date(t.replace(' ','T')); return `${d.getMonth()+1}월 ${d.getDate()}일 ${d.getHours()}시`; };
  $('#caseFollow').innerHTML=c.followups.length?`<div class="follow"><div class="advice-head">에이전트가 예약한 확인</div>
    <ul class="list">${c.followups.map(f=>`<li><div class="main"><strong>${fdt(f['때'])} ${esc(f['점검'])}${f['달']?` (${esc(f['달'])})`:''}</strong>
      <div class="sub">${esc(f['이유'])}</div></div><button class="btn ghost small" data-cancel="${f.task_id}">취소</button></li>`).join('')}</ul></div>`:'';
  $$('#caseFollow [data-cancel]').forEach(b=>b.onclick=async()=>{ if(!confirm('이 예약을 취소할까요?')) return;
    try{ await api('DELETE',`/api/followups/${b.dataset.cancel}`); await loadCase(); toast('예약을 취소했어요'); }catch(e){ toast(e.message); } });
  $('#caseMemWrap').classList.toggle('hidden',!c.memory.length);
  $('#caseMem').innerHTML=c.memory.map(m=>`<div class="log"><b>${esc(EVENT[m['실행']]||m['실행'])}</b> ${esc(m['기억'])}<div class="sub note-at">${esc(m['날짜'])}</div></div>`).join('');
}
// 알림: 지금 보고 있는 일하는 곳의 알림만 보여 준다
async function loadAlerts(){
  const jid=state.current, ns=await api('GET',`/api/notifications?job_id=${jid}`);
  $('#alertsClear').classList.toggle('hidden',!ns.length);
  $('#alerts').innerHTML=ns.length?ns.map(n=>`<div class="alert"><span class="dot ${n.read?'read':''}"></span>
      <div class="main"><strong>${waitMark(n.title)}</strong><div class="sub">${waitMark(n.body)}</div><div class="sub note-at">${fmtDT(n.at)}</div></div>
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
    // 시각 기록은 바로 끝내고, 퇴근 뒤 그날 근무 점검(에이전트)은 이어서 따로 한다
    const punch=body=>api('POST',`/api/jobs/${state.current}/punch`,{...body, check:false});
    try{ r=await punch(pos||{}); }
    catch(e){
      if(e.status!==409) throw e;
      // 방금 출근했거나 퇴근을 오래 안 눌렀을 때는 한 번 더 확인한다
      if(!confirm(e.message)){ toast('기록하지 않았어요'); return; }
      r=await punch({...(pos||{}), confirm:true});
    }
    const t=fmtDT(r.server_time,{sec:true});
    setPunchUI(r.action==='in', r.action==='in'?`${t} 출근 기록됨${pos?', 위치 함께 기록':''}`:`${t} 퇴근 기록됨`);
    await refreshRecords(false);
    toast(r.action==='in'?'출근이 기록됐어요':'퇴근이 기록됐어요');
    if(r.shift_record){
      renderShiftWaiting();  // 예전 결과('찾지 못했어요')가 남아 보이지 않게, 점검하는 동안은 찾는 중
      const shift=await checkAfterSave('#punchLive',()=>api('POST',`/api/jobs/${state.current}/agent/shift?record_id=${r.shift_record}`));
      // 동작 보기는 결과 칸 안에 한 번만
      if(shift){ $('#punchLive').innerHTML=''; renderShift(shift); if(shift.items?.length) toast('오늘 근무에서 확인할 점이 있어요'); }
      else $('#shiftResult').classList.add('hidden');
    }
  }catch(e){ toast(e.message); } finally{ btn.disabled=curJob().status==='quit'; }
};

// 근무 기록 (홈)
function todayStr(){ const d=new Date(); return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`; }
// 근무 기간 날짜 검사 (서버와 같은 규칙). 그만둔 날은 앞으로 그만둘 날일 수도 있어 오늘보다 뒤여도 된다
function dateProblem(d){
  if(d.start_date && d.start_date>todayStr()) return '근무 시작일은 오늘보다 뒤일 수 없어요';
  if(d.start_date && d.end_date && d.end_date<d.start_date) return '계약 종료일은 근무 시작일보다 앞일 수 없어요';
  if(d.start_date && d.status==='quit' && d.quit_date && d.quit_date<d.start_date) return '그만둔 날은 근무 시작일보다 앞일 수 없어요';
  return '';
}
function thisMonth(){ const d=new Date(); return `${d.getFullYear()}-${pad(d.getMonth()+1)}`; }
// 출퇴근 버튼 상태, 퇴근 잊음 안내, 기록 목록을 한 번에 새로 그린다
async function refreshRecords(updateState=true){
  const r=await api('GET',`/api/jobs/${state.current}/records`);
  const live=r.records.filter(x=>!x.void), open=live.find(x=>!x.clock_out), last=live.find(x=>x.clock_out);
  if(updateState) setPunchUI(r.working, open?`${fmtDT(open.clock_in,{sec:true})} 출근 기록됨`:(last?`마지막 퇴근 ${fmtDT(last.clock_out,{sec:true})}`:'아직 출근 기록이 없어요'));
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
const REC_SHOW=5;  // 홈에는 최근 근무 기록 5개만 보이고, 나머지는 '모두 보기'로 펼친다 (기록은 모두 그대로)
function renderRecords(recs){
  $('#recordsEmpty').classList.toggle('hidden',recs.length>0); $('#recordsPanel').classList.toggle('hidden',!recs.length);
  const more=$('#recMore'); more.classList.toggle('hidden',recs.length<=REC_SHOW);
  if(state.recFor!==state.current){ state.recFor=state.current; state.recAll=false; }
  more.textContent=state.recAll?'접기':`근무 기록 모두 보기 (${recs.length}건)`;
  $('#records').classList.toggle('show-all',!!state.recAll);
  $('#records').innerHTML=recs.map(x=>{
    // 출퇴근은 증거라 초까지. 밤을 넘겨 퇴근하면 퇴근 날짜도 적는다
    const outDay=x.clock_out&&x.clock_out.slice(0,10)!==x.clock_in.slice(0,10)?fmtDay(x.clock_out,{wd:false})+' ':'';
    const when=`${fmtTime(x.clock_in,{sec:true})} 출근, ${x.clock_out?outDay+fmtTime(x.clock_out,{sec:true})+' 퇴근':(x.void?'퇴근 없음':'근무 중')}`;
    const side=x.void
      ? `<button class="btn ghost small rec-act" data-unvoid="${x.id}">표시 취소</button>`
      : `<span class="tag rec-gps ${x.gps?'ok':'warn'}">${x.gps?'위치 기록':'위치 미기록'}</span><button class="link muted small rec-act" data-void="${x.id}">실수로 누름</button>`;
    const note=x.void?`<div class="sub void-note">실수로 표시함 (${fmtDT(x.void_at,{sec:true})}), ${esc(x.void_reason)}. 급여 계산과 점검에서 빠져요</div>`:'';
    return `<li class="${x.void?'void':''}"><div class="main"><strong class="num">${fmtDay(x.clock_in)}</strong>
      <div class="sub num rec-time">${when}</div>${note}</div>${side}</li>`;
  }).join('');
}
$('#recMore').onclick=()=>{ state.recAll=!state.recAll; $('#records').classList.toggle('show-all',state.recAll);
  $('#recMore').textContent=state.recAll?'접기':`근무 기록 모두 보기 (${$$('#records > li').length}건)`; };
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
function renderShiftWaiting(){
  state.shiftFor=state.current;
  const box=$('#shiftResult'); box.classList.remove('hidden');
  box.innerHTML=`<div class="panel"><strong>오늘 근무 점검</strong><p class="sub" style="margin:2px 0 0"><span class="wait-note">퇴근 기록에서 확인할 점을 찾는 중이에요</span></p></div>`;
}
function renderShift(sh){
  state.shiftFor=state.current;
  const box=$('#shiftResult'); box.classList.remove('hidden');
  const head=sh.items.length?`<strong>오늘 근무 점검</strong><p class="sub" style="margin:2px 0 10px">퇴근 기록으로 쉬는 시간, 청소년 근로시간 한도, 야간근로를 바로 확인했어요.</p>`
    :`<strong>오늘 근무 점검</strong><p class="sub" style="margin:2px 0 0">퇴근 기록에서 확인할 점을 찾지 못했어요.</p>`;
  box.innerHTML=`<div class="panel">${head}${sh.items.map(itemHTML).join('')}${traceHTML(sh.trace, sh.trace_at)}</div>`;
}
// 버튼(그만둔 날 저장, 받았어요/못 받았어요)이 곧 점검을 시작하는 동안에는 자동 재점검을 하지 않는다 (두 번 돌지 않게)
let quitBusy=false;
async function renderQuit(){
  const j=curJob(); const quit=j.status==='quit';
  $('#quitOpen').classList.toggle('hidden',quit); $('#quitForm').classList.add('hidden'); $('#quitPanel').classList.toggle('hidden',!quit); refreshNav();
  if(!quit) return;
  const {settlement:st, ai}=await api('GET',`/api/jobs/${j.id}/settlement`);
  if(!st){ $('#quitTag').className='tag warn'; $('#quitTag').textContent='확인 필요'; $('#quitText').textContent='그만둔 날을 입력하면 정산 기한을 계산해 드려요.'; return; }
  const f=s=>{const d=new Date(s+'T00:00:00'); return `${d.getMonth()+1}월 ${d.getDate()}일`;};
  $('#quitTag').className='tag '+st.status; $('#quitTag').textContent=LABEL[st.status]; $('#quitAi').innerHTML=aiJudgeHTML(st);
  if(st.status==='pending' && ai && !quitBusy && !homeBusy()) rejudgeOnce(`quit-${j.id}-${st.due}-${j.paid_after_quit}`, quitCheck);
  $('#quitText').textContent = j.paid_after_quit===true ? `${f(st.quit_date)}에 그만뒀고, 남은 임금을 받았다고 기록했어요.`
    : st.left>=0 ? `${f(st.quit_date)}에 그만뒀어요. 남은 임금 지급 기한은 ${f(st.due)}로, ${st.left}일 남았어요.`
    : `${f(st.quit_date)}에 그만뒀고 지급 기한 ${f(st.due)}이 지났어요. 아직 못 받았다면 상담을 준비하세요.`;
  $('#quitReport').classList.toggle('hidden',!(j.paid_after_quit===false||(st.status==='bad'&&j.paid_after_quit!==true)));
}
// 판단 대기인 결과가 보이면 (기록이 바뀌었거나 AI가 없던 때 저장됨) AI가 있을 때 한 번 다시 점검한다 (기다려도 바뀌지 않으므로)
const rejudged=new Set();
function rejudgeOnce(key, run){ if(rejudged.has(key)) return; rejudged.add(key); autoRun(run); }
function openQuitForm(){ const j=curJob(); $('#quitDateMain').value=j.quit_date||''; $('#quitDateMain').min=j.start_date||''; $('#quitForm').classList.remove('hidden'); $('#quitOpen').classList.add('hidden'); refreshNav();
  reveal($('#quitForm')); }  // '그만둔 날 고치기'는 위 종합 점검 카드에 있어 아래 입력 칸까지 옮긴다
function closeQuitForm(){ $('#quitForm').classList.add('hidden'); $('#quitOpen').classList.toggle('hidden',curJob().status==='quit'); refreshNav(); }
$('#quitOpen').onclick=openQuitForm; $('#quitEdit').onclick=openQuitForm; $('#quitCancel').onclick=goBack;
$('#quitSave').onclick=async()=>{ if(waitOthers()) return; const v=$('#quitDateMain').value; if(!v){ toast('그만둔 날을 골라 주세요'); return; }
  const st=curJob().start_date; if(st && v<st){ toast('그만둔 날은 근무 시작일보다 앞일 수 없어요'); return; }
  quitBusy=true;
  try{ await api('POST',`/api/jobs/${state.current}/quit`,{quit_date:v, check:false}); await loadJobs(); closeQuitForm(); await loadHome();
    toast('그만둔 날을 저장했어요. 이어서 퇴직 정산을 점검해요'); await quitCheck('#quitFormLive'); }catch(e){ toast(e.message); }finally{ quitBusy=false; afterQuit(); } };
// box: 누른 버튼 바로 아래 진행 칸 (그만둔 날 저장은 아래 입력 칸, 받았어요/못 받았어요는 종합 점검 카드 안)
async function quitCheck(box='#quitLive'){
  $('#quitTag').className='tag pending'; $('#quitTag').textContent=JUDGING;
  $('#quitAi').innerHTML=`<div class="ai-judge"><div class="ai-judge-head"><b>AI 에이전트 판단</b></div><p><span class="wait-note">${JUDGING}</span></p></div>`;
  const r=await checkAfterSave(box,()=>api('POST',`/api/jobs/${state.current}/agent/quit`)); if(!r){ renderQuit().catch(()=>{}); return; }
  $(box).innerHTML='';  // 동작 보기는 종합 점검 카드의 것 하나만 (에이전트 동작 기록 탭에는 실행마다 남는다)
  await loadJobs(); await renderQuit(); await loadAsk(); await loadOverview();  // 퇴직 정산이 바뀌었으니 종합 점검도 새로
}
async function setPaid(p){ if(waitOthers()) return; quitBusy=true; try{ await api('POST',`/api/jobs/${state.current}/paid`,{paid:p, check:false}); await loadJobs(); await renderQuit();
    await loadAsk();  // 받았는지 묻던 질문은 이 버튼으로 답한 것이라 서버가 닫는다
    toast(p?'받았다고 기록했어요':'못 받았다고 기록했어요'); await quitCheck(); }catch(e){ toast(e.message); }finally{ quitBusy=false; afterQuit(); } }
// 퇴직 정산 점검이 끝난 뒤: 기다리던 종합 점검(자동)을 이어서 시작할 수 있게 다시 부른다
function afterQuit(){ if(state.inApp && currentTab==='home') loadOverview().catch(()=>{}); }
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
    <span class="total">계약 시급 기준 받아야 할 금액</span><span class="total num">${won(e.total)}</span>
    <span>받은 금액</span><span class="num">${r.paid==null?'미입력':won(r.paid)}</span></div>
    <div class="result ${c.status}" style="margin-top:12px"><div class="head"><span class="law">비교 결과</span><span class="tag ${c.status}">${LABEL[c.status]}</span></div><p>${esc(c.text)}</p>${aiJudgeHTML(c)}</div>
    ${e.notes.map(n=>`<p class="hint">${esc(n)}</p>`).join('')}`;
}
// 급여 점검 탭
async function loadPayTab(){ applyCurrent(); if(!$('#payMonth').value) $('#payMonth').value=thisMonth(); $('#payTrace').innerHTML=''; $('#payOcrLive').innerHTML=''; await loadPay(); await loadPayslips(); }
async function loadPay(){ const month=$('#payMonth').value, r=await api('GET',`/api/jobs/${state.current}/pay?month=${month}`);
  if($('#payTrace .live')) return;  // 점검이 도는 중이면 '판단 중' 화면을 예전 결과로 덮지 않는다
  $('#payBody').innerHTML=payHTML(r);
  lastTrace('#payTrace','payday',month);
  if(r.compare?.status==='pending' && r.ai && !$('#payTrace .live'))
    rejudgeOnce(`pay-${state.current}-${month}-${r.paid}-${r.expected?.total}`, ()=>payCheck(month,'AI 판단을 기다리던 급여 비교를 다시 점검했어요')); }
async function loadPayslips(){
  const ps=await api('GET',`/api/jobs/${state.current}/payslips`); state.payslips=ps;
  const months=[...new Set(ps.map(p=>p.month))];
  $('#payslips').innerHTML=months.length?months.map(m=>{ const rows=ps.filter(p=>p.month===m), sum=rows.reduce((a,p)=>a+p.amount,0);
    return `<li class="pay-month"><div class="main"><strong class="num">${esc(fmtMonth(m))}분</strong>${rows.length>1?`<span class="sub num"> 합계 ${won(sum)} (${rows.length}건)</span>`:''}
      ${rows.map(p=>`<div class="pay-item"><span class="num">${won(p.amount)}${p.evidence_id?`, <a class="ev-link" href="/api/evidence/${p.evidence_id}/file" target="_blank">명세서 원본</a>`:''}<span class="sub pay-at">${fmtDT(p.created_at,{sec:true})} 저장</span></span>
        <span class="row-btns"><button class="btn ghost small" data-edit="${p.id}" data-amt="${p.amount}">고치기</button><button class="btn ghost small danger" data-del="${p.id}" data-m="${esc(m)}">지우기</button></span></div>`).join('')}</div></li>`; }).join('')
    :'<li><span class="sub">아직 저장한 받은 급여가 없어요</span></li>';
  $$('#payslips [data-edit]').forEach(b=>b.onclick=async()=>{ const v=prompt('고친 금액을 원 단위 숫자로 적어 주세요',b.dataset.amt); if(v===null) return;
    const n=Number(String(v).replace(/[^0-9]/g,'')); if(!String(v).trim()||Number.isNaN(n)){ toast('숫자로 적어 주세요'); return; }
    try{ const s=await api('PUT',`/api/payslips/${b.dataset.edit}`,{amount:n, check:false}); await loadPayslips(); toast('고쳤어요. 이어서 그 달을 다시 비교해요');
      $('#payMonth').value=s.month||$('#payMonth').value; await payCheck(s.month, '고치고 다시 비교했어요'); }catch(e){ toast(e.message); } });
  $$('#payslips [data-del]').forEach(b=>b.onclick=async()=>{ if(!confirm(`${b.dataset.m} 받은 금액 한 건을 지울까요? 명세서 원본은 증거 자료로 남아요.`)) return;
    try{ await api('DELETE',`/api/payslips/${b.dataset.del}`); await loadPay(); await loadPayslips(); toast('지웠어요'); }catch(e){ toast(e.message); } });
  payModeSync();
}
// 이 달에 이미 저장한 금액이 있으면 '따로 더 받았어요 / 금액 고치기'를 고르게 한다
function payModeSync(){ const m=$('#payMonth').value, rows=(state.payslips||[]).filter(p=>p.month===m);
  $('#payModeWrap').classList.toggle('hidden',!rows.length);
  if(rows.length){ $('#payModeLabel').textContent=`${m}에 이미 저장한 금액이 있어요 (합계 ${won(rows.reduce((a,p)=>a+p.amount,0))})`;
    if(!segVal($('#payModeWrap'),'paymode')) setSeg($('#payModeWrap'),'paymode','add'); } }
bindSeg($('#payModeWrap'));
// 점검이 도는 중이면 진행 칸은 지우지 않는다 (끝나면 결과로 바뀐다)
$('#payMonth').onchange=()=>{ if(!$('#payTrace .live')) $('#payTrace').innerHTML=''; payModeSync(); loadPay().catch(e=>toast(e.message)); };
$('#payRun').onclick=async()=>{ try{ judging('#payBody'); const r=await agent('#payTrace',()=>api('POST',`/api/jobs/${state.current}/agent/payday?month=${$('#payMonth').value}`)); $('#payBody').innerHTML=payHTML(r); $('#payTrace').innerHTML=traceHTML(r.trace, r.trace_at); toast('에이전트가 급여를 점검했어요'); }catch(e){ toast(e.message); loadPay().catch(()=>{}); } };
// 명세서 사진: 고르면 바로 원본 저장 후 AI가 읽어 금액과 달을 채운다. 사용자가 확인하고 저장한다.
let payslipEv=null;
$('#payFile').onchange=async e=>{ const f=e.target.files[0]; if(!f) return; const fd=new FormData(); fd.append('file',f);
  payslipEv=null; $('#payOcr').textContent='명세서를 저장하고 읽는 중이에요…';
  fd.append('read','false');
  try{ const saved=await api('POST',`/api/jobs/${state.current}/payslip/read`,fd,true); payslipEv=saved.evidence_id;
    $('#payOcr').textContent='명세서 원본을 저장했어요. AI가 읽는 중이에요…';
    const r=await checkAfterSave('#payOcrLive',()=>api('POST',`/api/jobs/${state.current}/agent/read?evidence_id=${saved.evidence_id}&kind=payslip`));
    $('#payOcrLive').innerHTML=''; if(!r){ $('#payOcr').textContent='명세서 원본을 저장했어요. 금액은 직접 입력해 주세요.'; return; }
    if(r.ai){
      if(r.month){ $('#payMonth').value=r.month; payModeSync(); } if(r.net_pay!=null) $('#payAmount').value=r.net_pay;
      const parts=[r.month&&`${r.month}분`, r.net_pay!=null&&`실지급액 ${won(r.net_pay)}`, r.base_pay!=null&&`기본급 ${won(r.base_pay)}`,
        r.weekly_holiday_pay!=null&&`주휴수당 ${won(r.weekly_holiday_pay)}`, r.deduction!=null&&`공제 ${won(r.deduction)}`].filter(Boolean);
      $('#payOcr').textContent=`AI가 읽은 내용: ${parts.join(', ')||'읽은 항목이 없어요'}. 명세서와 같은지 확인하고 저장해 주세요.`;
    } else $('#payOcr').textContent=`명세서 원본을 저장했어요. ${r.reason}`;
  }catch(err){ $('#payOcr').textContent=''; toast(err.message); } };
$('#paySave').onclick=async()=>{
  const amt=parseWon($('#payAmount').value); if(amt==null){ toast('받은 금액을 입력해 주세요 (예: 557,280원, 55만원)'); return; }
  const fd=new FormData(); fd.append('month',$('#payMonth').value); fd.append('amount',amt); if(payslipEv) fd.append('evidence_id',payslipEv);
  if(!$('#payModeWrap').classList.contains('hidden')) fd.append('mode',segVal($('#payModeWrap'),'paymode')||'add');
  fd.append('check','false');
  try{ const s=await api('POST',`/api/jobs/${state.current}/payslip`,fd,true);
    $('#payAmount').value=''; $('#payFile').value=''; $('#payOcr').textContent=''; payslipEv=null; await loadPayslips(); payModeSync();
    toast('저장했어요. 이어서 그 달 급여를 비교해요'); await payCheck(s.month, '저장하고 비교했어요'); }catch(e){ toast(e.message); }
};
// 받은 금액을 저장한 뒤 그 달 급여 점검(에이전트)을 이어서 한다
async function payCheck(month, done){
  judging('#payBody');
  const r=await checkAfterSave('#payTrace',()=>api('POST',`/api/jobs/${state.current}/agent/payday?month=${month}`)); if(!r) { loadPay().catch(()=>{}); return; }
  $('#payBody').innerHTML=payHTML(r); $('#payTrace').innerHTML=traceHTML(r.trace, r.trace_at); toast(done);
}

// 점검
async function loadCheck(){
  $('#ocrLive').innerHTML='';
  const f=await api('GET',`/api/jobs/${state.current}/contract/fields`);
  // 무엇을 적는지는 칸 안의 흐린 안내 글(placeholder)로 보여 주고, 쓸 수 없는 글자는 입력하는 동안 뺀다
  const rules=state.rules?.contract||{};
  $('#fieldsBox').innerHTML='<p class="hint" style="margin:0 0 10px">계약서에 적힌 대로 옮겨 적고, 계약서에 없는 칸은 비워 두세요.</p>'
    +f.items.map(k=>`<label class="field"><span>${esc(k)}</span><textarea class="input field-area" rows="2" data-k="${esc(k)}"
      placeholder="${esc(rules[k]?.placeholder||'계약서에 없으면 비워 두세요')}">${esc(f.fields[k]||'')}</textarea></label>`).join('')
    +'<p class="hint save-state" id="fieldsSaved">입력하면 바로 저장돼요</p>';
  const jobId=state.current;
  $$('#fieldsBox [data-k]').forEach(i=>{ const k=i.dataset.k, r=rules[k];
    const grow=()=>{ i.style.height='auto'; i.style.height=`${i.scrollHeight+2}px`; };
    const run=bindChars(i, r?.chars, ()=>toast(`${k}에는 ${r.allowed}만 쓸 수 있어요`));
    run(); grow();  // 예전에 저장된 값에 쓸 수 없는 글자가 있으면 화면에서 빼 둔다 (점검하기를 누르면 이 값으로 저장)
    i.addEventListener('input',()=>{ grow(); autosave(`fields-${jobId}`,()=>saveFields(jobId),'#fieldsSaved'); }); });
  const r=await api('GET',`/api/jobs/${state.current}/check`); renderCheck(r.items, r.created_at); $('#checkTrace').innerHTML=''; lastTrace('#checkTrace','contract_check');
  const stale=r.ai && (r.items||[]).some(i=>i.status==='pending') && !rechecked.has(jobId);
  const ls=await api('GET','/api/law/status');
  const mw=(ls.min_wage?` 최저임금 ${ls.min_wage.year}년 시간급 ${won(ls.min_wage.value)}, 근거 고시: ${ls.min_wage.source}.`:'')
    +(ls.min_wage_missing_years?.length?` ${ls.min_wage_missing_years.join(', ')}년 최저임금 값은 아직 등록 전이에요.`:'');
  $('#lawStatus').textContent=ls.built?`법 기준표: 법제처 현행 법령 ${Object.keys(ls.laws).length}개, 조문 ${Object.values(ls.laws).reduce((a,b)=>a+b,0)}개, 참고 판례·해석 ${ls.refs}건.${mw}`
    :'법 기준표 미구축: 법제처 API 키를 등록하고 조문을 불러오면 결과마다 조문 원문이 붙어요.';
  await loadLog();
  if(stale) autoRun(()=>recheck(jobId));
}
// AI가 없던 때 저장된 'AI 응답 대기 중' 결과: 지금 AI가 있으면 화면을 열 때 한 번 다시 점검한다 (기다려도 바뀌지 않으므로)
const rechecked=new Set();
async function recheck(jobId){
  rechecked.add(jobId);
  judging('#checkItems');
  try{ const r=await agent('#checkTrace',()=>api('POST',`/api/jobs/${jobId}/check`)); if(jobId!==state.current) return;
    renderCheck(r.items, r.created_at); $('#checkTrace').innerHTML=traceHTML(r.trace, r.trace_at); await loadLog(); toast('AI 판단을 기다리던 항목을 다시 점검했어요');
  }catch(e){ toast(e.message); reloadCheck(); } }
// 점검이 실패하면 '판단 중' 표시를 저장된 결과로 되돌린다
async function reloadCheck(){ try{ const r=await api('GET',`/api/jobs/${state.current}/check`); renderCheck(r.items, r.created_at); }catch(e){} }
function renderCheck(items, at){
  $('#checkAt').textContent=items&&at?`마지막 점검 ${fmtDT(at,{sec:true,wd:true})}`:'';
  if(!items){ $('#checkSummary').innerHTML=''; $('#checkItems').innerHTML='<p class="sub">아직 점검하지 않았어요. 계약서 내용을 확인하고 점검해 보세요.</p>'; return; }
  const c=s=>items.filter(i=>i.status===s).length;
  // AI 판단 전에는 확인 중과 확인 필요만, AI가 판단하면 정상과 위반 의심도 보여 준다
  const keys=['bad','warn','pending','ok'].filter(k=>c(k));
  $('#checkSummary').innerHTML=keys.map(k=>`<div class="${k}"><strong>${c(k)}</strong>${k==='pending'?'<span class="wait-note">AI 판단 대기</span>':LABEL[k]}</div>`).join('');
  const order={bad:0,warn:1,pending:2,ok:3}; $('#checkItems').innerHTML=[...items].sort((a,b)=>order[a.status]-order[b.status]).map(itemHTML).join('');
}
// 에이전트 동작 기록: 실행 한 번씩 묶고, 펼치면 계획, AI 판단, 도구 실행, 항목별 AI 판단 결과가 차례로 보인다
async function loadLog(){ const jobId=state.current; const runs=await api('GET',`/api/jobs/${jobId}/agent/runs?limit=15`); if(jobId!==state.current) return;
  $('#agentLog').innerHTML=runs.length?runs.map(r=>{ const end=r.steps[r.steps.length-1]; const ai=r.steps.filter(t=>t.step==='AI 판단 결과').length;
    return `<details class="run"><summary><span class="sub log-at">${fmtDT(r.at,{sec:true})}</span> <b>${esc(EVENT[r.event]||r.event)}</b>
      ${end&&end.step==='결과'?waitMark(end.detail.slice(0,80)):''}${ai?` <span class="chip">AI 판단 ${ai}건</span>`:''}</summary>${r.steps.map(stepHTML).join('')}</details>`; }).join('')
    :'<p class="sub">기록이 없어요</p>'; }
// 계약서 사진: 원본 저장 후 AI(비전 모델)가 읽은 값을 칸에 채운다. 저장은 사용자가 확인한 내용으로.
// 계약서가 여러 장이면 한 번에 골라도 된다. 장마다 원본으로 저장하고 AI가 읽어, 앞 장에서 채운 칸은 뒤 장이 덮어쓰지 않는다
$('#contractFile').onchange=async e=>{ const files=[...e.target.files]; if(!files.length) return;
  const filled=new Set(); let found=0, total=0, ai=false, reason='', trace=[];
  try{
    for(const [i,f] of files.entries()){
      $('#ocrNote').textContent=`계약서를 저장하고 읽는 중이에요… (${i+1}/${files.length}장)`;
      const fd=new FormData(); fd.append('file',f); fd.append('read','false');
      const saved=await api('POST',`/api/jobs/${state.current}/contract`,fd,true);  // 원본 저장은 바로 끝난다
      const r=await checkAfterSave('#ocrLive',()=>api('POST',`/api/jobs/${state.current}/agent/read?evidence_id=${saved.evidence.id}&kind=contract`));
      $('#ocrLive').innerHTML=''; if(!r){ reason='AI가 읽지 못해 칸은 직접 입력해 주세요'; continue; }
      trace=trace.concat(r.trace||[]);
      if(!r.ai){ reason=r.reason; continue; }
      ai=true; total=r.total;
      $$('#fieldsBox [data-k]').forEach(inp=>{ const v=r.fields[inp.dataset.k]; if(v && !filled.has(inp.dataset.k)){ inp.value=v; filled.add(inp.dataset.k); } });
    }
    found=filled.size;
    if(ai){
      fieldsCache[state.current]=readFields(); autosave(`fields-${state.current}`,()=>saveFields(state.current),'#fieldsSaved',0);
      $('#ocrNote').textContent=`AI가 계약서 ${files.length}장에서 ${total}개 항목 중 ${found}개를 읽었어요. 사진과 비교해 틀린 곳을 고쳐 주세요. 빈 칸은 계약서에 없거나 읽지 못한 항목이에요.`;
      toast('계약서를 읽어 칸을 채웠어요');
    } else { $('#ocrNote').textContent=`계약서 원본 ${files.length}장을 저장했어요. ${reason}`; toast('계약서 원본을 저장했어요'); }
    $('#checkTrace').innerHTML=traceHTML(trace);
  }catch(err){ $('#ocrNote').textContent=''; toast(err.message); } e.target.value=''; };
function readFields(){ const fields={}; $$('#fieldsBox [data-k]').forEach(i=>fields[i.dataset.k]=i.value.replace(/\s+/g,' ').trim()); return fields; }
// 입력하던 사업장 번호를 고정해 두어, 저장 전에 다른 곳으로 바꿔도 섞이지 않게 한다
const fieldsCache={};
function saveFields(jobId){ return api('PUT',`/api/jobs/${jobId}/contract/fields`,{fields:fieldsCache[jobId]}); }
$('#fieldsBox').addEventListener('input',()=>{ fieldsCache[state.current]=readFields(); });
$('#checkRun').onclick=async()=>{
  fieldsCache[state.current]=readFields(); pendingSaves.delete(`fields-${state.current}`); clearTimeout(saveTimers[`fields-${state.current}`]);
  try{ await saveFields(state.current); $('#fieldsSaved').textContent='자동 저장됨'; judging('#checkItems'); const r=await agent('#checkTrace',()=>api('POST',`/api/jobs/${state.current}/check`));
    renderCheck(r.items, r.created_at); $('#checkTrace').innerHTML=traceHTML(r.trace, r.trace_at); await loadLog(); toast('점검을 마쳤어요'); }catch(e){ toast(e.message); reloadCheck(); }
};

// 자료
async function loadDocs(){
  const evs=await api('GET',`/api/jobs/${state.current}/evidence`);
  // 파일로 올린 자료, 사업장 등록 전에 올린 공고, 주소와 확인 시각만 남긴 게시물을 모두 보여 준다
  $('#evList').innerHTML=evs.length?evs.map(e=>e.file
    ?`<li><div class="main"><strong>${esc(KIND[e.kind]||e.kind)}${e.before_job?' (지원 전)':''}</strong>
      <div class="sub">${esc(e.filename)}, ${fmtDT(e.uploaded_at,{sec:true})} 올림</div><a class="ev-link" href="/api/evidence/${e.id}/file" target="_blank">원본 보기</a></div><span class="tag ok">원본</span></li>`
    :`<li><div class="main"><strong>게시물 주소</strong>
      <div class="sub">${esc(e.filename)}, ${fmtDT(e.uploaded_at,{sec:true})} 확인</div>${safeUrl(e.note)!=='#'?`<a class="ev-link" href="${esc(e.note)}" target="_blank" rel="noopener noreferrer">게시물 열기</a>`:''}</div><span class="tag warn">주소만</span></li>`).join('')
    :'<li><span class="sub">아직 올린 자료가 없어요</span></li>';
  const rs=await api('GET',`/api/jobs/${state.current}/reports`);
  $('#reportList').innerHTML=rs.map(r=>`<li><div class="main"><strong>상담 사전 자료</strong><div class="sub">${fmtDT(r.created_at,{sec:true})} 작성</div></div><a class="ev-link" href="${esc(r.url)}" target="_blank">열기</a></li>`).join('');
  $('#reportTrace').innerHTML=''; lastTrace('#reportTrace','report');
  const cs=await api('GET','/api/counsel');
  $('#counsel').innerHTML=cs.map(c=>`<div class="panel"><strong>${esc(c.name)}</strong><div class="sub">${esc(c.note)}</div><div class="num" style="margin-top:4px">${esc(c.phone)}</div></div>`).join('');
}
// 증거 자료는 여러 개를 한 번에 골라도 하나씩 원본으로 저장한다 (파일마다 올린 시각과 SHA-256이 따로 남음)
$('#evFile').onchange=async e=>{ const files=[...e.target.files]; if(!files.length) return; let ok=0;
  for(const f of files){ const fd=new FormData(); fd.append('file',f); fd.append('kind',$('#evKind').value);
    try{ await api('POST',`/api/jobs/${state.current}/evidence`,fd,true); ok++; }catch(err){ toast(`${f.name}: ${err.message}`); } }
  await loadDocs(); if(ok) toast(`자료 ${ok}개를 원본으로 저장했어요`); e.target.value=''; };
// 상담 사전 자료: 에이전트가 일하는 동안 이 화면에서 진행을 보여 주고, 다 만든 뒤에 자료 화면으로 넘어간다
// (새 창을 먼저 열면 진행이 안 보이는 빈 창만 보인다). 자료에서 뒤로 가기를 누르면 이 화면으로 돌아온다
$('#reportBtn').onclick=async()=>{
  const btn=$('#reportBtn'); btn.disabled=true;
  try{ const r=await agent('#reportTrace',()=>api('POST',`/api/jobs/${state.current}/report`));
    await loadDocs(); $('#reportTrace').innerHTML=traceHTML(r.trace, r.trace_at);
    toast(r.summary_ai?'상담 사전 자료를 만들었어요. 자료를 열어요':'AI 응답 대기 중이라 AI 요약 없이 만들었어요. 자료를 열어요');
    setTimeout(()=>{ location.href=r.url; }, 1200); }
  catch(e){ toast(e.message); } finally{ btn.disabled=false; } };

// 보호
async function loadGuard(){ $('#guardTrace').innerHTML=''; $('#reportedLive').innerHTML=''; renderGuard(await api('GET',`/api/jobs/${state.current}/guard`));
  lastTrace('#guardTrace','guard_on,guard_off,guard_search,guard_preserve,guard_review'); }
function renderGuard(g){
  $('#reported').checked=g.reported; $('#guardOn').classList.toggle('hidden',!g.reported); if(!pendingSaves.has(`msg-${state.current}`)) $('#warnMsg').value=g.message;
  $('#msgReset').classList.toggle('hidden',!g.custom_message); msgSource(g.message_source);
  state.keywords=g.keywords;
  $('#kwList').innerHTML=g.keywords.map((k,i)=>`<span class="chip kw">${esc(k)}<button type="button" data-i="${i}" aria-label="${esc(k)} 지우기">×</button></span>`).join('');
  $$('#kwList [data-i]').forEach(b=>b.onclick=()=>saveKeywords(state.keywords.filter((_,i)=>i!==Number(b.dataset.i))));
  $('#kwQueries').textContent=`검색할 말: ${g.queries.join(' / ')}`;
  const st={pending:['pending','AI 응답 대기 중'],suspect:['bad','보복 의심'],ok:['ok','문제 없음'],unclear:['warn','확인 필요']};
  $('#postList').innerHTML=g.posts.length?g.posts.map(p=>`<li class="post"><div class="main"><strong>${esc(p.title||'제목 없음')}</strong>
    <div class="sub"><a href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener noreferrer">${esc(p.url)}</a></div>
    ${p.ai_reason?`<div class="sub">AI 판별 근거: ${esc(p.ai_reason)}</div>`:''}
    <div class="sub">${fmtDT(p.found_at,{sec:true})} 확인${p.evidence_id?`, <a class="ev-link" href="/api/evidence/${p.evidence_id}/file" target="_blank">보존한 화면</a>`:', 화면 캡처 없음'}</div></div>
    <span class="tag ${st[p.status][0]}">${st[p.status][1]}</span></li>`).join(''):'<li><span class="sub">아직 확인한 게시물이 없어요</span></li>';
}
const MSG_SOURCE={ai:'AI가 이번 상황에 맞게 작성한 문구예요. 고쳐 써도 돼요.',
  waiting:'AI 응답 대기 중이라 기본 문구를 보여 드려요. AI가 응답하면 상황에 맞는 문구로 바뀌어요.',custom:'직접 고친 문구예요.'};
function msgSource(src){ const n=$('#msgSource'); n.textContent=MSG_SOURCE[src]||''; n.classList.toggle('wait',src==='waiting'); }
// 켬/끔은 바로 저장하고, 안내 문구 작성(AI)은 이어서 따로 부른다
$('#reported').onchange=async e=>{ try{
    renderGuard(await api('POST',`/api/jobs/${state.current}/guard`,{reported:e.target.checked, check:false}));
    const g=await checkAfterSave('#reportedLive',()=>api('POST',`/api/jobs/${state.current}/agent/guard`)); if(!g) return;
    renderGuard(g); $('#reportedLive').innerHTML=traceHTML(g.trace, g.trace_at); }catch(err){ toast(err.message); } };
async function saveKeywords(list){ try{ renderGuard(await api('PUT',`/api/jobs/${state.current}/guard/keywords`,{keywords:list})); }catch(e){ toast(e.message); } }
$('#kwAdd').onclick=()=>{ const v=$('#kwInput').value.trim(); if(!v){ toast('검색어를 넣어 주세요'); return; }
  $('#kwInput').value=''; saveKeywords([...(state.keywords||[]), ...v.split(',').map(x=>x.trim()).filter(Boolean)]); };
$('#kwInput').addEventListener('keydown',e=>{ if(e.key==='Enter'){ e.preventDefault(); $('#kwAdd').click(); } });
$('#warnMsg').addEventListener('input',()=>{
  const jobId=state.current, msg=$('#warnMsg').value;
  autosave(`msg-${jobId}`,()=>api('PUT',`/api/jobs/${jobId}/guard/message`,{message:msg}).then(g=>{ if(jobId===state.current){ $('#msgReset').classList.toggle('hidden',!g.custom_message); msgSource(g.message_source); } }),'#msgSaved');
});
$('#msgReset').onclick=async()=>{ if(!confirm('고친 문구를 지우고 원래 문구로 되돌릴까요?')) return;
  try{ renderGuard(await api('PUT',`/api/jobs/${state.current}/guard/message`,{message:''})); $('#msgSaved').textContent='원래 문구로 되돌렸어요'; }catch(e){ toast(e.message); } };
$('#copyMsg').onclick=()=>{ const t=$('#warnMsg').value; (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>toast('안내 문구를 복사했어요'),()=>toast('직접 선택해 복사해 주세요')); };
// 게시물 주소는 여러 개를 띄어 써서 한 번에 넣어도 된다 (하나씩 보존하고 판별)
$('#postAdd').onclick=async()=>{ const urls=[...new Set($('#postUrl').value.split(/\s+/).filter(Boolean))]; if(!urls.length){ toast('게시물 주소를 넣어 주세요'); return; }
  let ok=0, last=null;
  let caps=0, why='';
  $('#guardTrace').innerHTML='<p class="sub wait-note">게시물을 보존하는 중이에요…</p>';
  for(const url of urls){ try{ last=await api('POST',`/api/jobs/${state.current}/guard/posts`,{url, check:false}); ok++;
    if(last.captured) caps++; else why=last.capture_error||why; }catch(e){ toast(`${url.slice(0,40)}: ${e.message}`); } }
  $('#guardTrace').innerHTML='';
  if(ok){ $('#postUrl').value=''; await loadGuard();
    toast(caps===ok?`게시물 ${ok}개의 화면과 주소, 확인 시각을 보존했어요 (자료 탭에 있어요)`
      :`게시물 ${ok}개의 주소와 확인 시각을 보존했어요 (자료 탭에 있어요). ${ok-caps}개는 화면 캡처를 못 했어요${why?`: ${why}`:''}`);
    const r=await checkAfterSave('#guardTrace',()=>api('POST',`/api/jobs/${state.current}/agent/review`));  // 보존한 뒤 AI 판별
    if(r){ await loadGuard(); $('#guardTrace').innerHTML=traceHTML(r.trace, r.trace_at); } } };
$('#postSearch').onclick=async()=>{ try{ const r=await agent('#guardTrace',()=>api('POST',`/api/jobs/${state.current}/guard/search`)); await loadGuard(); $('#guardTrace').innerHTML=traceHTML(r.trace, r.trace_at); toast(r.skipped?r.reason:`새 게시물 ${r.added}건을 찾았어요`); }catch(e){ toast(e.message); } };

// ---------- 처음 쓰는 사람 체험 안내 (새로 가입한 계정만, app/guide.py) ----------
// 가입할 때 고른 상황의 미션 3개, 업로드할 때 '내 기기에서 고르기'와 예시 자료, 다 하면 설문. 안내가 없는 계정은 아무것도 가로채지 않는다
const guide={st:null, doneShown:false, timer:null, input:null, bypass:false};
function guideSoon(){ if(!guide.st?.on) return; clearTimeout(guide.timer); guide.timer=setTimeout(guideRefresh, 700); }
async function guideRefresh(){
  if(!state.me) return;
  try{ guide.st=await api('GET','/api/guide/state'); }catch(e){ return; }
  guideRender();
}
function guideOpen(id){ $(id).classList.remove('hidden'); }
function guideClose(id){ $(id).classList.add('hidden'); }
async function guideMark(key){ try{ guide.st=await api('POST','/api/guide/mark',{key}); guideRender(); }catch(e){ toast(e.message); } }
function guideRender(){
  const st=guide.st, chip=$('#guideChip');
  $('#surveyBtn').classList.toggle('hidden', !st?.survey_url);  // 설문은 일하는 곳 선택 창에 늘 있다
  guideJobFillSync();  // 가입 직후에는 등록 화면이 먼저 열리고 안내 상태가 뒤에 온다
  // 안내가 없거나, 다 하고 완료 창을 닫았으면 버튼을 숨긴다 (설문은 메뉴에)
  if(!st?.on || (st.all_done && st.closed)){ chip.classList.add('hidden'); if(!st?.on) ['#guideIntro','#guideSheet','#guideDone'].forEach(guideClose); return; }
  chip.classList.remove('hidden'); chip.classList.toggle('done', st.all_done);
  chip.textContent = st.all_done ? '체험 완료 · 설문하기' : `체험 미션 ${st.done_count}/${st.missions.length}`;
  $('#guideSheetSub').textContent=`${st.title}: 하나씩 해 보면 핵심 기능을 다 써 볼 수 있어요.`;
  $('#guideMissions').innerHTML=st.missions.map((m,i)=>`<li class="${m.done?'done':''}"><span class="guide-num">${m.done?'✓':i+1}</span>
    <div class="guide-m"><b>${esc(m.title)}</b><p>${esc(m.how)}</p>${m.done?'':`<button class="btn ghost small" type="button" data-guide-go="${esc(m.go)}">바로 가기</button>`}</div></li>`).join('');
  $('#guideSheetSurvey').classList.toggle('hidden', !st.all_done || !st.survey_url);
  if(!st.intro_seen && !visible('#guideIntro')) guideIntro();
  if(st.all_done && !st.closed && !guide.doneShown){ guide.doneShown=true; $('#guideDoneSurvey').classList.toggle('hidden', !st.survey_url); guideOpen('#guideDone'); }
}
function guideIntro(){
  const st=guide.st; $('#guideIntroMode').textContent=st.title;
  $('#guideIntroList').innerHTML=st.missions.map(m=>`<li>${esc(m.title)}</li>`).join('');
  guideOpen('#guideIntro');
}
$('#guideIntroGo').onclick=()=>{ guideClose('#guideIntro'); guideMark('intro'); };
$('#guideIntroSkip').onclick=()=>{ guideClose('#guideIntro'); guideMark('off'); toast('체험 안내를 껐어요. 바로 써 보세요'); };
$('#guideChip').onclick=()=>{ if(guide.st?.all_done) guideOpen('#guideDone'); else guideOpen('#guideSheet'); };
$('#guideSheetX').onclick=()=>guideClose('#guideSheet');
$('#guideOff').onclick=()=>{ if(!confirm('체험 안내를 끌까요? 다시 켤 수는 없어요. 기능은 모두 그대로 쓸 수 있어요.')) return;
  guideClose('#guideSheet'); guideMark('off'); };
// 미션의 바로 가기: 그 기능이 있는 화면으로
function guideGo(v){
  if(v==='seek'){ if(state.inApp) $('#seekEntry').click(); else { state.mode='seek'; seekReset(); openOverlay('obSeek'); } return; }
  if(v==='job'){
    if(visible('#ob2')) return;  // 이미 등록 화면
    if(state.inApp){ $('#addPlace').click(); return; }
    if(visible('#seekResult')){ toast("아래 '이곳에서 일하게 됐어요, 등록하기'를 눌러 주세요"); $('#seekToWork').scrollIntoView({block:'center'}); return; }
    toast('지금 상황을 고르고 다음을 눌러 일하는 곳을 등록해 주세요'); openOverlay('ob0'); return;
  }
  if(!state.inApp){ toast('먼저 일하는 곳을 등록해 주세요. 지금 상황을 고르고 다음을 누르면 돼요'); openOverlay('ob0'); return; }
  if(visible('#onboard')) closeOverlay();
  showTab(v);
}
$('#guideMissions').addEventListener('click',e=>{ const b=e.target.closest('[data-guide-go]'); if(!b) return; guideClose('#guideSheet'); guideGo(b.dataset.guideGo); });
function guideSurvey(){ const u=guide.st?.survey_url; if(u) window.open(u, '_blank', 'noopener'); }
function guideDoneClose(){ guideClose('#guideDone'); if(!guide.st?.closed) guideMark('closed'); }
$('#guideDoneX').onclick=guideDoneClose;
$('#guideKeep').onclick=guideDoneClose;
$('#guideDoneSurvey').onclick=()=>{ guideSurvey(); guideDoneClose(); };
$('#guideSheetSurvey').onclick=()=>{ guideSurvey(); guideClose('#guideSheet'); };
$('#surveyBtn').onclick=()=>{ closePicker(); guideSurvey(); };
// 체험하는 동안(또는 데모 모드) 업로드를 누르면: 내 기기에서 고르기 + 예시 자료. '이 자료 올리기'를 누르면 그 파일을 원래 업로드 칸에 넣어 그대로 올린다
document.addEventListener('click',e=>{
  if(guide.bypass || !guide.st?.files) return;  // 체험 안내가 끝났거나 꺼져 있고 데모 모드도 아니면 서버가 files를 비워 보낸다
  const input=e.target.closest('input[type=file]') || e.target.closest('label')?.querySelector('input[type=file]');
  if(!input || !(guide.st.files[input.id]||[]).length) return;
  e.preventDefault(); e.stopPropagation(); guideFiles(input);
}, true);
function guideFiles(input){
  guide.input=input;
  $('#guideFilesSub').textContent=guide.st.demo?'또는 데모 자료 (가상의 가게예요. 이 칸에 맞는 자료부터)':'또는 예시 자료로 해 보기 (가상의 가게예요)';
  $('#guideFileList').innerHTML=guide.st.files[input.id].map((f,i)=>`<div class="guide-file"><img src="${esc(f.url)}" alt="${esc(f.label)}">
    <p>${esc(f.label)}</p><button class="btn ghost small" type="button" data-guide-file="${i}">이 자료 올리기</button></div>`).join('');
  guideOpen('#guideFiles');
}
$('#guideFileList').addEventListener('click',async e=>{
  const b=e.target.closest('[data-guide-file]'); if(!b || !guide.input) return;
  const f=guide.st.files[guide.input.id][+b.dataset.guideFile];
  b.disabled=true; b.textContent='가져오는 중';
  try{
    const r=await fetch(f.url); if(!r.ok) throw new Error('자료를 가져오지 못했어요. 다시 눌러 주세요');
    const blob=await r.blob();
    const dt=new DataTransfer(); dt.items.add(new File([blob], f.name.split('/').pop(), {type:blob.type||'image/png'}));
    guide.input.files=dt.files; guideClose('#guideFiles');
    guide.input.dispatchEvent(new Event('change',{bubbles:true}));
    if(f.fill && guide.input.id==='seekFile'){ guideFillNode(seek.node, seek, f.fill); toast('예시 공고의 조건으로 칸을 채웠어요. 확인하고 점검해 보세요'); }
  }catch(err){ toast(err.message); b.disabled=false; b.textContent='이 자료 올리기'; }
});
$('#guideFilesX').onclick=()=>guideClose('#guideFiles');
$('#guideOwnFile').onclick=()=>{ const inp=guide.input; guideClose('#guideFiles'); if(!inp) return; guide.bypass=true; inp.click(); setTimeout(()=>{ guide.bypass=false; },0); };
// 예시 자료에 적힌 조건으로 입력칸 채우기 (공고 입력칸, 일하는 곳 카드 공용: 이름, 업종, 하는 일, 시급, 시작일, 근무 요일과 시간, 수습)
function guideFillNode(n, holder, d){
  const set=(sel,v)=>{ const el=n.querySelector(sel); if(el && v!=null){ el.value=v; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); } };
  set('.f-name',d.name); set('.f-type',d.industry); set('.f-work',d.work_desc); set('.f-wage',d.wage); set('.f-start',d.start_date);
  holder.schedule=JSON.parse(JSON.stringify(d.schedule||{}));
  if(DAY_KEYS.some(k=>holder.schedule[k])){ n.querySelector('.sched-sum').textContent=schedSummary(holder.schedule); n.querySelector('.sched-go').textContent='수정'; }
  if(d.probation) setSeg(n,'probation',d.probation);
}
// 일하는 곳 등록 화면: 체험하는 동안(또는 데모 모드) 첫 카드가 비어 있으면 예시 계약서와 '이 예시로 채우기'
function guideJobFillSync(){
  const box=$('#guideJobFill'), jf=(guide.st?.on || guide.st?.demo) && Object.keys(guide.st.files||{}).length ? guide.st.job_fill : null, first=state.cards[0];
  const empty=first && !first.node.querySelector('.f-name').value.trim();
  if(!jf || !empty || !visible('#ob2')){ box.classList.add('hidden'); return; }
  box.innerHTML=`<p class="guide-fill-title">예시 자료: ${esc(jf.label)}</p><img src="${esc(jf.url)}" alt="${esc(jf.label)}">
    <button class="btn ghost block" type="button" id="guideJobFillGo">이 예시로 채우기</button>`;
  box.classList.remove('hidden');
  $('#guideJobFillGo').onclick=()=>{ const c=state.cards[0]||addCard(); guideFillNode(c.node, c, jf.fill); box.classList.add('hidden');
    toast('예시 계약서 내용으로 채웠어요. 나머지를 확인하고 시작하기를 눌러 주세요'); };
}

boot();
