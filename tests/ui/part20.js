// 20부: 에이전트 대기줄 (서버 전체에 하나). 다른 사람의 점검이 1번에서 도는 동안 내 점검은 2번에서 기다리고,
// 진행 칸 머리에 '대기 중이에요 · 대기줄 2번째 (앞에 1개)'가 보이다가, 앞 점검이 끝나면 '에이전트가 일하는 중이에요'로 바뀐다
const { B, ck, browser, page, api, summary } = require('./lib');
(async () => {
  const b = await browser();
  const a = await page(b), me = await page(b);
  for (const [p, email] of [[a,'queue_a@example.com'],[me,'queue_b@example.com']]) {
    await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email,password:'test1234',birth_date:'2009-05-01'});
    await api(p,'POST','/api/jobs',{name:'QA 가상분식',wage:10320,start_date:'2026-08-03',schedule:{}});
  }
  // 다른 사람의 계약서 점검이 먼저 1번에서 돈다 (기다리지 않고 보냄)
  await a.evaluate(async()=>{ const j=(await (await fetch('/api/jobs')).json())[0]; fetch(`/api/jobs/${j.id}/check`,{method:'POST'}); });
  await a.waitForTimeout(800);
  await me.goto(B+'/'); await me.waitForSelector('#app:not(.hidden)');
  await me.click('#tabs [data-v="check"]'); await me.waitForTimeout(1500);
  await me.click('#checkRun');
  let waiting='', saw2=false;
  for (let i=0;i<40 && !saw2;i++){ await me.waitForTimeout(500);
    waiting = await me.evaluate(()=>document.querySelector('#checkTrace .live-head')?.textContent||'');
    saw2 = waiting.includes('대기줄 2번째'); }
  ck('[대기줄] 앞 점검이 도는 동안 내 진행 칸에 대기줄 2번째', saw2, waiting);
  await me.screenshot({path:'p20_waiting.png'});
  const ran = await me.waitForFunction(()=>(document.querySelector('#checkTrace .live-head')?.textContent||'').includes('일하는 중'),null,{timeout:120000}).then(()=>true).catch(()=>false);
  ck('[대기줄] 앞 점검이 끝나면 내 점검이 1번이 되어 실행', ran);
  const done = await me.waitForFunction(()=>!document.querySelector('#checkTrace .live'),null,{timeout:120000}).then(()=>true).catch(()=>false);
  ck('[대기줄] 내 점검도 끝나고 결과가 나옴', done && (await me.textContent('#checkItems')).length>0);
  ck('[대기줄] 페이지 오류와 5xx 없음', !me.errs.length && !me.bad.length && !a.bad.length, [...me.errs, ...me.bad, ...a.bad].join(' / '));
  await b.close(); summary();
})().catch(e=>{ console.log('중단', e.message); summary(); process.exit(1); });
