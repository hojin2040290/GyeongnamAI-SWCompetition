// 11부: 그만둔 사업장의 상담 사전 자료 (퇴직일과 지급 기한)
const { B, ck, browser, page, api, summary } = require('./lib');
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'quit@example.com',password:'test1234',birth_date:'2009-05-01'});
  const job=(await api(p,'POST','/api/jobs',{name:'QA 가상퇴직',wage:9000,start_date:'2026-08-01',schedule:{'월':{start:'17:00',end:'22:30',brk:'없음'}}})).body;
  await api(p,'POST',`/api/jobs/${job.id}/quit`,{quit_date:'2026-09-20'});
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(500);
  await p.evaluate(()=>document.querySelector('#tabs [data-v="docs"]').click()); await p.waitForTimeout(800);
  await p.click('#reportBtn');
  await p.waitForURL(/\/api\/reports\//,{timeout:60000}).catch(()=>{});
  const txt=await p.evaluate(()=>document.body.innerText);
  ck('그만둔 사업장 상담 자료 열림', /\/api\/reports\//.test(p.url()), p.url());
  ck('퇴직일과 지급 기한 표시', /퇴직일 2026년 9월 20일 \(일\), 임금 지급 기한 2026년 \d+월 \d+일/.test(txt), (txt.match(/퇴직일[^\n]*/)||[''])[0]);
  await p.screenshot({path:'p11_report.png'});
  ck('오류 없음', !p.errs.length && !p.bad.length, [...p.errs,...p.bad].join('|'));
  summary(); await b.close();
})().catch(e=>{console.error('중단',e);process.exit(1);});
