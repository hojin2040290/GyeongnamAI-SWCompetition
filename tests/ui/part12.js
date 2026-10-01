// 12부: AI 에이전트 판단 표시 — 판단하는 동안 'AI 에이전트가 판단 중', 항목마다 AI 판단 칸,
// 다시 열어도 마지막 '에이전트 동작 보기', 에이전트 동작 기록은 실행마다 묶고 AI 판단 결과를 보여 줌 (가짜 AI 3초 대기)
const { B, ck, browser, page, api, overflow, summary } = require('./lib');
const PREFIX = '테스트 답변입니다';
const JUDGING = 'AI 에이전트가 판단 중';
const tab = async (p, v) => { await p.evaluate(v=>document.querySelector(`#tabs [data-v="${v}"]`).click(), v); await p.waitForTimeout(900); };
const until = async (p, fn, arg, ms=90000) => p.waitForFunction(fn, arg, {timeout:ms}).then(()=>true).catch(()=>false);
// 진행 칸이 나타난 뒤 사라질 때까지 (에이전트가 끝날 때까지)
const finish = async (p, box) => { await until(p, b=>!!document.querySelector(b+' .live'), box, 10000); await until(p, b=>!document.querySelector(b+' .live'), box); await p.waitForTimeout(300); };
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'judge@example.com',password:'test1234',birth_date:'2009-05-01'});
  const job=(await api(p,'POST','/api/jobs',{name:'QA 가상판단',wage:12000,start_date:'2026-08-01',schedule:{'월':{start:'16:00',end:'22:00',brk:'없음'}}})).body;
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(500);

  // 계약서: 첫 점검
  await tab(p,'check'); await p.click('#checkRun'); await finish(p,'#checkTrace');
  const items = await p.$$eval('#checkItems .result', rs=>rs.map(r=>({ai:!!r.querySelector('.ai-judge'), text:r.querySelector('.ai-judge')?.textContent||''})));
  ck('[계약서] 항목마다 AI 에이전트 판단 칸', items.length>0 && items.every(x=>x.ai && x.text.includes('AI 에이전트 판단')), `${items.length}개`);
  ck('[계약서] AI 판단 칸에 이유와 근거 (테스트 답변)', items.every(x=>x.text.includes(PREFIX) && x.text.includes('근거 조항')));
  await p.screenshot({path:'p12_items.png'});

  // 다시 점검: 판단하는 동안 'AI 에이전트가 판단 중'
  await p.click('#checkRun'); await until(p, ()=>!!document.querySelector('#checkTrace .live'), null, 10000);
  const mid = await p.$$eval('#checkItems .result .head .tag', ts=>ts.map(t=>[t.textContent, t.classList.contains('pending')]));
  ck('[계약서] 판단하는 동안 모든 항목이 판단 중 (확인 필요가 아님)', mid.length>0 && mid.every(([t,pd])=>t==='AI 에이전트가 판단 중' && pd), JSON.stringify(mid.slice(0,2)));
  ck('[계약서] 판단하는 동안 요약도 판단 중(도는 표시)', !!(await p.$('#checkSummary .pending .wait-note')));
  await p.evaluate(()=>document.querySelector('#checkItems').scrollIntoView()); await p.screenshot({path:'p12_judging.png'});
  await finish(p,'#checkTrace');
  ck('[계약서] 끝나면 판단 결과로 바뀜', !(await p.textContent('#checkItems')).includes(JUDGING));
  ck('[계약서] 에이전트 동작 보기에 시각과 AI 판단 결과', /에이전트 동작 보기 \(\d+단계, .*\d{2}:\d{2}:\d{2}/.test(await p.textContent('#checkTrace')) && (await p.textContent('#checkTrace')).includes('AI 판단 결과'));

  // 새로고침 뒤에도 마지막 에이전트 동작 보기
  await p.reload(); await p.waitForSelector('#app:not(.hidden)'); await tab(p,'check'); await p.waitForTimeout(800);
  ck('[계약서] 다시 열어도 에이전트 동작 보기가 남음', (await p.textContent('#checkTrace')).includes('에이전트 동작 보기'));
  // 에이전트 동작 기록: 실행마다 묶음, AI 판단 결과
  await p.evaluate(()=>{ const d=document.querySelector('#agentLog').closest('details'); d.open=true; const r=document.querySelector('#agentLog details.run'); if(r) r.open=true; document.querySelector('#agentLog').scrollIntoView(); });
  await p.waitForTimeout(400);
  const runs = await p.$$eval('#agentLog details.run', d=>d.length);
  ck('[계약서] 에이전트 동작 기록이 실행마다 묶임', runs>=2, `${runs}번`);
  ck('[계약서] 동작 기록에 항목별 AI 판단 결과', (await p.$$eval('#agentLog details.run[open] .log-ai', x=>x.map(e=>e.textContent))).some(t=>t.includes('AI 판단 결과') && t.includes('근로기준법')));
  await p.screenshot({path:'p12_log.png'});
  ck('[계약서] 390px 넘침 없음', (await overflow(p)).length===0, await overflow(p));

  // 급여: 저장 → 판단 중 → AI 판단 칸, 다시 열어도 동작 보기
  await tab(p,'pay'); await p.fill('#payMonth','2026-09'); await p.dispatchEvent('#payMonth','change'); await p.waitForTimeout(600);
  await p.fill('#payAmount','300000'); await p.click('#paySave'); await until(p, ()=>!!document.querySelector('#payTrace .live'), null, 10000);
  ck('[급여] 판단하는 동안 비교 결과가 판단 중', (await p.textContent('#payBody .result .head .tag')).includes('판단 중'));
  await finish(p,'#payTrace');
  ck('[급여] 비교 결과에 AI 에이전트 판단 칸', ((await p.textContent('#payBody .ai-judge'))||'').includes(PREFIX));
  await p.screenshot({path:'p12_pay.png'});
  await tab(p,'home'); await tab(p,'pay'); await p.waitForTimeout(600);
  ck('[급여] 다시 열어도 그 달의 에이전트 동작 보기', (await p.textContent('#payTrace')).includes('에이전트 동작 보기'));

  // 퇴직 정산: 그만둔 날 저장 → AI 판단 칸
  await tab(p,'home'); await p.click('#quitOpen'); await p.fill('#quitDateMain', '2026-09-20'); await p.click('#quitSave'); await until(p, ()=>!!document.querySelector('#quitLive .live'), null, 10000);
  ck('[퇴직 정산] 판단하는 동안 판단 중', (await p.textContent('#quitTag')).includes('판단 중'));
  await finish(p,'#quitLive');
  ck('[퇴직 정산] AI 에이전트 판단 칸', ((await p.textContent('#quitAi'))||'').includes(PREFIX));
  await p.evaluate(()=>document.querySelector('#quitPanel').scrollIntoView()); await p.screenshot({path:'p12_quit.png'});
  await p.reload(); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(1000);
  ck('[퇴직 정산] 다시 열어도 에이전트 동작 보기', (await p.textContent('#quitLive')).includes('에이전트 동작 보기'));

  // 상담 사전 자료에도 항목별 AI 에이전트 판단
  await tab(p,'docs'); await p.click('#reportBtn');
  await p.waitForURL(/\/api\/reports\//, {timeout:90000}).catch(()=>{});
  ck('[상담 자료] 항목마다 AI 에이전트 판단', (await p.evaluate(()=>document.body.innerText)).includes('AI 에이전트 판단'));
  await p.screenshot({path:'p12_report.png'});
  ck('오류 없음', !p.errs.length && !p.bad.length, [...p.errs,...p.bad].join('|'));
  summary(); await b.close();
})().catch(e=>{console.error('중단',e);process.exit(1);});
