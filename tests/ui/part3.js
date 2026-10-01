// 3부: AI 없음(LLM_FAKE=false) — 대기 표시, 보안
const { B, ck, browser, page, vis, toast, api, overflow, summary } = require('./lib');
const tab = async (p, v) => { await p.evaluate(v=>document.querySelector(`#tabs [data-v="${v}"]`).click(), v); await p.waitForTimeout(900); };
// '대기 중', '판단 대기', '판단 중' 글자가 있는 요소마다 도는 표시(자기나 조상에 대기 클래스)가 있는지
const waitsWithoutSpinner = p => p.evaluate(()=>{
  const out=[]; const w=document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while(w.nextNode()){ const n=w.currentNode, el=n.parentElement; if(!el||!el.offsetParent) continue;
    if(!/응답 대기 중|판단 대기|판단 중/.test(n.textContent)) continue;
    if(el.closest('.wait-note,.ai-note.wait,.tag.pending,.live,details.trace,.log,#toast,.hint')) continue;
    out.push(n.textContent.trim().slice(0,60)); }
  return out; });
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await p.waitForTimeout(300);
  await api(p,'POST','/api/auth/register',{email:'qa3@example.com',password:'test1234',birth_date:'2009-05-01'});
  ck('[AI 없음] 상태: 가짜 AI 꺼짐', !(await api(p,'GET','/api/ai/status')).body.fake);
  const job = (await api(p,'POST','/api/jobs',{name:'QA 가상분식<img src=x onerror=alert(1)>',wage:9000,payday:10,start_date:'2026-08-01',
    schedule:{'월':{start:'17:00',end:'22:30',brk:'없음'}}})).body;
  ck('[보안] 사업장 이름 꺾쇠 거절(서버)', !job.id, JSON.stringify(job).slice(0,100));
  const j = (await api(p,'POST','/api/jobs',{name:'QA 가상분식',wage:9000,payday:10,start_date:'2026-08-01',status:'working',
    schedule:{'월':{start:'17:00',end:'22:30',brk:'없음'}}})).body;
  await api(p,'PUT',`/api/jobs/${j.id}/contract/fields`,{fields:{'임금':'시급 9000원'}});
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(800);
  ck('[AI 없음] 홈 조언: 도는 대기 표시', !!(await p.$('#caseAdvice .ai-note.wait')), await p.textContent('#caseAdvice'));
  await tab(p,'check'); await p.click('#checkRun'); await p.waitForTimeout(2500);
  ck('[AI 없음] 계약서 점검: 판단은 대기, 사실은 정리', (await p.textContent('#checkItems')).includes('AI 응답 대기 중'));
  let bad = await waitsWithoutSpinner(p); ck('[AI 없음] 계약서 탭: 대기 문구마다 도는 표시', !bad.length, bad.join(' / '));
  await p.screenshot({path:'p3_check_wait.png'});
  await tab(p,'pay'); await p.fill('#payMonth','2026-09').catch(()=>{}); await p.type('#payAmount','30만원'); await p.press('#payAmount','Tab'); await p.click('#paySave'); await p.waitForTimeout(2000);
  bad = await waitsWithoutSpinner(p); ck('[AI 없음] 급여 탭: 대기 문구마다 도는 표시', !bad.length, bad.join(' / '));
  console.log('  급여 결과:', (await p.textContent('#payBody')).replace(/\s+/g,' ').slice(0,160));
  await p.screenshot({path:'p3_pay_wait.png', fullPage:true});
  await tab(p,'docs'); await p.click('#reportBtn');
  let tmsg=''; for(let i=0;i<60 && !tmsg.includes('AI 요약 없이');i++){ await p.waitForTimeout(50); tmsg=await p.$eval('#toast',e=>e.textContent).catch(()=>tmsg); }
  ck('[AI 없음] 상담 자료 알림 문구', tmsg.includes('AI 요약 없이'), tmsg);
  await p.waitForURL(/\/api\/reports\//,{timeout:15000}).catch(()=>{});
  ck('[AI 없음] 상담 자료 문서의 대기 문구에 도는 표시', /AI 응답 대기 중/.test(await p.evaluate(()=>document.body.innerText)) && !!(await p.$('.wait')));
  await p.screenshot({path:'p3_toast.png'}); await p.goBack(); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(800);
  const reps = (await api(p,'GET',`/api/jobs/${j.id}/reports`)).body;
  const doc = await p.evaluate(async u=>await (await fetch(u)).text(), reps[0].url);
  ck('[AI 없음] 상담 자료 문서: 대기 문구에 도는 표시', doc.includes("class='w wait'") && doc.includes('@keyframes spin'));
  ck('[AI 없음] 상담 자료 문서: "사건" 없음, "AI 요약"', !doc.replace(/사건번호|사건명/g,'').includes('사건') && doc.includes('AI 요약'));
  const rp = await b.newPage({viewport:{width:390,height:844}}); await rp.context().addCookies(await p.context().cookies());
  await rp.goto(B+reps[0].url); await rp.waitForTimeout(400); await rp.screenshot({path:'p3_report_wait.png'});
  await tab(p,'guard'); await p.click('label:has(#reported)'); await p.waitForTimeout(2000);
  ck('[AI 없음] 보호: 기본 안내 문구 + 대기 표시', (await p.inputValue('#warnMsg')).length>20 && !!(await p.$('#msgSource.wait')), await p.textContent('#msgSource'));
  await p.fill('#postUrl','https://example.invalid/w/1'); await p.click('#postAdd'); await p.waitForTimeout(4000);
  bad = await waitsWithoutSpinner(p); ck('[AI 없음] 보호 탭: 대기 문구마다 도는 표시', !bad.length, bad.join(' / '));
  ck('[AI 없음] 게시물: AI 응답 대기 중 태그(도는 표시)', !!(await p.$('#postList .tag.pending')));
  await p.screenshot({path:'p3_guard_wait.png', fullPage:true});
  await tab(p,'home'); await p.screenshot({path:'p3_home_wait.png', fullPage:true}); bad = await waitsWithoutSpinner(p); ck('[AI 없음] 홈: 대기 문구마다 도는 표시', !bad.length, bad.join(' / '));
  // ---- 보안 ----
  const h = await p.evaluate(async()=>{ const r=await fetch('/'); return [r.headers.get('x-content-type-options'), r.headers.get('x-frame-options')]; });
  ck('[보안] nosniff, 프레임 금지 헤더', h[0]==='nosniff' && h[1]==='DENY', h);
  ck('[보안] 시연용 주소는 DEV_TOOLS=false면 막힘', (await api(p,'POST','/api/dev/daily-check')).status===404);
  const p2 = await page(b); await p2.goto(B+'/'); await api(p2,'POST','/api/auth/register',{email:'other3@example.com',password:'test1234',birth_date:'2009-05-01'});
  ck('[보안] 다른 사용자 사업장 조회 불가', (await api(p2,'GET',`/api/jobs/${j.id}/records`)).status===404);
  ck('[보안] 다른 사용자 증거 파일 불가', (await api(p2,'GET',`/api/evidence/1/file`)).status===404);
  ck('[보안] 다른 사용자 상담 자료 불가', (await api(p2,'GET',reps[0].url)).status===404);
  await api(p2,'POST','/api/auth/logout'); ck('[보안] 로그인 없이 API 불가', (await api(p2,'GET','/api/jobs')).status===401);
  const e500 = await api(p,'POST',`/api/jobs/${j.id}/punch`,{lat:'없는값'});
  ck('[오류] 틀린 값은 한국어 오류', e500.status<500 && /[가-힣]/.test(JSON.stringify(e500.body)), JSON.stringify(e500.body));
  ck('[전체] 스크립트 오류, 500 없음', !p.errs.length && !p.bad.length, [...p.errs, ...p.bad].join(' | '));
  summary(); await b.close();
})().catch(e=>{console.error('중단', e); process.exit(1);});
