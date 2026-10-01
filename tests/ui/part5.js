// 5부: 매일 자동 점검을 지금 실행(DEV_TOOLS=true)하면 홈 알림에 결과와 조언이 오는지 (MODE=fake: 가짜 AI)
const { B, ck, browser, page, api, summary } = require('./lib');
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'daily@example.com',password:'test1234',birth_date:'2009-05-01'});
  await api(p,'POST','/api/jobs',{name:'QA 가상분식',wage:10320,start_date:'2026-08-01'});
  const r = await api(p,'POST','/api/dev/daily-check'); ck('매일 점검 지금 실행', r.status===200, JSON.stringify(r.body));
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(1000);
  const al = await p.textContent('#alerts');
  ck('홈 알림에 "오늘 자동 점검"', al.includes('오늘 자동 점검'), al.slice(0,200));
  ck(process.env.MODE==='fake' ? '알림에 에이전트 조언(테스트 답변)' : '알림에 조언 대기 + 도는 표시',
     process.env.MODE==='fake' ? al.includes('에이전트 조언: 테스트 답변입니다') : !!(await p.$('#alerts .wait-note')));
  await p.evaluate(()=>document.querySelector('#alerts').scrollIntoView()); await p.waitForTimeout(300);
  await p.screenshot({path:`p5_${process.env.MODE}.png`});
  ck('오류 없음', !p.errs.length && !p.bad.length, [...p.errs,...p.bad].join('|'));
  summary(); await b.close();
})();
