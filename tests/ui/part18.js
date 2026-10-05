// 18부: AI가 답을 못 낼 때 홈의 자동 종합 점검이 끝없이 다시 돌지 않는지 (실제 모델에서 진행 칸이 '시작'부터 계속 다시 뜨던 문제)
// 서버는 AI를 켠 설정이지만 닿지 않는 주소(LLM_BASE_URL=127.0.0.1:9)라 모든 AI 호출이 실패하고 결과는 'AI 판단 대기'로 남는다
const { B, ck, browser, page, api, summary } = require('./lib');
// 화면에서 시작한 종합 점검만 센다 (첫 단계 '시작 · 사용자 입력'). 서버가 켜지고 20초 뒤의 '다시 맡기기'는 설계대로 뒤에서 한 번 돈다
const runs = p => p.evaluate(async()=>{ const j=(await (await fetch('/api/jobs')).json())[0];
  const rs=await (await fetch(`/api/jobs/${j.id}/agent/runs?events=overview&limit=50`)).json();
  return rs.filter(r=>r.steps[0] && r.steps[0].step==='시작' && r.steps[0].detail==='사용자 입력').length; });
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'loop@example.com',password:'test1234',birth_date:'2009-05-01'});
  await api(p,'POST','/api/jobs',{name:'QA 가상분식',wage:10320,start_date:'2026-08-03',schedule:{}});
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)');
  await p.waitForTimeout(20000);
  const n1 = await runs(p);
  ck('[종합 점검] AI가 답을 못 내도 홈을 열면 자동 점검은 한 번만', n1===1, `20초 동안 ${n1}번`);
  ck('[종합 점검] 결과는 대기 표시 (도는 표시)', /판단 대기|판단 중/.test(await p.textContent('#ovTag')), await p.textContent('#ovTag'));
  await p.click('#ovRun'); await p.waitForTimeout(12000);
  const n2 = await runs(p);
  ck('[종합 점검] 지금 종합 점검은 누른 만큼만 (한 번 더)', n2===2, `누른 뒤 ${n2}번`);
  ck('[종합 점검] 페이지 오류와 5xx 없음', !p.errs.length && !p.bad.length, [...p.errs, ...p.bad].join(' / '));
  await b.close(); summary();
})().catch(e=>{ console.log('중단', e.message); summary(); process.exit(1); });
