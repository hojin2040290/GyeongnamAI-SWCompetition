// 점검 요청이 끊겨도 저장한 값은 남고, '저장은 됐어요' 알림이 뜨는지
const { B, ck, browser, page, api, summary } = require('./lib');
const tab = async (p, v) => { await p.evaluate(v=>document.querySelector(`#tabs [data-v="${v}"]`).click(), v); await p.waitForTimeout(900); };
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'cut@example.com',password:'test1234',birth_date:'2009-05-01'});
  const job=(await api(p,'POST','/api/jobs',{name:'QA 가상끊김',wage:9000,start_date:'2026-08-01',schedule:{'월':{start:'17:00',end:'22:30',brk:'없음'}}})).body;
  await p.route('**/agent/**', r=>r.abort());
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(800);
  await tab(p,'pay'); await p.fill('#payMonth','2026-09'); await p.fill('#payAmount','300000');
  const t0=Date.now(); await p.click('#paySave');
  await p.waitForFunction(()=>/저장은 됐어요/.test(document.querySelector('#toast').textContent),null,{timeout:10000}).catch(()=>{});
  const msg=await p.$eval('#toast',e=>e.textContent);
  ck('점검이 끊기면 저장은 됐다고 알림', /저장은 됐어요/.test(msg), msg);
  await p.screenshot({path:'p9_cut.png'});
  const ps=(await api(p,'GET',`/api/jobs/${job.id}/payslips`)).body;
  ck('끊겨도 받은 금액은 저장됨', ps.length===1 && ps[0].amount===300000, JSON.stringify(ps));
  await p.unroute('**/agent/**');
  await p.reload(); await p.waitForSelector('#app:not(.hidden)'); await tab(p,'pay');
  ck('새로고침 뒤에도 목록에 있음', /300,000/.test(await p.$eval('#payslips',e=>e.textContent)));
  ck('오류 없음(끊긴 요청 제외)', !p.errs.length, p.errs.join('|'));
  summary(); await b.close();
})().catch(e=>{console.error('중단',e);process.exit(1);});
