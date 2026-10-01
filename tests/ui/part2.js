const path = require('path');
// 2부: 앱 안 모든 화면 (기본 설정: 실제 모델이 없으니 가짜 AI가 답함)
const { B, ck, browser, page, vis, toast, api, overflow, summary } = require('./lib');
const fs = require('fs');
const tab = async (p, v) => { await p.evaluate(v=>document.querySelector(`#tabs [data-v="${v}"]`).click(), v); await p.waitForTimeout(900); };
const bodyText = p => p.evaluate(()=>document.body.innerText);
const PREFIX = '테스트 답변입니다';
(async () => {
  const b = await browser(); const p = await page(b);
  p.removeAllListeners('dialog'); p.answer='';
  p.on('dialog', d=>{ p.lastDialog=d.message(); d.type()==='prompt' ? d.accept(p.answer).catch(()=>{}) : d.accept().catch(()=>{}); });
  await p.goto(B+'/'); await p.waitForTimeout(300);
  ck('[버전] /api/version', !!(await api(p,'GET','/api/version')).body.version);
  await api(p,'POST','/api/auth/register',{email:'qa2@example.com',password:'test1234',birth_date:'2009-05-01'});
  ck('[AI] 기본 설정에서 가짜 AI 켜짐', (await api(p,'GET','/api/ai/status')).body.fake===true);
  const job = (await api(p,'POST','/api/jobs',{name:'QA 가상분식',wage:9000,payday:31,paytype:'월급',start_date:'2026-08-01',size:'lt5',
    schedule:{'월':{start:'17:00',end:'22:30',brk:'없음'},'수':{start:'18:00',end:'23:00',brk:'30분'}}})).body;
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(800);
  // ---- 홈 ----
  await p.screenshot({path:'p2_home0.png'});
  ck('[홈] 출근 버튼', (await p.textContent('#punchBtn')).includes('출근'));
  const before = Date.now();
  await p.click('#punchBtn'); await p.waitForTimeout(800);
  await p.click('#punchBtn'); await p.waitForTimeout(1500);
  ck('[홈] 1분 안 퇴근은 한 번 더 물음', (p.lastDialog||'').includes('퇴근'), p.lastDialog);
  let recs = (await api(p,'GET',`/api/jobs/${job.id}/records`)).body;
  const rec = (recs.records||recs)[0];
  ck('[홈] 출퇴근 기록 저장', rec && rec.clock_out, JSON.stringify(rec).slice(0,120));
  ck('[홈] 출근 시각은 서버 시각', rec && Math.abs(new Date(rec.clock_in+'+09:00') - before) < 120000, rec && rec.clock_in);
  ck('[홈] 브라우저가 보낸 시각은 무시', (await api(p,'POST',`/api/jobs/${job.id}/punch`,{kind:'in',time:'2020-01-01T00:00:00'})).status<500 &&
     !JSON.stringify((await api(p,'GET',`/api/jobs/${job.id}/records`)).body).includes('2020-01-01'));
  await api(p,'POST',`/api/jobs/${job.id}/punch`,{kind:'out',force:true}); await api(p,'POST',`/api/jobs/${job.id}/punch`,{kind:'out',confirm:true});
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(1000);
  p.answer='테스트로 잘못 누름';
  const voidBtn = await p.$('#records [data-void]');
  if (voidBtn) { await voidBtn.click(); await p.waitForTimeout(800); }
  ck('[홈] 실수로 누름 표시 (시각은 남음)', !!voidBtn && (await p.textContent('#records')).includes('실수'), (await p.textContent('#records')).slice(0,120));
  ck('[홈] 출퇴근 기록은 지우기 버튼 없음', !(await p.$('#records [data-del]')));
  await p.click('label:has(#gpsOn)'); await p.waitForTimeout(700);
  ck('[홈] 위치 기록 동의 저장', (await api(p,'GET','/api/me')).body.gps_consent===true);
  await p.click('label:has(#gpsOn)'); await p.waitForTimeout(500);
  ck('[홈] 위치 기록 동의 끄기', (await api(p,'GET','/api/me')).body.gps_consent===false);
  const home = await bodyText(p);
  ck('[홈] "AI 에이전트 진행 상황" 표기', home.includes('AI 에이전트 진행 상황'));
  const adv0 = await p.textContent('#caseAdvice');
  ck('[홈] 조언 없을 때 AI 대기로 보이지 않음 (AI 있음)', adv0.includes(PREFIX) || (adv0.includes('아직 조언이 없어요') && !(await p.$('#caseAdvice .wait'))), adv0.slice(0,80));
  await p.screenshot({path:'p2_home.png', fullPage:true});
  ck('[홈] 390px 넘침 없음', (await overflow(p)).length===0, await overflow(p));
  // ---- 급여 ----
  await tab(p,'pay');
  await p.fill('#payMonth', '2026-09').catch(()=>{});
  await p.type('#payAmount','55만원'); await p.press('#payAmount','Tab'); await p.waitForTimeout(200);
  ck('[급여] "55만원" → 550000', await p.inputValue('#payAmount')==='550000');
  await p.click('#paySave'); await p.waitForTimeout(2500);
  ck('[급여] 저장하고 비교', (await toast(p)).includes('비교'), await toast(p));
  ck('[급여] 비교 결과에 AI 판단', (await p.textContent('#payBody')).length>10, (await p.textContent('#payBody')).slice(0,100));
  await p.type('#payAmount','1만'); await p.press('#payAmount','Tab');
  ck('[급여] 같은 달 두 번째: 더하기/바꾸기 선택 보임', !(await p.$eval('#payModeWrap',e=>e.classList.contains('hidden'))));
  await p.click('#paySave'); await p.waitForTimeout(2500);
  const slips = (await api(p,'GET',`/api/jobs/${job.id}/payslips`)).body;
  ck('[급여] 나눠 받은 금액 두 건 저장', slips.filter(s=>s.month==='2026-09').length===2, JSON.stringify(slips).slice(0,150));
  p.answer='600000'; await p.click('#payslips [data-edit]'); await p.waitForTimeout(800);
  ck('[급여] 받은 금액 고치기', (await api(p,'GET',`/api/jobs/${job.id}/payslips`)).body.some(s=>s.amount===600000));
  await p.click('#payslips [data-del]'); await p.waitForTimeout(800);
  ck('[급여] 받은 금액 지우기', (await api(p,'GET',`/api/jobs/${job.id}/payslips`)).body.filter(s=>s.month==='2026-09').length===1);
  await p.click('#payRun'); await p.waitForTimeout(2500);
  ck('[급여] 지금 점검 (에이전트)', (await toast(p)).includes('점검'), await toast(p));
  ck('[급여] 에이전트 동작 기록에 계획', (await p.textContent('#payTrace')).includes('계획'));
  await p.screenshot({path:'p2_pay.png', fullPage:true});
  ck('[급여] 390px 넘침 없음', (await overflow(p)).length===0, await overflow(p));
  // ---- 계약서 ----
  await tab(p,'check');
  const ph = await p.$$eval('#fieldsBox textarea', t=>t.map(x=>[x.dataset.k, x.placeholder, x.value]));
  ck('[계약서] 7개 칸 모두 흐린 안내 글', ph.length===7 && ph.every(x=>x[1].includes('예:')), ph.map(x=>x[0]).join(','));
  ck('[계약서] 저장된 "0" 없음', ph.every(x=>x[2]!=='0'));
  ck('[계약서] 칸 아래 설명 줄 없음', !(await p.$('#fieldsBox .field .hint')));
  await p.type('#fieldsBox [data-k="근무장소"]', 'Seoul 창원시 12'); await p.waitForTimeout(200);
  ck('[계약서] 근무장소에 영문 입력 빠짐', !(/[A-Za-z]/.test(await p.inputValue('#fieldsBox [data-k="근무장소"]'))), await p.inputValue('#fieldsBox [data-k="근무장소"]'));
  await p.type('#fieldsBox [data-k="휴일"]', '매주 일요일'); await p.waitForTimeout(100);
  await tab(p,'home'); await tab(p,'check');
  ck('[계약서] 바로 탭을 바꿔도 입력 저장', (await p.inputValue('#fieldsBox [data-k="휴일"]'))==='매주 일요일');
  ck('[계약서] 서버: 칸 규칙 밖 글자 거절', (await api(p,'PUT',`/api/jobs/${job.id}/contract/fields`,{fields:{'근무장소':'<script>'}})).status===400);
  const img = fs.readFileSync(path.join(__dirname, '..', '..', '테스트자료', '02_근로계약서.png'));
  await p.setInputFiles('#contractFile', {name:'계약서.png', mimeType:'image/png', buffer:img}); await p.waitForTimeout(3000);
  ck('[계약서] 사진 올리면 가짜 AI가 칸을 채움', (await p.inputValue('#fieldsBox [data-k="임금"]')).includes(PREFIX), await p.textContent('#ocrNote'));
  await p.click('#checkRun');
  let live=false; for(let i=0;i<40 && !live;i++){ live = await p.evaluate(()=>!!document.querySelector('#checkTrace .live .wait-note')); if(!live) await p.waitForTimeout(25); }
  await p.waitForTimeout(3500);
  ck('[계약서] 점검 중 진행 표시', live);
  const items = await p.textContent('#checkItems');
  ck('[계약서] 점검 결과에 조항과 AI 판단 근거(테스트 답변)', items.includes('근로기준법') && items.includes(PREFIX));
  ck('[계약서] 결과는 정상/확인 필요/위반 의심만', !(await p.$$eval('#checkItems .tag', ts=>ts.map(t=>t.textContent).filter(x=>!['정상','확인 필요','위반 의심'].includes(x.trim()) && !x.includes('기준표') && !x.includes('근무 기록') && !x.includes('입력 정보')))).length,
     await p.$$eval('#checkItems .tag', ts=>[...new Set(ts.map(t=>t.textContent.trim()))].join(',')));
  await p.screenshot({path:'p2_check.png', fullPage:true});
  await tab(p,'home');
  ck('[홈] 점검 뒤 조언에 테스트 답변', (await p.textContent('#caseAdvice')).includes(PREFIX), (await p.textContent('#caseAdvice')).slice(0,80));
  ck('[홈] 점검 뒤 알림에 테스트 답변', (await p.textContent('#alerts')).includes(PREFIX));
  ck('[홈] 에이전트가 기억하는 것', (await p.textContent('#caseMem')).includes(PREFIX));
  await tab(p,'check');
  ck('[계약서] 390px 넘침 없음', (await overflow(p)).length===0, await overflow(p));
  // 지원 전 확인 (앱 안에서)
  await p.click('#seekEntry'); await p.waitForTimeout(400);
  ck('[지원 전] 앱 안에서 열림', await vis(p)==='obSeek');
  await p.fill('#seekForm .f-name','QA 가상편의점'); await p.fill('#seekForm .f-wage','9000'); await p.press('#seekForm .f-wage','Tab');
  await p.click('#seekRun'); await p.waitForTimeout(3000);
  ck('[지원 전] 결과와 물어볼 질문', !(await p.$eval('#seekResult',e=>e.classList.contains('hidden'))) && (await p.textContent('#seekQs')).length>5);
  ck('[지원 전] AI 질문에 테스트 답변', (await p.textContent('#seekResult')).includes(PREFIX));
  await p.click('#seekClose'); await p.waitForTimeout(400);
  // ---- 자료 ----
  await tab(p,'docs');
  await api(p,'POST','/api/auth/logout'); // 지원 전 공고는 로그인 필요 → 다시 로그인
  await api(p,'POST','/api/auth/login',{email:'qa2@example.com',password:'test1234'});
  await p.evaluate(async()=>{ const fd=new FormData(); fd.append('file',new Blob(['png'],{type:'image/png'}),'공고.png'); fd.append('kind','notice'); await fetch('/api/evidence',{method:'POST',body:fd}); });
  await p.selectOption('#evKind','message');
  await p.setInputFiles('#evFile', [{name:'메시지1.png',mimeType:'image/png',buffer:img},{name:'메시지2.png',mimeType:'image/png',buffer:img}]); await p.waitForTimeout(1500);
  ck('[자료] 여러 파일 한 번에 저장', (await toast(p)).includes('2개'), await toast(p));
  await tab(p,'home'); await tab(p,'docs');
  const ev = await p.textContent('#evList');
  ck('[자료] 올린 파일과 지원 전 공고, 계약서 사진이 목록에', ev.includes('메시지1.png') && ev.includes('지원 전') && ev.includes('근로계약서'), ev.slice(0,150));
  const evs = (await api(p,'GET',`/api/jobs/${job.id}/evidence`)).body;
  ck('[자료] 파일마다 SHA-256', evs.filter(e=>e.file).every(e=>/^[0-9a-f]{64}$/.test(e.sha256)));
  const r1 = await api(p,'POST',`/api/jobs/${job.id}/report`); const r2 = await api(p,'POST',`/api/jobs/${job.id}/report`);
  ck('[자료] 상담 사전 자료 연속 두 번 → 서로 다른 파일', r1.body.url!==r2.body.url);
  const rep = await p.evaluate(async u=>{ const r=await fetch(u); return {csp:r.headers.get('content-security-policy'), t:await r.text()}; }, r1.body.url);
  ck('[자료] 상담 자료에 AI 요약(테스트 답변)', rep.t.includes('AI 요약') && rep.t.includes(PREFIX));
  ck('[자료] 상담 자료 보안 헤더(CSP)', (rep.csp||'').includes("default-src 'none'"));
  ck('[자료] 상담 자료에 "사건" 없음', !rep.t.replace(/사건번호|사건명/g,'').includes('사건'));
  await p.screenshot({path:'p2_docs.png', fullPage:true});
  ck('[자료] 390px 넘침 없음', (await overflow(p)).length===0, await overflow(p));
  // ---- 보호 ----
  await tab(p,'guard');
  await p.click('label:has(#reported)'); await p.waitForTimeout(3000);
  ck('[보호] 신고했어요 → AI 안내 문구(테스트 답변)', (await p.inputValue('#warnMsg')).includes(PREFIX));
  await p.fill('#warnMsg','직접 고친 문구예요'); await p.waitForTimeout(1500);
  ck('[보호] 문구 직접 고치면 저장', (await api(p,'GET',`/api/jobs/${job.id}/guard`)).body.message_source==='custom');
  await p.click('#msgReset'); await p.waitForTimeout(800);
  ck('[보호] 되돌리면 AI 문구로', (await api(p,'GET',`/api/jobs/${job.id}/guard`)).body.message_source==='ai');
  await p.fill('#kwInput','QA 가상분식 사장'); await p.click('#kwAdd'); await p.waitForTimeout(800);
  ck('[보호] 검색어 등록', (await p.textContent('#kwList')).includes('QA 가상분식 사장'));
  await p.fill('#postUrl','https://example.invalid/qa/1 https://example.invalid/qa/2'); await p.click('#postAdd'); await p.waitForTimeout(8000);
  const gt = await toast(p);
  ck('[보호] 게시물 여러 개 보존, 캡처 못 하면 이유 안내', gt.includes('2개') && (gt.includes('화면 캡처') ), gt);
  ck('[보호] 게시물 판별 근거(테스트 답변)', (await p.textContent('#postList')).includes(PREFIX) || (await api(p,'GET',`/api/jobs/${job.id}/guard`)).body.posts.every(x=>x.ai_reason.startsWith(PREFIX)));
  await p.screenshot({path:'p2_guard.png', fullPage:true});
  ck('[보호] 390px 넘침 없음', (await overflow(p)).length===0, await overflow(p));
  await tab(p,'docs');
  ck('[자료] 주소만 보존한 게시물도 목록에', (await p.textContent('#evList')).includes('example.invalid/qa/1'));
  // ---- 내 정보 ----
  await p.click('#jobSwitch'); await p.waitForTimeout(300);
  ck('[선택 창] 내 정보(이메일, 생년월일) 수정 문구', (await p.textContent('#editMe')).includes('이메일'));
  await p.click('#editMe'); await p.waitForTimeout(300);
  await p.fill('#meEmail','12'); await p.click('#meSave'); await p.waitForTimeout(300);
  ck('[내 정보] 이메일 "12" 막힘', (await p.textContent('#meErr')).includes('@'));
  await p.fill('#meEmail','qa2.new@example.com'); await p.click('#meSave'); await p.waitForTimeout(600);
  ck('[내 정보] 이메일 바꾸기', (await api(p,'GET','/api/me')).body.email==='qa2.new@example.com');
  // ---- 문구 ----
  let all=''; for (const v of ['home','pay','check','docs','guard']) { await tab(p,v); all += await bodyText(p); }
  ck('[문구] 화면에 "사건" 없음', !all.includes('사건'), (all.match(/.{0,15}사건.{0,15}/)||[''])[0]);
  ck('[문구] "보복 대응" 없음', !all.includes('보복 대응'));
  ck('[문구] "다음에 할 일" 조언 표현 없음', !all.includes('다음에 할 일'));
  ck('[전체] 스크립트 오류, 500 없음', !p.errs.length && !p.bad.length, [...p.errs, ...p.bad].join(' | '));
  summary(); await b.close();
})().catch(e=>{console.error('중단', e); process.exit(1);});
