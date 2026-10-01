// 1부: 가입, 로그인, 처음 설정, 사업장 등록, 근무 시간 입력
const { B, ck, browser, page, vis, toast, api, overflow, summary } = require('./lib');
(async () => {
  const b = await browser();
  // ---- 로그아웃 상태: 세 상황 모두 1단계 ----
  for (const m of ['seek','work','quit']) {
    const p = await page(b); await p.goto(B+'/'); await p.waitForTimeout(400);
    ck(`[처음] ${m}: 처음 화면`, await vis(p)==='ob0');
    ck(`[처음] ${m}: 고르기 전 다음 버튼 막힘`, await p.$eval('#modeNext',e=>e.disabled));
    await p.click(`.mode[data-mode="${m}"]`); await p.click('#modeNext'); await p.waitForTimeout(200);
    ck(`[처음] ${m}: 다음 → 1단계`, await vis(p)==='ob1' && (await p.textContent('#ob1 .step')).includes('1 / 2'));
    await p.click('#ob1 .ob-back'); await p.waitForTimeout(200); ck(`[처음] ${m}: 1단계 뒤로 → 처음 화면`, await vis(p)==='ob0');
    await p.context().close();
  }
  // ---- 이메일 검사 (가입) ----
  const p = await page(b); await p.goto(B+'/'); await p.waitForTimeout(300);
  await p.click('.mode[data-mode="work"]'); await p.click('#modeNext');
  for (const [e, want] of [['12','@가 없어요'],['abc@','형식'],['a@b','형식'],['a@@b.com','형식'],['가나@ex.com','형식'],['a b@ex.com','형식']]) {
    await p.fill('#regEmail', e); await p.fill('#regPw','test1234'); await p.fill('#birth','2009-05-01'); await p.click('#regBtn'); await p.waitForTimeout(250);
    ck(`[가입] 이메일 "${e}" 막힘`, await vis(p)==='ob1' && (await p.textContent('#regErr')).includes(want), await p.textContent('#regErr'));
  }
  await p.fill('#regEmail',''); await p.click('#regBtn'); ck('[가입] 빈 칸 안내', (await p.textContent('#regErr')).includes('모두 입력'));
  ck('[가입] 서버도 "12" 거절', (await api(p,'POST','/api/auth/register',{email:'12',password:'1234',birth_date:'2009-05-01'})).status===400);
  ck('[가입] 서버: 비밀번호 4자 미만 거절', (await api(p,'POST','/api/auth/register',{email:'short@example.com',password:'12',birth_date:'2009-05-01'})).status===400);
  await p.fill('#regEmail',' QA.User@Example.com '); await p.fill('#regPw','test1234'); await p.fill('#birth','2009-05-01'); await p.click('#regBtn'); await p.waitForTimeout(600);
  ck('[가입] 올바른 이메일 → 2단계', await vis(p)==='ob2' && (await p.textContent('#ob2Step')).includes('2 / 2'));
  ck('[가입] 이메일 소문자로 저장', (await api(p,'GET','/api/me')).body.email==='qa.user@example.com');
  ck('[가입] 중복 가입 거절(대소문자 달라도)', (await api(p,'POST','/api/auth/register',{email:'QA.USER@example.com',password:'test1234',birth_date:'2009-05-01'})).status===400);
  // ---- 2단계 뒤로 → 1단계 (로그인 상태: 채워져 있음) ----
  await p.click('#ob2 .ob-back'); await p.waitForTimeout(300);
  ck('[처음] 2단계 뒤로 → 1단계', await vis(p)==='ob1');
  ck('[처음] 로그인 상태 1단계: 이메일 채움, 비밀번호 칸 숨김', (await p.inputValue('#regEmail'))==='qa.user@example.com' && await p.$eval('#regPwWrap',e=>e.classList.contains('hidden')));
  await p.fill('#regEmail','12'); await p.click('#regBtn'); await p.waitForTimeout(200);
  ck('[처음] 로그인 상태에서도 "12" 막힘', await vis(p)==='ob1' && (await p.textContent('#regErr')).includes('@'));
  await p.fill('#regEmail','qa.user@example.com'); await p.click('#regBtn'); await p.waitForTimeout(400);
  ck('[처음] 로그인 상태 다음 → 2단계', await vis(p)==='ob2');
  // ---- 사업장 등록 입력 검사 ----
  const card = '#jobList .job';
  ck('[등록] 월급날 선택지 모름/1~30일/말일', await p.$$eval(`${card} .f-payday option`, os=>os.length===32 && os[0].textContent==='모름' && os[30].textContent==='30일' && os[31].textContent.startsWith('말일')));
  await p.fill(`${card} .f-name`, '가상분식<b>'); await p.waitForTimeout(100);
  ck('[등록] 사업장 이름에서 꺾쇠 빠짐', !(await p.inputValue(`${card} .f-name`)).includes('<'), await p.inputValue(`${card} .f-name`));
  await p.fill(`${card} .f-name`, 'QA 가상분식');
  await p.fill(`${card} .f-wage`, ''); await p.type(`${card} .f-wage`, '1만30원'); await p.press(`${card} .f-wage`,'Tab'); await p.waitForTimeout(150);
  ck('[등록] 시급 "1만30원" → 10030', await p.inputValue(`${card} .f-wage`)==='10030', await p.inputValue(`${card} .f-wage`));
  await p.fill(`${card} .f-wage`, '2000000'); await p.press(`${card} .f-wage`,'Tab'); await p.waitForTimeout(150);
  ck('[등록] 시급 범위 밖(200만) 지움', await p.inputValue(`${card} .f-wage`)==='', await toast(p));
  await p.type(`${card} .f-wage`, 'abc12'); ck('[등록] 시급에 글자 입력 막힘', await p.inputValue(`${card} .f-wage`)==='12');
  await p.fill(`${card} .f-wage`, '10320'); await p.press(`${card} .f-wage`,'Tab');
  // 날짜
  const fut = new Date(Date.now()+5*86400000).toISOString().slice(0,10);
  await p.fill(`${card} .f-start`, fut); await p.click('#saveJobsBtn'); await p.waitForTimeout(300);
  ck('[등록] 근무 시작일 미래 막힘', (await p.textContent('#jobErr')).includes('시작일'), await p.textContent('#jobErr'));
  ck('[등록] 서버도 미래 시작일 거절', (await api(p,'POST','/api/jobs',{name:'x',start_date:fut})).status===400);
  await p.fill(`${card} .f-start`, '2026-08-01'); await p.fill(`${card} .f-end`, '2026-07-01'); await p.click('#saveJobsBtn'); await p.waitForTimeout(300);
  ck('[등록] 종료일이 시작일보다 앞 막힘', (await p.textContent('#jobErr')).includes('종료일'), await p.textContent('#jobErr'));
  await p.fill(`${card} .f-end`, '');
  await p.selectOption(`${card} .f-payday`, '31'); await p.click(`${card} .seg[data-name="paytype"] [data-v="월급"]`);
  // 근무 시간 시트
  await p.click(`${card} .schedule-btn`); await p.waitForTimeout(300);
  await p.click('#dayQuick [data-days="월화수목금"]').catch(()=>{}); await p.waitForTimeout(200);
  const days = await p.$$eval('#dayPicker [aria-pressed="true"]', x=>x.map(b=>b.textContent).join(''));
  ck('[근무 시간] 평일 빠른 선택', days==='월화수목금', days);
  await p.click('#dayQuick [data-days=""]').catch(()=>{}); await p.waitForTimeout(150);
  await p.click('#dayPicker button:has-text("월")'); await p.click('#dayPicker button:has-text("토")'); await p.waitForTimeout(150);
  // 월요일 시간대 두 개, 직접 입력 쉬는 시간 19분
  await p.click('#slotList .slot >> nth=0 >> .add-part'); await p.waitForTimeout(150);
  ck('[근무 시간] 한 요일에 시간대 더하기', await p.$$eval('#slotList .slot >> nth=0', x=>0).catch(()=>0)===0 && (await p.$$('#slotList .slot:first-child .slot-part')).length===2);
  await p.selectOption('#slotList .slot:first-child .slot-part >> nth=0 >> .brk select', '직접 입력');
  await p.fill('#slotList .slot:first-child .slot-part >> nth=0 >> .brk-custom input >> nth=1', '19'); await p.press('#slotList .slot:first-child .slot-part >> nth=0 >> .brk-custom input >> nth=1','Tab');
  await p.fill('#slotList .slot:first-child .slot-part >> nth=0 >> .brk-custom input >> nth=0', '9'); await p.press('#slotList .slot:first-child .slot-part >> nth=0 >> .brk-custom input >> nth=0','Tab'); await p.waitForTimeout(200);
  ck('[근무 시간] 일하는 시간보다 긴 쉬는 시간 막힘', (await toast(p)).includes('보다 길어요'), await toast(p));
  await p.fill('#slotList .slot:first-child .slot-part >> nth=0 >> .brk-custom input >> nth=0', ''); await p.fill('#slotList .slot:first-child .slot-part >> nth=0 >> .brk-custom input >> nth=1', '19'); await p.press('#slotList .slot:first-child .slot-part >> nth=0 >> .brk-custom input >> nth=1','Tab');
  await p.selectOption('#slotList .slot:nth-child(2) .brk select', '3시간').catch(async()=>{});
  ck('[근무 시간] 빠른 선택지 15분, 45분 있음', await p.$$eval('#slotList .brk select >> nth=0', ()=>1).catch(()=>1) && (await p.$eval('#slotList .brk select', s=>[...s.options].map(o=>o.value).join(','))).includes('15분,30분,45분'));
  await p.screenshot({path:'p1_sheet.png'});
  ck('[근무 시간] 390px 넘침 없음', (await overflow(p)).length===0, await overflow(p));
  await p.click('#sheetSave'); await p.waitForTimeout(200);
  // 여러 사업장
  await p.click('#addJobBtn'); await p.waitForTimeout(200);
  ck('[등록] + 아르바이트 추가로 카드 2개', (await p.$$('#jobList .job')).length===2);
  const c2 = '#jobList .job >> nth=1';
  await p.click(`${c2} >> .seg[data-name="status"] [data-v="quit"]`); await p.fill(`${c2} >> .f-name`, 'QA 가상카페'); await p.fill(`${c2} >> .f-start`, '2026-06-01');
  await p.fill(`${c2} >> .f-quit`, fut);
  await p.screenshot({path:'p1_ob2.png', fullPage:true});
  ck('[등록] 390px 넘침 없음', (await overflow(p)).length===0, await overflow(p));
  await p.click('#saveJobsBtn'); await p.waitForTimeout(1200);
  ck('[등록] 그만둘 날(미래) 허용하고 시작', await vis(p)==='app', await p.textContent('#jobErr'));
  const jobs = (await api(p,'GET','/api/jobs')).body;
  const j1 = jobs.find(j=>j.name==='QA 가상분식');
  ck('[등록] 두 사업장 저장', jobs.length===2, jobs.map(j=>j.name));
  ck('[등록] 월급날 말일=31 저장', j1 && j1.payday===31);
  ck('[등록] 쉬는 시간 19분 저장, 월요일 시간대 2개', j1 && Array.isArray(j1.schedule['월']) && j1.schedule['월'][0].brk==='19분', JSON.stringify(j1&&j1.schedule));
  ck('[등록] 서버: 시간대 5개 거절', (await api(p,'PUT',`/api/jobs/${j1.id}`,{...j1, schedule:{'월':Array(5).fill({start:'09:00',end:'10:00',brk:'없음'})}})).status===400);
  ck('[등록] 서버: 월급날 1300 거절', (await api(p,'PUT',`/api/jobs/${j1.id}`,{...j1, payday:1300})).status===400);
  // ---- 로그인 ----
  await api(p,'POST','/api/auth/logout'); await p.goto(B+'/'); await p.waitForTimeout(400);
  await p.click('#toLogin'); await p.fill('#logEmail','12'); await p.fill('#logPw','x'); await p.click('#logBtn'); await p.waitForTimeout(200);
  ck('[로그인] "12" 막힘', (await p.textContent('#logErr')).includes('@'));
  await p.fill('#logEmail','QA.USER@EXAMPLE.COM'); await p.fill('#logPw','test1234'); await p.click('#logBtn'); await p.waitForTimeout(800);
  ck('[로그인] 대문자로 적어도 로그인', await vis(p)==='app');
  // 로그인 시도 제한
  await api(p,'POST','/api/auth/logout');
  let last; for(let i=0;i<5;i++) last=await api(p,'POST','/api/auth/login',{email:'qa.user@example.com',password:'wrong'});
  const locked = await api(p,'POST','/api/auth/login',{email:'qa.user@example.com',password:'test1234'});
  ck('[로그인] 5번 틀리면 맞아도 막힘', locked.status===429, locked.body&&locked.body.detail);
  ck('[로그인] 남은 횟수 안내', (last.body.detail||'').includes('로그인할 수 없어요'), last.body.detail);
  console.log('화면 오류:', p.errs, '500 응답:', p.bad);
  ck('[전체] 스크립트 오류, 500 없음', !p.errs.length && !p.bad.length);
  summary(); await b.close();
})().catch(e=>{console.error('중단', e); process.exit(1);});
