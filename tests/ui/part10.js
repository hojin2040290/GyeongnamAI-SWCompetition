// AI가 없던 때 저장된 '대기 중' 계약서 점검: 화면을 열면 다시 점검하는지
const { B, ck, browser, page, api, summary } = require('./lib');
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await api(p,'POST','/api/auth/login',{email:'old@example.com',password:'test1234'});
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(500);
  await p.evaluate(()=>document.querySelector('#tabs [data-v="check"]').click());
  let seen=false; for(let i=0;i<60&&!seen;i++){ await p.waitForTimeout(100); seen=!!(await p.$('#checkTrace .live')); }
  await p.waitForTimeout(1500); await p.screenshot({path:'p10_live.png'});
  ck('예전 대기 결과: 탭을 열면 진행 칸이 보임', seen);
  await p.waitForFunction(()=>!document.querySelector('#checkTrace .live'),null,{timeout:90000}).catch(()=>{});
  const left=await p.$$eval('#checkItems .wait-note',e=>e.length);
  ck('다시 점검 뒤 대기 항목 없음', left===0, `남은 대기 ${left}`);
  await p.screenshot({path:'p10_done.png'});
  await p.evaluate(()=>document.querySelector('#tabs [data-v="pay"]').click()); await p.waitForTimeout(800);
  await p.evaluate(()=>document.querySelector('#tabs [data-v="check"]').click()); await p.waitForTimeout(1500);
  ck('다시 열어도 또 점검하지 않음', !(await p.$('#checkTrace .live')));
  ck('오류 없음', !p.errs.length && !p.bad.length, [...p.errs,...p.bad].join('|'));
  summary(); await b.close();
})().catch(e=>{console.error('중단',e);process.exit(1);});
