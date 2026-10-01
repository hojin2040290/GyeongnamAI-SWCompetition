// 6부: 알림 목록과 에이전트 진행 상황 화면
const { B, ck, browser, page, api, summary, overflow } = require('./lib');
const tab = async (p, v) => { await p.evaluate(v=>document.querySelector(`#tabs [data-v="${v}"]`).click(), v); await p.waitForTimeout(900); };
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'notice@example.com',password:'test1234',birth_date:'2009-05-01'});
  await api(p,'POST','/api/jobs',{name:'QA 가상분식',wage:9000,start_date:'2026-08-01',schedule:{'월':{start:'17:00',end:'22:30',brk:'없음'}}});
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(800);
  await tab(p,'check');
  const t0 = Date.now(); await p.click('#checkRun'); await p.waitForTimeout(2000);
  ck('가짜 AI 3초 대기 동안 진행 표시(도는 표시)', !!(await p.$('#checkTrace .live .wait-note')));
  await p.screenshot({path:'p6_live.png'});
  const steps = await p.$$eval('#checkTrace .live-steps .log', x=>x.length);
  await p.waitForFunction(()=>!document.querySelector('#checkTrace .live'), null, {timeout:120000});
  const took = (Date.now()-t0)/1000;
  ck('점검이 가짜 AI 대기 시간만큼 걸림 (3초 × AI 호출 수)', took >= 6, `${took.toFixed(1)}초, 진행 중 단계 ${steps}개 표시`);
  await tab(p,'home');
  const t1 = await p.$$eval('#alerts .alert strong', x=>x.map(e=>e.textContent));
  await tab(p,'check'); await p.click('#checkRun'); await p.waitForFunction(()=>!document.querySelector('#checkTrace .live'), null, {timeout:120000});
  await tab(p,'home');
  const t2 = await p.$$eval('#alerts .alert strong', x=>x.map(e=>e.textContent));
  ck('같은 점검을 두 번 해도 알림이 쌓이지 않음', t2.length===t1.length, `1번째 ${t1.length}개 [${t1.join(' / ')}] → 2번째 ${t2.length}개`);
  ck('알림 안내 문구', (await p.textContent('#alerts').catch(()=>''))!==null && (await p.evaluate(()=>document.body.innerText)).includes('예전 알림은 지우고'));
  await p.evaluate(()=>document.querySelector('#alerts').scrollIntoView()); await p.waitForTimeout(300);
  await p.screenshot({path:'p6_alerts.png'});
  ck('390px 넘침 없음', (await overflow(p)).length===0, await overflow(p));
  ck('오류 없음', !p.errs.length && !p.bad.length, [...p.errs,...p.bad].join('|'));
  summary(); await b.close();
})();
