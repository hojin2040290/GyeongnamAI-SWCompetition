// 12부: AI 에이전트 판단 표시 — 판단하는 동안 'AI 에이전트가 판단 중', 항목마다 AI 판단 칸,
// 다시 열어도 마지막 '에이전트 동작 보기', 에이전트 동작 기록은 실행마다 묶고 AI 판단 결과를 보여 줌 (가짜 AI 3초 대기)
const { execSync } = require('child_process');
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
  // 이 달은 근무 기록이 없어 비교할 수 없다: AI에게 체불 판단을 받지 않고 코드가 확인 필요로 둔다 (근무 기록이 있는 달은 part13)
  const pj = (await p.textContent('#payBody .ai-judge'))||'';
  ck('[급여] 근무 기록이 없는 달은 체불을 판단하지 않음', pj.includes('AI 판단 없이') && !(await p.textContent('#payBody')).includes('체불'), pj.slice(0,60));
  await p.screenshot({path:'p12_pay.png'});
  // 예전 코드가 이 달에 저장해 둔 체불 판단이 있어도 다시 쓰지 않음 (근무 기록이 없으면 AI 판단을 받지 않는 지금 규칙)
  execSync(`python3 -c "import sqlite3,json; c=sqlite3.connect('${process.env.DB}'); r=c.execute(\\"select id,results_json from checkrun where kind='payday' order by id desc limit 1\\").fetchone(); d=json.loads(r[1]); d['compare'].update(ai_reason='예전 판단: 체불이 의심됩니다', ai_law='근로기준법 제55조', ai_fact='계산 0원'); c.execute('insert into checkrun (user_id,job_id,kind,results_json,created_at) select user_id,job_id,kind,?,created_at from checkrun where id=?',(json.dumps(d,ensure_ascii=False),r[0])); c.commit()"`);
  await tab(p,'home'); await tab(p,'pay'); await p.waitForTimeout(600);
  ck('[급여] 예전에 저장된 체불 판단을 다시 쓰지 않음', !(await p.textContent('#payBody')).includes('체불') && ((await p.textContent('#payBody .ai-judge'))||'').includes('AI 판단 없이'));
  await p.screenshot({path:'p12_pay_old.png'});
  await tab(p,'home'); await tab(p,'pay'); await p.waitForTimeout(600);
  ck('[급여] 다시 열어도 그 달의 에이전트 동작 보기', (await p.textContent('#payTrace')).includes('에이전트 동작 보기'));

  // 퇴근: 점검하는 동안 '찾는 중' (예전 결과 '찾지 못했어요'가 남지 않게), 두 번 퇴근해도 마찬가지
  for(const n of [1,2]){
    await tab(p,'home'); await p.click('#punchBtn'); await p.waitForTimeout(800);  // 출근
    await p.click('#punchBtn'); await until(p, ()=>!!document.querySelector('#punchLive .live'), null, 10000);  // 퇴근 (바로 퇴근 확인은 자동 수락)
    const mid=await p.textContent('#shiftResult').catch(()=>'' );
    ck(`[퇴근 ${n}] 점검하는 동안 '찾는 중'`, mid.includes('찾는 중이에요') && !mid.includes('찾지 못했어요'), mid.replace(/\s+/g,' ').slice(0,60));
    if(n===1) await p.screenshot({path:'p12_shift_wait.png'});
    await finish(p,'#punchLive');
    ck(`[퇴근 ${n}] 끝나면 결과로 바뀜`, !(await p.textContent('#shiftResult')).includes('찾는 중'));
  }

  // 퇴직 정산: 그만둔 날 저장 → AI 판단 칸
  await tab(p,'home'); await p.click('#quitOpen'); await p.fill('#quitDateMain', '2026-09-20'); await p.click('#quitSave'); await until(p, ()=>!!document.querySelector('#quitFormLive .live'), null, 10000);
  ck('[퇴직 정산] 판단하는 동안 판단 중', (await p.textContent('#quitTag')).includes('판단 중'));
  await finish(p,'#quitFormLive'); await p.waitForTimeout(1500);
  const quitRuns = await p.evaluate(async()=>(await (await fetch(`/api/jobs/${state.current}/agent/runs?events=quit_check&limit=10`)).json()).length);
  ck('[퇴직 정산] 그만둔 날 저장은 점검을 한 번만 (자동 재점검과 겹치지 않음)', quitRuns===1 && !(await p.$('#quitLive .live')), `점검 ${quitRuns}번`);
  ck('[퇴직 정산] AI 에이전트 판단 칸', ((await p.textContent('#quitAi'))||'').includes(PREFIX));
  await p.evaluate(()=>document.querySelector('#quitPanel').scrollIntoView()); await p.screenshot({path:'p12_quit.png'});
  await p.reload(); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(1000);
  ck('[퇴직 정산] 종합 점검 카드 안의 한 줄 (카드가 따로 없음)', !!(await p.$('#ovPanel #quitPanel:not(.hidden)')));
  ck('[퇴직 정산] 종합 점검 사실 목록에 퇴직 정산 줄이 두 번 나오지 않음', !(await p.textContent('#ovParts')).includes('퇴직 후 임금 정산'));
  ck('[퇴직 정산] AI 판단과 동작 보기는 종합 점검 것 하나 (퇴직 정산 판단은 펼칠 때만)',
     !(await p.isVisible('#quitAi')) && !(await p.textContent('#quitLive')).includes('에이전트 동작 보기') && (await p.$$('#ovPanel #ovAi .ai-judge')).length<=1);
  // 남은 임금 받았어요: 다시 판단하고, 받았다는 기록이 위반 의심으로 바뀌지 않음
  // 받았는지 묻는 퇴직 정산 질문이 열려 있는 상태 (예전 코드가 만든 질문)
  execSync(`python3 -c "import sqlite3; c=sqlite3.connect('${process.env.DB}'); c.execute(\\"insert into agentquestion (user_id,job_id,event,run_id,question,options_json,why,law,answer,status,context_json,created_at) select user_id,id,'quit_check','','테스트 답변입니다 (임금을 받았나요)','[]','테스트 답변입니다 (기한 확인)','','','open','{}',datetime('now') from job where name='QA 가상판단'\\"); c.commit()"`);
  await p.reload(); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(1000);
  ck('[퇴직 정산] 받았는지 묻는 질문 카드가 보임', await p.isVisible('#askCard') && (await p.textContent('#askList')).includes('임금을 받았나요'));
  await p.click('#paidYes'); await finish(p,'#quitLive');
  ck('[퇴직 정산] 받았어요를 누르면 그 질문이 닫히고 다시 생기지 않음', !(await p.textContent('#askList')).includes('임금을 받았나요') && !(await p.$$('#askList .ask')).length);
  ck('[퇴직 정산] 받았어요 뒤 위반 의심이 아님', !(await p.textContent('#quitTag')).includes('위반') && (await p.textContent('#quitText')).includes('받았다고'),
     await p.textContent('#quitTag'));
  ck('[퇴직 정산] 받았어요 뒤 AI가 받은 기록을 봄', ((await p.textContent('#quitAi'))||'').includes('받았다고 기록했어요'));
  await p.evaluate(()=>document.querySelector('#quitPanel').scrollIntoView()); await p.screenshot({path:'p12_paid.png'});

  // 상담 사전 자료에도 항목별 AI 에이전트 판단
  await tab(p,'docs'); await p.click('#reportBtn');
  await p.waitForURL(/\/api\/reports\//, {timeout:90000}).catch(()=>{});
  ck('[상담 자료] 항목마다 AI 에이전트 판단', (await p.evaluate(()=>document.body.innerText)).includes('AI 에이전트 판단'));
  await p.screenshot({path:'p12_report.png'});
  ck('오류 없음', !p.errs.length && !p.bad.length, [...p.errs,...p.bad].join('|'));
  summary(); await b.close();
})().catch(e=>{console.error('중단',e);process.exit(1);});
