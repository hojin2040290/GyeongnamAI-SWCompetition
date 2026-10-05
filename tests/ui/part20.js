// 20부: 에이전트 대기줄 (사용자마다 하나). 다른 사람의 점검은 내 점검을 기다리게 하지 않고,
// 내 점검이 1번에서 도는 동안 내가 누른 다른 점검은 2번에서 기다리며 진행 칸 머리에 '대기 중이에요 · 대기줄 2번째 (앞에 1개)'가
// 보이다가, 앞 점검이 끝나면 '에이전트가 일하는 중이에요'로 바뀐다
const { B, ck, browser, page, api, summary } = require('./lib');
(async () => {
  const b = await browser();
  const a = await page(b), other = await page(b), me = await page(b);
  for (const [p, email] of [[a,'queue_a@example.com'],[other,'queue_c@example.com'],[me,'queue_b@example.com']]) {
    await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email,password:'test1234',birth_date:'2009-05-01'});
    await api(p,'POST','/api/jobs',{name:'QA 가상분식',wage:10320,start_date:'2026-08-03',schedule:{}});
  }
  await api(me,'POST','/api/jobs',{name:'QA 가상카페',wage:10320,start_date:'2026-08-03',schedule:{}});
  // 1) 다른 사용자: A의 계약서 점검이 도는 동안 C의 점검도 자기 줄 1번에서 바로 돈다 (서로 기다리지 않음)
  const start = p => p.evaluate(async()=>{ const j=(await (await fetch('/api/jobs')).json())[0]; fetch(`/api/jobs/${j.id}/check`,{method:'POST'}); });
  await start(a); await a.waitForTimeout(800);
  await start(other); await other.waitForTimeout(500);
  const qa = await api(a,'GET','/api/agent/queue'), qc = await api(other,'GET','/api/agent/queue');
  ck('[대기줄] 다른 사용자의 점검은 서로 기다리지 않고 각자 1번에서 실행', qa.body.mine?.[0]?.pos===1 && qc.body.mine?.[0]?.pos===1 && qc.body.size===1, JSON.stringify([qa,qc]));
  // 2) 같은 사용자: 내 다른 사업장의 계약서 점검이 먼저 1번에서 돈다 (기다리지 않고 보냄)
  await me.goto(B+'/'); await me.waitForSelector('#app:not(.hidden)');
  await me.click('#tabs [data-v="check"]'); await me.waitForTimeout(1500);
  await me.evaluate(async()=>{ const j=(await (await fetch('/api/jobs')).json()).find(x=>x.id!==state.current); fetch(`/api/jobs/${j.id}/check`,{method:'POST'}); });
  await me.waitForTimeout(800);
  await me.click('#checkRun');
  let waiting='', saw2=false;
  for (let i=0;i<40 && !saw2;i++){ await me.waitForTimeout(500);
    waiting = await me.evaluate(()=>document.querySelector('#checkTrace .live-head')?.textContent||'');
    saw2 = /대기줄 [2-9]번째/.test(waiting); }
  ck('[대기줄] 내 앞 점검이 도는 동안 내 진행 칸에 대기줄 번호', saw2, waiting);
  await me.screenshot({path:'p20_waiting.png'});
  const ran = await me.waitForFunction(()=>(document.querySelector('#checkTrace .live-head')?.textContent||'').includes('일하는 중'),null,{timeout:120000}).then(()=>true).catch(()=>false);
  ck('[대기줄] 앞 점검이 끝나면 내 점검이 1번이 되어 실행', ran);
  const done = await me.waitForFunction(()=>!document.querySelector('#checkTrace .live'),null,{timeout:120000}).then(()=>true).catch(()=>false);
  ck('[대기줄] 내 점검도 끝나고 결과가 나옴', done && (await me.textContent('#checkItems')).length>0);
  ck('[대기줄] 페이지 오류와 5xx 없음', !me.errs.length && !me.bad.length && !a.bad.length && !other.bad.length, [...me.errs, ...me.bad, ...a.bad, ...other.bad].join(' / '));
  await b.close(); summary();
})().catch(e=>{ console.log('중단', e.message); summary(); process.exit(1); });
